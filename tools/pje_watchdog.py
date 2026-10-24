#!/usr/bin/env python3
"""Watchdog da VPS de produção do pje-download (TJES).

Roda NA VPS, como comando forçado de uma chave SSH restrita (ver
``docs/ops/hermes-watchdog.md``). O Hermes, no Mac mini, executa
``ssh deploy@pje-vps`` a cada poucos minutos e envia a saída ao Telegram.

Contrato com o Hermes (cron sem LLM):
  * saída vazia  -> nada a avisar (tick silencioso);
  * saída com texto -> enviada como está;
  * código de saída diferente de zero -> o Hermes envia um alerta de erro.
    É também o que acontece quando o ``ssh`` falha: VPS fora do ar vira alerta.

Somente leitura: health do worker e do dashboard, estado dos contêineres,
tamanho das filas do Redis e o histórico de lotes. Só biblioteca padrão, nenhum
``shell``, e nada do que vem do cliente SSH é executado: ``SSH_ORIGINAL_COMMAND``
é apenas comparado com a palavra ``digest``.

Privacidade: a saída vai para o Telegram. Nunca contém número de processo,
conteúdo de payload nem credenciais; erros de lotes passam por ``scrub``.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, NamedTuple

ALERTA = "ALERTA"
AVISO = "AVISO"

JOBS_KEY = "kratos:pje:jobs"
PROCESSING_KEY = "kratos:pje:jobs:processing"
DEAD_LETTER_KEY = "kratos:pje:dead-letter"

# O teste de fila do deploy injeta um payload inválido de propósito (um por
# deploy), então esse motivo não é sinal de problema.
IGNORED_DEAD_LETTER_REASONS = frozenset({"invalid_json"})

MAX_SEEN_BATCHES = 200
MAX_DEAD_LETTER_SCAN = 100

_CASE_NUMBER = re.compile(r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}")
_CPF_SHAPE = re.compile(r"\d{3}\.\d{3}\.\d{3}-\d{2}")
_LONG_DIGITS = re.compile(r"\d{6,}")
_REASON = re.compile(r"^[a-z_]{1,40}$")


class WatchdogError(Exception):
    """Falha da própria checagem (não do sistema observado)."""


class HttpResult(NamedTuple):
    status: int | None
    body: Any
    error: str | None


class RunResult(NamedTuple):
    code: int
    out: str


@dataclass(frozen=True)
class Finding:
    key: str
    severity: str
    text: str
    # "state": persiste até sumir (avisa na confirmação, lembra, avisa a volta).
    # "event": acontece uma vez (avisa sempre que aparece, nunca "normaliza").
    kind: str = "state"
    # Execuções consecutivas necessárias antes de anunciar um "state".
    confirm: int = 2


@dataclass
class Config:
    pje_dir: str = "/opt/pje-download"
    worker_url: str = "http://127.0.0.1:8006/health"
    dashboard_url: str = "http://127.0.0.1:8007/healthz"
    history_url: str = "http://127.0.0.1:8007/api/history"
    redis_container: str = "pje-redis"
    containers: tuple[str, ...] = ("pje-redis", "pje-dashboard", "pje-worker")
    remind_hours: float = 6.0
    stuck_minutes: float = 15.0
    disk_warn_mb: float = 5120.0
    digest_hours: float = 24.0
    state_path: str = field(
        default_factory=lambda: os.path.expanduser("~/.cache/pje-watchdog/state.json")
    )

    @classmethod
    def from_environ(cls, environ) -> Config:
        cfg = cls()
        if environ.get("WATCHDOG_STATE"):
            cfg.state_path = environ["WATCHDOG_STATE"]
        if environ.get("WATCHDOG_PJE_DIR"):
            cfg.pje_dir = environ["WATCHDOG_PJE_DIR"]
        for var, attr in (
            ("WATCHDOG_WORKER_URL", "worker_url"),
            ("WATCHDOG_DASHBOARD_URL", "dashboard_url"),
            ("WATCHDOG_HISTORY_URL", "history_url"),
        ):
            if environ.get(var):
                setattr(cfg, attr, environ[var])
        for var, attr in (
            ("WATCHDOG_REMIND_HOURS", "remind_hours"),
            ("WATCHDOG_STUCK_MINUTES", "stuck_minutes"),
        ):
            raw = environ.get(var)
            if raw:
                try:
                    setattr(cfg, attr, float(raw))
                except ValueError:
                    pass
        return cfg


# ───────────────────────── ambiente (efeitos colaterais) ─────────────────────────


class SystemEnv:
    """Tudo que toca o mundo real; os testes trocam isto por um ``FakeEnv``."""

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def now(self) -> float:
        return datetime.now(timezone.utc).timestamp()

    def http_get_json(self, url, headers=None, timeout=5.0) -> HttpResult:
        req = urllib.request.Request(url, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                return HttpResult(resp.status, _parse_json(resp.read()), None)
        except urllib.error.HTTPError as exc:
            try:
                body = _parse_json(exc.read())
            except Exception:
                body = None
            return HttpResult(exc.code, body, None)
        except Exception as exc:  # rede, timeout, conexão recusada
            return HttpResult(None, None, type(exc).__name__)

    def run(self, argv, extra_env=None, timeout=15.0) -> RunResult:
        env = dict(os.environ)
        if extra_env:
            env.update(extra_env)
        try:
            proc = subprocess.run(  # noqa: S603 - argv fixo, sem shell
                list(argv),
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise WatchdogError(f"{argv[0]}: {type(exc).__name__}") from exc
        return RunResult(proc.returncode, proc.stdout)

    def read_dotenv(self) -> dict[str, str]:
        path = os.path.join(self.cfg.pje_dir, ".env")
        values: dict[str, str] = {}
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    values[key.strip()] = value.strip().strip("'\"")
        except OSError:
            return {}
        return values

    def free_mb(self, path: str) -> float:
        return shutil.disk_usage(path).free / 1_048_576


def _parse_json(raw: bytes | str):
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    return json.loads(raw) if raw.strip() else None


# ───────────────────────── utilidades ─────────────────────────


def scrub(text, limit: int = 120) -> str:
    """Tira de um texto livre o que não deve ir para o Telegram.

    Números de processo, CPF e qualquer sequência longa de dígitos viram
    marcadores; o resultado é cortado em ``limit`` caracteres.
    """
    if not text:
        return ""
    out = _CASE_NUMBER.sub("[processo]", str(text))
    out = _CPF_SHAPE.sub("[cpf]", out)
    out = _LONG_DIGITS.sub("[#]", out)
    out = " ".join(out.split())
    return out[:limit]


def fmt_ts(ts: float) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    try:
        from zoneinfo import ZoneInfo

        return dt.astimezone(ZoneInfo("America/Sao_Paulo")).strftime("%d/%m %H:%M")
    except Exception:
        return dt.strftime("%d/%m %H:%M UTC")


def fmt_duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{max(minutes, 1)} min"
    hours, rest = divmod(minutes, 60)
    if hours < 48:
        return f"{hours} h {rest:02d} min"
    return f"{hours // 24} d"


def _redis(env, cfg: Config, dotenv: dict, *args: str) -> str:
    password = dotenv.get("REDIS_PASSWORD", "")
    if not password:
        raise WatchdogError("REDIS_PASSWORD ausente no .env")
    # A senha vai por variável de ambiente do cliente docker (-e VAR sem valor),
    # nunca pelo argv, que aparece em `ps`.
    argv = [
        "docker",
        "exec",
        "-e",
        "REDISCLI_AUTH",
        cfg.redis_container,
        "redis-cli",
        "--raw",
        *args,
    ]
    res = env.run(argv, extra_env={"REDISCLI_AUTH": password})
    if res.code != 0:
        raise WatchdogError(f"redis-cli saiu com {res.code}")
    return res.out.strip()


def _api_headers(dotenv: dict) -> dict | None:
    key = dotenv.get("DASHBOARD_API_KEY", "")
    return {"X-API-Key": key} if key else None


def _llen(env, cfg, dotenv, key: str) -> int:
    try:
        return int(_redis(env, cfg, dotenv, "LLEN", key))
    except ValueError as exc:
        raise WatchdogError("resposta inesperada do Redis") from exc


# ───────────────────────── checagens ─────────────────────────


def check_worker(env, cfg: Config) -> list[Finding]:
    res = env.http_get_json(cfg.worker_url)
    if res.status is None:
        return [Finding("worker:down", ALERTA, "Worker não responde em :8006")]
    body = res.body
    if not isinstance(body, dict):
        return [
            Finding(
                "worker:bad-response",
                AVISO,
                f"Worker respondeu HTTP {res.status} sem JSON válido",
            )
        ]

    findings: list[Finding] = []
    status = body.get("status")
    checks = body.get("checks") if isinstance(body.get("checks"), dict) else {}

    # "processing" responde 503/healthy=false enquanto um job roda. Não é falha.
    if status == "redis_unreachable":
        findings.append(
            Finding(
                "worker:redis_unreachable",
                ALERTA,
                "Worker: Redis inacessível (circuit breaker aberto)",
            )
        )
    elif status == "session_expired":
        findings.append(
            Finding("worker:session_expired", ALERTA, "Worker: sessão expirada")
        )
    elif status == "captcha_required":
        findings.append(
            Finding("worker:captcha_required", ALERTA, "Worker: PJe pediu captcha")
        )

    if checks.get("redis") == "unreachable" and status != "redis_unreachable":
        findings.append(Finding("worker:redis", ALERTA, "Worker: Redis inacessível"))

    free = checks.get("disk_free_mb")
    if checks.get("disk") == "low":
        findings.append(
            Finding("worker:disk", ALERTA, f"Disco do worker baixo: {free} MB livres")
        )
    elif isinstance(free, (int, float)) and free < cfg.disk_warn_mb:
        findings.append(
            Finding("worker:disk", AVISO, f"Disco do worker com {free:.0f} MB livres")
        )

    mni = checks.get("mni")
    if mni not in (None, "healthy", "disabled"):
        findings.append(Finding("worker:mni", AVISO, f"MNI do TJES: {scrub(mni, 40)}"))
    return findings


def check_dashboard(env, cfg: Config) -> list[Finding]:
    res = env.http_get_json(cfg.dashboard_url)
    if res.status is None:
        return [Finding("dashboard:down", ALERTA, "Dashboard não responde em :8007")]
    body = res.body
    if not isinstance(body, dict):
        return [
            Finding(
                "dashboard:bad-response",
                AVISO,
                f"Dashboard respondeu HTTP {res.status} sem JSON válido",
            )
        ]
    findings: list[Finding] = []
    checks = body.get("checks") if isinstance(body.get("checks"), dict) else {}
    if checks.get("redis") == "unreachable":
        findings.append(
            Finding("dashboard:redis", ALERTA, "Dashboard: Redis inacessível")
        )
    if checks.get("active_batch_resume_pending"):
        findings.append(
            Finding(
                "dashboard:resume-pending",
                AVISO,
                "Dashboard: há um lote aguardando retomada",
            )
        )
    return findings


_INSPECT_FORMAT = (
    "{{.Id}} {{.State.Status}} "
    "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} "
    "{{.RestartCount}}"
)


def check_containers(env, cfg: Config, state: dict) -> list[Finding]:
    findings: list[Finding] = []
    known = state.setdefault("containers", {})
    for name in cfg.containers:
        res = env.run(["docker", "inspect", "--format", _INSPECT_FORMAT, name])
        parts = res.out.split()
        if res.code != 0 or len(parts) != 4:
            findings.append(
                Finding(
                    f"container:{name}:missing", ALERTA, f"Contêiner {name} não existe"
                )
            )
            continue
        cid, status, health, restarts_raw = parts
        try:
            restarts = int(restarts_raw)
        except ValueError:
            restarts = 0
        if status != "running":
            findings.append(
                Finding(
                    f"container:{name}:down", ALERTA, f"Contêiner {name} está {status}"
                )
            )
        if health == "unhealthy":
            findings.append(
                Finding(
                    f"container:{name}:unhealthy",
                    AVISO,
                    f"Contêiner {name} marcado unhealthy (pode ser um job longo)",
                    confirm=3,
                )
            )
        previous = known.get(name)
        if (
            previous
            and previous.get("id") == cid
            and restarts > previous.get("restarts", 0)
        ):
            delta = restarts - previous["restarts"]
            findings.append(
                Finding(
                    f"container:{name}:restarts:{int(env.now())}",
                    ALERTA,
                    f"Contêiner {name} reiniciou {delta} vez(es) desde a última checagem",
                    kind="event",
                )
            )
        # Recriado pelo deploy (id novo): o contador volta a zero, sem alerta.
        known[name] = {"id": cid, "restarts": restarts}
    return findings


def _unchanged_for(entry: dict | None, n: int, now: float) -> tuple[dict, float]:
    if entry is None or entry.get("n") != n:
        entry = {"n": n, "changed": now}
    return entry, now - entry["changed"]


def check_queue(env, cfg: Config, state: dict) -> list[Finding]:
    dotenv = env.read_dotenv()
    now = env.now()
    q = state.setdefault("queue", {})
    findings: list[Finding] = []

    for name, key in (("jobs", JOBS_KEY), ("processing", PROCESSING_KEY)):
        n = _llen(env, cfg, dotenv, key)
        q[name], idle = _unchanged_for(q.get(name), n, now)
        if n > 0 and idle >= cfg.stuck_minutes * 60:
            findings.append(
                Finding(
                    f"queue:{name}-stuck",
                    AVISO,
                    f"Fila '{name}' com {n} item(ns) sem mudar há {fmt_duration(idle)}",
                    confirm=1,
                )
            )

    dead = _llen(env, cfg, dotenv, DEAD_LETTER_KEY)
    baseline = q.get("dead_len")
    if baseline is None or dead < baseline:
        q["dead_len"] = dead
        return findings
    if dead > baseline:
        fresh = min(dead - baseline, MAX_DEAD_LETTER_SCAN)
        raw = _redis(env, cfg, dotenv, "LRANGE", DEAD_LETTER_KEY, "0", str(fresh - 1))
        reasons: dict[str, int] = {}
        for line in raw.splitlines():
            try:
                reason = json.loads(line).get("reason", "")
            except (ValueError, AttributeError):
                reason = ""
            reason = reason if _REASON.match(str(reason)) else "desconhecido"
            reasons[reason] = reasons.get(reason, 0) + 1
        q["dead_len"] = dead
        for reason, count in sorted(reasons.items()):
            if reason in IGNORED_DEAD_LETTER_REASONS:
                continue
            findings.append(
                Finding(
                    f"queue:dead-letter:{reason}:{int(now)}",
                    ALERTA,
                    f"Dead-letter: {count} item(ns) novo(s), motivo {reason}",
                    kind="event",
                )
            )
    return findings


def check_batches(env, cfg: Config, state: dict) -> list[Finding]:
    dotenv = env.read_dotenv()
    res = env.http_get_json(cfg.history_url, headers=_api_headers(dotenv))
    if res.status != 200 or not isinstance(res.body, list):
        return [
            Finding(
                "batches:history-unavailable",
                AVISO,
                f"Histórico de lotes indisponível (HTTP {res.status or 'sem resposta'})",
            )
        ]

    first_run = "seen_batches" not in state
    seen = set(state.get("seen_batches", []))
    findings: list[Finding] = []
    for item in res.body[:50]:
        if not isinstance(item, dict):
            continue
        bid = str(item.get("batch_id", ""))
        status = item.get("status")
        if not bid or bid in seen or status in ("queued", "running", None):
            continue
        seen.add(bid)
        if first_run or status not in ("failed", "partial"):
            continue
        detail = scrub(item.get("error"))
        findings.append(
            Finding(
                f"batch:{bid}",
                ALERTA if status == "failed" else AVISO,
                f"Lote {bid}: {status} ({item.get('processos', '?')} processos, "
                f"{item.get('total_docs', 0)} docs)"
                + (f". {detail}" if detail else ""),
                kind="event",
            )
        )
    state["seen_batches"] = sorted(seen)[-MAX_SEEN_BATCHES:]
    return findings


def check_host(env, cfg: Config) -> list[Finding]:
    free = env.free_mb("/")
    if free < cfg.disk_warn_mb:
        return [
            Finding("host:disk", AVISO, f"Disco da VPS com {free / 1024:.1f} GB livres")
        ]
    return []


# ───────────────────────── reconciliação e execução ─────────────────────────

# (nome da checagem, prefixo das chaves que ela produz). O prefixo permite manter
# um problema ativo quando a PRÓPRIA checagem falha, em vez de dar como resolvido.
CHECKS = ("worker", "dashboard", "containers", "queue", "batches", "host")
_PREFIX = {
    "worker": "worker:",
    "dashboard": "dashboard:",
    "containers": "container:",
    "queue": "queue:",
    "batches": "batch",
    "host": "host:",
}


def reconcile(
    state: dict,
    findings: list[Finding],
    now: float,
    cfg: Config,
    skip_prefixes: tuple[str, ...] = (),
) -> list[str]:
    active = state.setdefault("active", {})
    pending = state.setdefault("pending", {})
    lines: list[str] = []

    for f in findings:
        if f.kind == "event":
            lines.append(f"[{f.severity}] {f.text}")

    current = {f.key: f for f in findings if f.kind == "state"}
    for key in list(pending):
        if key not in current:
            del pending[key]

    for key, f in current.items():
        if key in active:
            rec = active[key]
            rec["text"], rec["severity"] = f.text, f.severity
            if now - rec["notified"] >= cfg.remind_hours * 3600:
                lines.append(f"[LEMBRETE] {f.text} (desde {fmt_ts(rec['since'])})")
                rec["notified"] = now
            continue
        pending[key] = pending.get(key, 0) + 1
        if pending[key] >= f.confirm:
            del pending[key]
            active[key] = {
                "since": now,
                "notified": now,
                "severity": f.severity,
                "text": f.text,
            }
            lines.append(f"[{f.severity}] {f.text}")

    for key in list(active):
        if key in current or key.startswith(skip_prefixes):
            continue
        rec = active.pop(key)
        lines.append(
            f"[NORMALIZOU] {rec['text']} (durou {fmt_duration(now - rec['since'])})"
        )
    return lines


def load_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(path: str, state: dict) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, mode=0o700, exist_ok=True)
    tmp = f"{path}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=1, sort_keys=True)
    os.replace(tmp, path)


def collect(env, cfg: Config, state: dict) -> tuple[list[Finding], tuple[str, ...]]:
    """Roda todas as checagens. Uma que quebra vira um aviso próprio."""
    runners = {
        "worker": lambda: check_worker(env, cfg),
        "dashboard": lambda: check_dashboard(env, cfg),
        "containers": lambda: check_containers(env, cfg, state),
        "queue": lambda: check_queue(env, cfg, state),
        "batches": lambda: check_batches(env, cfg, state),
        "host": lambda: check_host(env, cfg),
    }
    findings: list[Finding] = []
    failed: list[str] = []
    for name in CHECKS:
        try:
            findings.extend(runners[name]())
        except Exception as exc:  # nunca derrubar o ciclo por uma checagem
            failed.append(_PREFIX[name])
            findings.append(
                Finding(
                    f"check-error:{name}",
                    AVISO,
                    f"A checagem '{name}' falhou ({type(exc).__name__})",
                )
            )
    return findings, tuple(failed)


def _header(env) -> str:
    return f"PJe/TJES (VPS) · {fmt_ts(env.now())}"


def run_check(env, cfg: Config) -> list[str]:
    os.makedirs(os.path.dirname(cfg.state_path) or ".", mode=0o700, exist_ok=True)
    lock_fd = os.open(cfg.state_path + ".lock", os.O_WRONLY | os.O_CREAT, 0o600)
    lock = os.fdopen(lock_fd, "w")  # vive até o fim da execução
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return []  # outra execução em andamento
        state = load_state(cfg.state_path)
        findings, failed = collect(env, cfg, state)
        lines = reconcile(state, findings, env.now(), cfg, skip_prefixes=failed)
        save_state(cfg.state_path, state)
        return [_header(env), *lines] if lines else []
    finally:
        lock.close()


def run_digest(env, cfg: Config) -> list[str]:
    """Resumo diário. Não altera o estado do modo ``check``."""
    state = load_state(cfg.state_path)
    # Cópia: as checagens atualizam linhas de base, que o digest não pode consumir.
    scratch = json.loads(json.dumps(state))
    findings, _ = collect(env, cfg, scratch)

    lines = [f"{_header(env)} · resumo diário"]

    problems = [f for f in findings if f.kind == "state" or f.severity == ALERTA]
    health = env.http_get_json(cfg.worker_url)
    body = health.body if isinstance(health.body, dict) else {}
    checks = body.get("checks") if isinstance(body.get("checks"), dict) else {}
    uptime = body.get("uptime_minutes")
    lines.append(
        "Worker: "
        + (str(body.get("status", "sem resposta")))
        + f" · build {str(body.get('build_sha', '?'))[:7]}"
        + (
            f" · no ar há {fmt_duration(uptime * 60)}"
            if isinstance(uptime, (int, float))
            else ""
        )
        + (
            f" · disco {checks['disk_free_mb'] / 1024:.0f} GB livres"
            if isinstance(checks.get("disk_free_mb"), (int, float))
            else ""
        )
    )

    since = datetime.fromtimestamp(env.now(), tz=timezone.utc) - timedelta(
        hours=cfg.digest_hours
    )
    counts: dict[str, int] = {}
    processos = docs = 0
    hist = env.http_get_json(cfg.history_url, headers=_api_headers(env.read_dotenv()))
    if hist.status == 200 and isinstance(hist.body, list):
        for item in hist.body:
            created = (
                _parse_dt(item.get("created_at")) if isinstance(item, dict) else None
            )
            if created is None or created < since:
                continue
            counts[item.get("status", "?")] = counts.get(item.get("status", "?"), 0) + 1
            processos += int(item.get("processos") or 0)
            docs += int(item.get("total_docs") or 0)
        by_status = ", ".join(f"{s} {n}" for s, n in sorted(counts.items())) or "nenhum"
        lines.append(
            f"Lotes nas últimas {cfg.digest_hours:.0f} h: {by_status} · "
            f"{processos} processos, {docs} docs"
        )
    else:
        lines.append("Lotes: histórico indisponível")

    q = scratch.get("queue", {})
    dead_now = q.get("dead_len")
    if dead_now is not None:
        delta = dead_now - state.get("queue", {}).get("dead_len", dead_now)
        lines.append(
            f"Dead-letter: {dead_now} (novos desde a última checagem: {max(delta, 0)})"
        )

    if problems:
        lines.append("Problemas agora:")
        lines.extend(f"  [{f.severity}] {f.text}" for f in problems)
    else:
        lines.append("Problemas agora: nenhum")
    return lines


def _parse_dt(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def main(argv=None, environ=None, env=None) -> int:
    environ = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(
        description="Watchdog read-only da VPS do pje-download"
    )
    parser.add_argument("--mode", choices=("check", "digest"))
    args = parser.parse_args(argv)

    # Único uso do comando enviado pelo cliente SSH: comparar com uma palavra.
    # Qualquer outro texto cai no modo padrão e nunca é executado.
    mode = args.mode or (
        "digest"
        if environ.get("SSH_ORIGINAL_COMMAND", "").strip() == "digest"
        else "check"
    )
    cfg = Config.from_environ(environ)
    env = env or SystemEnv(cfg)
    try:
        lines = run_digest(env, cfg) if mode == "digest" else run_check(env, cfg)
    except Exception as exc:
        print(f"WATCHDOG: erro interno ({type(exc).__name__})")
        return 1
    if lines:
        print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
