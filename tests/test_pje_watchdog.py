"""Watchdog da VPS de produção (tools/pje_watchdog.py).

O watchdog roda NA VPS, como comando forçado da chave SSH do Hermes (Mac mini).
Ele só lê: health do worker e do dashboard, estado dos contêineres, filas do
Redis e o histórico de lotes. Imprime apenas o que mudou; saída vazia = nada a
avisar (o Hermes não envia mensagem para saída vazia).

Os testes usam um ``FakeEnv`` no lugar de rede, Docker e relógio.
"""

from __future__ import annotations

import ast
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools import pje_watchdog as wd

ROOT = Path(__file__).resolve().parent.parent


def cnj_like() -> str:
    """Número no formato CNJ montado em tempo de execução (nunca literal)."""
    return "-".join(["5000001", "00.2024.8.08.0001"])


class FakeEnv:
    def __init__(self):
        self.t = 1_000_000.0
        self.http: dict[str, wd.HttpResult] = {}
        self.http_calls: list[tuple[str, dict | None]] = []
        self.containers: dict[str, str] = {}
        self.llen = {
            wd.JOBS_KEY: 0,
            wd.PROCESSING_KEY: 0,
            wd.DEAD_LETTER_KEY: 0,
        }
        self.dead_entries: list[str] = []  # índice 0 = mais novo (LPUSH)
        self.dotenv = {"REDIS_PASSWORD": "pw-secreta", "DASHBOARD_API_KEY": "chave-api"}
        self.free = 50_000.0
        self.argv_log: list[list[str]] = []
        self.extra_env_log: list[dict | None] = []

    def now(self):
        return self.t

    def http_get_json(self, url, headers=None, timeout=5.0):
        self.http_calls.append((url, headers))
        return self.http.get(url, wd.HttpResult(None, None, "ConnectionRefusedError"))

    def run(self, argv, extra_env=None, timeout=15.0):
        self.argv_log.append(list(argv))
        self.extra_env_log.append(extra_env)
        if argv[:2] == ["docker", "inspect"]:
            name = argv[-1]
            if name in self.containers:
                return wd.RunResult(0, self.containers[name] + "\n")
            return wd.RunResult(1, "")
        if "redis-cli" in argv:
            cmd = argv[argv.index("--raw") + 1 :]
            if cmd[0] == "LLEN":
                return wd.RunResult(0, f"{self.llen[cmd[1]]}\n")
            if cmd[0] == "LRANGE":
                lo, hi = int(cmd[2]), int(cmd[3])
                return wd.RunResult(0, "\n".join(self.dead_entries[lo : hi + 1]) + "\n")
        return wd.RunResult(127, "")

    def read_dotenv(self):
        return dict(self.dotenv)

    def free_mb(self, path):
        return self.free


def healthy_worker(**over):
    body = {
        "service": "pje-worker",
        "status": "ready",
        "healthy": True,
        "build_sha": "6605fd8",
        "checks": {
            "mni": "healthy",
            "redis": "healthy",
            "disk_free_mb": 89000.0,
            "disk": "ok",
        },
        "uptime_minutes": 100.0,
    }
    body.update(over)
    return body


def healthy_dashboard(**over):
    body = {
        "service": "pje-dashboard",
        "ready": True,
        "build_sha": "6605fd8",
        "current_batch": None,
        "checks": {
            "redis": "healthy",
            "active_batch_recovered": False,
            "active_batch_resume_pending": False,
        },
    }
    body.update(over)
    return body


@pytest.fixture
def cfg():
    return wd.Config()


@pytest.fixture
def env():
    e = FakeEnv()
    e.http[wd.Config().worker_url] = wd.HttpResult(200, healthy_worker(), None)
    e.http[wd.Config().dashboard_url] = wd.HttpResult(200, healthy_dashboard(), None)
    e.http[wd.Config().history_url] = wd.HttpResult(200, [], None)
    for name in wd.Config().containers:
        e.containers[name] = f"id-{name} running healthy 0"
    return e


# ───────────────────────── scrub ─────────────────────────


class TestScrub:
    def test_masks_formatted_case_numbers(self):
        out = wd.scrub(f"falhou em {cnj_like()} por timeout")
        assert cnj_like() not in out
        assert "5000001" not in out
        # Nenhum fragmento do número (ano, tribunal, origem) pode sobrar.
        assert "2024" not in out and "0001" not in out
        assert "[processo]" in out

    def test_masks_long_digit_runs_and_cpf_shape(self):
        cpf_like = ".".join(["123", "456", "789"]) + "-09"
        out = wd.scrub(f"id 12345678901234567890 e {cpf_like}")
        assert "12345678901234567890" not in out
        assert "456" not in out

    def test_truncates(self):
        assert len(wd.scrub("x" * 500, limit=50)) <= 50

    def test_none_is_empty(self):
        assert wd.scrub(None) == ""


# ───────────────────────── worker ─────────────────────────


class TestCheckWorker:
    def test_healthy_has_no_findings(self, env, cfg):
        assert wd.check_worker(env, cfg) == []

    def test_unreachable_is_an_alert(self, env, cfg):
        env.http[cfg.worker_url] = wd.HttpResult(None, None, "URLError")
        (f,) = wd.check_worker(env, cfg)
        assert f.key == "worker:down" and f.severity == wd.ALERTA

    def test_busy_worker_is_not_a_problem(self, env, cfg):
        """O worker responde 503/healthy=false enquanto processa um job
        (status "processing"). Isso não é falha."""
        env.http[cfg.worker_url] = wd.HttpResult(
            503, healthy_worker(status="processing", healthy=False), None
        )
        assert wd.check_worker(env, cfg) == []

    @pytest.mark.parametrize(
        "status,key",
        [
            ("redis_unreachable", "worker:redis_unreachable"),
            ("session_expired", "worker:session_expired"),
            ("captcha_required", "worker:captcha_required"),
        ],
    )
    def test_fatal_statuses_alert(self, env, cfg, status, key):
        env.http[cfg.worker_url] = wd.HttpResult(
            503, healthy_worker(status=status, healthy=False), None
        )
        keys = [f.key for f in wd.check_worker(env, cfg)]
        assert key in keys

    def test_low_disk_is_an_alert_and_shows_free_space(self, env, cfg):
        body = healthy_worker(healthy=False)
        body["checks"]["disk"] = "low"
        body["checks"]["disk_free_mb"] = 800.0
        env.http[cfg.worker_url] = wd.HttpResult(503, body, None)
        f = next(x for x in wd.check_worker(env, cfg) if x.key == "worker:disk")
        assert f.severity == wd.ALERTA and "800" in f.text

    def test_disk_below_warning_threshold_is_a_warning(self, env, cfg):
        body = healthy_worker()
        body["checks"]["disk_free_mb"] = cfg.disk_warn_mb - 1
        env.http[cfg.worker_url] = wd.HttpResult(200, body, None)
        f = next(x for x in wd.check_worker(env, cfg) if x.key == "worker:disk")
        assert f.severity == wd.AVISO

    def test_mni_unreachable_is_a_warning(self, env, cfg):
        body = healthy_worker()
        body["checks"]["mni"] = "unreachable"
        env.http[cfg.worker_url] = wd.HttpResult(200, body, None)
        f = next(x for x in wd.check_worker(env, cfg) if x.key == "worker:mni")
        assert f.severity == wd.AVISO

    def test_mni_disabled_is_fine(self, env, cfg):
        body = healthy_worker()
        body["checks"]["mni"] = "disabled"
        env.http[cfg.worker_url] = wd.HttpResult(200, body, None)
        assert wd.check_worker(env, cfg) == []

    def test_unparseable_body_is_a_warning_not_a_crash(self, env, cfg):
        env.http[cfg.worker_url] = wd.HttpResult(200, "not json", None)
        keys = [f.key for f in wd.check_worker(env, cfg)]
        assert keys == ["worker:bad-response"]


# ───────────────────────── dashboard ─────────────────────────


class TestCheckDashboard:
    def test_healthy(self, env, cfg):
        assert wd.check_dashboard(env, cfg) == []

    def test_unreachable(self, env, cfg):
        env.http[cfg.dashboard_url] = wd.HttpResult(None, None, "URLError")
        (f,) = wd.check_dashboard(env, cfg)
        assert f.key == "dashboard:down" and f.severity == wd.ALERTA

    def test_redis_unreachable(self, env, cfg):
        body = healthy_dashboard(ready=False)
        body["checks"]["redis"] = "unreachable"
        env.http[cfg.dashboard_url] = wd.HttpResult(503, body, None)
        assert "dashboard:redis" in [f.key for f in wd.check_dashboard(env, cfg)]

    def test_resume_pending_is_a_warning(self, env, cfg):
        body = healthy_dashboard(ready=False)
        body["checks"]["active_batch_resume_pending"] = True
        env.http[cfg.dashboard_url] = wd.HttpResult(503, body, None)
        (f,) = wd.check_dashboard(env, cfg)
        assert f.key == "dashboard:resume-pending" and f.severity == wd.AVISO


# ───────────────────────── containers ─────────────────────────


class TestCheckContainers:
    def test_all_running(self, env, cfg):
        assert wd.check_containers(env, cfg, {}) == []

    def test_stopped_container_alerts(self, env, cfg):
        env.containers["pje-worker"] = "id-w exited none 0"
        f = next(
            x
            for x in wd.check_containers(env, cfg, {})
            if x.key == "container:pje-worker:down"
        )
        assert f.severity == wd.ALERTA

    def test_missing_container_alerts(self, env, cfg):
        del env.containers["pje-redis"]
        keys = [f.key for f in wd.check_containers(env, cfg, {})]
        assert "container:pje-redis:missing" in keys

    def test_unhealthy_is_a_slow_warning(self, env, cfg):
        env.containers["pje-worker"] = "id-w running unhealthy 0"
        f = next(
            x
            for x in wd.check_containers(env, cfg, {})
            if x.key == "container:pje-worker:unhealthy"
        )
        assert f.severity == wd.AVISO and f.confirm >= 3

    def test_restart_counter_increase_alerts_once(self, env, cfg):
        state: dict = {}
        env.containers["pje-worker"] = "id-w running healthy 0"
        wd.check_containers(env, cfg, state)
        env.containers["pje-worker"] = "id-w running healthy 3"
        events = [f for f in wd.check_containers(env, cfg, state) if f.kind == "event"]
        assert len(events) == 1 and "3" in events[0].text
        assert [
            f for f in wd.check_containers(env, cfg, state) if f.kind == "event"
        ] == []

    def test_recreated_container_resets_the_baseline(self, env, cfg):
        """Deploy recria o contêiner (id novo, contador volta a 0): sem alerta."""
        state: dict = {}
        env.containers["pje-worker"] = "id-old running healthy 5"
        wd.check_containers(env, cfg, state)
        env.containers["pje-worker"] = "id-new running healthy 0"
        assert [
            f for f in wd.check_containers(env, cfg, state) if f.kind == "event"
        ] == []

    def test_recreated_container_that_already_restarted_alerts_on_the_next_run(
        self, env, cfg
    ):
        """Contêiner novo que já nasceu em crash-loop: a 1ª leitura só vira
        linha de base; o aumento seguinte alerta."""
        state: dict = {}
        env.containers["pje-worker"] = "id-old running healthy 0"
        wd.check_containers(env, cfg, state)
        env.containers["pje-worker"] = "id-new running healthy 4"
        assert [
            f for f in wd.check_containers(env, cfg, state) if f.kind == "event"
        ] == []
        env.containers["pje-worker"] = "id-new running healthy 6"
        events = [f for f in wd.check_containers(env, cfg, state) if f.kind == "event"]
        assert len(events) == 1 and "2" in events[0].text


# ───────────────────────── fila ─────────────────────────


def dead_entry(reason: str) -> str:
    return json.dumps(
        {"reason": reason, "payload": "x", "details": {}, "timestamp": "2026-10-24"},
        ensure_ascii=False,
    )


class TestCheckQueue:
    def test_first_run_sets_baseline_without_alerting(self, env, cfg):
        env.llen[wd.DEAD_LETTER_KEY] = 38
        state: dict = {}
        assert wd.check_queue(env, cfg, state) == []
        assert state["queue"]["dead_len"] == 38

    def test_redis_password_goes_by_environment_never_argv(self, env, cfg):
        wd.check_queue(env, cfg, {})
        assert env.argv_log, "esperava chamadas ao redis-cli"
        for argv in env.argv_log:
            assert "pw-secreta" not in " ".join(argv)
        assert any(
            e and e.get("REDISCLI_AUTH") == "pw-secreta" for e in env.extra_env_log
        )
        assert any(
            a[:3] == ["docker", "exec", "-e"] and "REDISCLI_AUTH" in a
            for a in env.argv_log
        )

    def test_smoke_test_dead_letters_are_ignored(self, env, cfg):
        state: dict = {}
        env.llen[wd.DEAD_LETTER_KEY] = 38
        wd.check_queue(env, cfg, state)
        env.llen[wd.DEAD_LETTER_KEY] = 39
        env.dead_entries = [dead_entry("invalid_json")]
        assert wd.check_queue(env, cfg, state) == []

    def test_real_dead_letter_reason_alerts_without_payload(self, env, cfg):
        state: dict = {}
        env.llen[wd.DEAD_LETTER_KEY] = 38
        wd.check_queue(env, cfg, state)
        env.llen[wd.DEAD_LETTER_KEY] = 40
        env.dead_entries = [
            dead_entry("redelivery_exhausted"),
            dead_entry("invalid_json"),
        ]
        (f,) = wd.check_queue(env, cfg, state)
        assert f.kind == "event" and f.severity == wd.ALERTA
        assert "redelivery_exhausted" in f.text
        assert "payload" not in f.text.lower()

    def test_dead_letter_shrinking_resets_the_baseline(self, env, cfg):
        state: dict = {}
        env.llen[wd.DEAD_LETTER_KEY] = 38
        wd.check_queue(env, cfg, state)
        env.llen[wd.DEAD_LETTER_KEY] = 0
        assert wd.check_queue(env, cfg, state) == []
        assert state["queue"]["dead_len"] == 0

    def test_jobs_that_do_not_move_for_too_long_warn(self, env, cfg):
        state: dict = {}
        env.llen[wd.JOBS_KEY] = 5
        assert wd.check_queue(env, cfg, state) == []
        env.t += cfg.stuck_minutes * 60 + 1
        (f,) = wd.check_queue(env, cfg, state)
        assert f.key == "queue:jobs-stuck" and f.severity == wd.AVISO

    def test_jobs_that_keep_draining_do_not_warn(self, env, cfg):
        state: dict = {}
        env.llen[wd.JOBS_KEY] = 5
        wd.check_queue(env, cfg, state)
        env.t += cfg.stuck_minutes * 60 + 1
        env.llen[wd.JOBS_KEY] = 3
        assert wd.check_queue(env, cfg, state) == []

    def test_processing_item_stuck_warns(self, env, cfg):
        state: dict = {}
        env.llen[wd.PROCESSING_KEY] = 1
        wd.check_queue(env, cfg, state)
        env.t += cfg.stuck_minutes * 60 + 1
        keys = [f.key for f in wd.check_queue(env, cfg, state)]
        assert "queue:processing-stuck" in keys

    def test_missing_password_raises_so_the_runner_reports_it(self, env, cfg):
        env.dotenv = {}
        with pytest.raises(wd.WatchdogError):
            wd.check_queue(env, cfg, {})


# ───────────────────────── lotes ─────────────────────────


def batch(bid, status="done", processos=3, docs=10, error=None, created=None):
    return {
        "batch_id": bid,
        "processos": processos,
        "status": status,
        "created_at": created or "2026-10-24T05:00:00+00:00",
        "finished_at": "2026-10-24T05:10:00+00:00",
        "total_docs": docs,
        "total_bytes": 1000,
        "error": error,
    }


class TestCheckBatches:
    def test_first_run_baselines_old_failures_silently(self, env, cfg):
        env.http[cfg.history_url] = wd.HttpResult(200, [batch("a", "failed")], None)
        state: dict = {}
        assert wd.check_batches(env, cfg, state) == []
        assert "a" in state["seen_batches"]

    def test_new_failed_batch_alerts_once_with_scrubbed_error(self, env, cfg):
        state: dict = {"seen_batches": ["old"]}
        err = f"timeout em {cnj_like()}"
        env.http[cfg.history_url] = wd.HttpResult(
            200, [batch("n1", "failed", error=err), batch("old")], None
        )
        (f,) = wd.check_batches(env, cfg, state)
        assert f.kind == "event" and f.severity == wd.ALERTA
        assert "n1" in f.text and "5000001" not in f.text
        assert wd.check_batches(env, cfg, state) == []

    def test_partial_batch_is_a_warning(self, env, cfg):
        state: dict = {"seen_batches": []}
        env.http[cfg.history_url] = wd.HttpResult(200, [batch("p1", "partial")], None)
        (f,) = wd.check_batches(env, cfg, state)
        assert f.severity == wd.AVISO

    def test_done_batch_is_silent(self, env, cfg):
        state: dict = {"seen_batches": []}
        env.http[cfg.history_url] = wd.HttpResult(200, [batch("d1", "done")], None)
        assert wd.check_batches(env, cfg, state) == []

    def test_running_batch_is_not_marked_as_seen(self, env, cfg):
        state: dict = {"seen_batches": []}
        env.http[cfg.history_url] = wd.HttpResult(200, [batch("r1", "running")], None)
        wd.check_batches(env, cfg, state)
        assert "r1" not in state["seen_batches"]

    def test_api_key_header_is_sent(self, env, cfg):
        wd.check_batches(env, cfg, {})
        url, headers = env.http_calls[-1]
        assert url == cfg.history_url and headers == {"X-API-Key": "chave-api"}

    def test_history_unavailable_is_a_warning(self, env, cfg):
        env.http[cfg.history_url] = wd.HttpResult(401, {"error": "x"}, None)
        (f,) = wd.check_batches(env, cfg, {})
        assert f.key == "batches:history-unavailable" and f.severity == wd.AVISO

    def test_seen_list_is_bounded(self, env, cfg):
        state: dict = {"seen_batches": [f"b{i}" for i in range(500)]}
        env.http[cfg.history_url] = wd.HttpResult(200, [batch("z")], None)
        wd.check_batches(env, cfg, state)
        assert len(state["seen_batches"]) <= 200


class TestCheckHost:
    def test_low_free_space_warns(self, env, cfg):
        env.free = cfg.disk_warn_mb - 1
        (f,) = wd.check_host(env, cfg)
        assert f.key == "host:disk" and f.severity == wd.AVISO

    def test_enough_space(self, env, cfg):
        assert wd.check_host(env, cfg) == []


# ───────────────────────── reconcile ─────────────────────────


def F(key="k", sev=wd.ALERTA, text="algo errado", **kw):
    return wd.Finding(key, sev, text, **kw)


class TestReconcile:
    def test_state_finding_needs_consecutive_confirmations(self, cfg):
        state: dict = {}
        assert wd.reconcile(state, [F(confirm=2)], 100.0, cfg) == []
        lines = wd.reconcile(state, [F(confirm=2)], 400.0, cfg)
        assert lines == ["[ALERTA] algo errado"]

    def test_a_blip_that_clears_does_not_alert(self, cfg):
        state: dict = {}
        wd.reconcile(state, [F(confirm=2)], 100.0, cfg)
        assert wd.reconcile(state, [], 400.0, cfg) == []
        assert wd.reconcile(state, [F(confirm=2)], 700.0, cfg) == []

    def test_announced_once_then_silent(self, cfg):
        state: dict = {}
        wd.reconcile(state, [F(confirm=1)], 100.0, cfg)
        assert wd.reconcile(state, [F(confirm=1)], 400.0, cfg) == []

    def test_reminder_after_the_interval(self, cfg):
        state: dict = {}
        wd.reconcile(state, [F(confirm=1)], 100.0, cfg)
        later = 100.0 + cfg.remind_hours * 3600 + 1
        (line,) = wd.reconcile(state, [F(confirm=1)], later, cfg)
        assert line.startswith("[LEMBRETE]")
        assert wd.reconcile(state, [F(confirm=1)], later + 60, cfg) == []

    def test_recovery_is_announced_once(self, cfg):
        state: dict = {}
        wd.reconcile(state, [F(confirm=1)], 100.0, cfg)
        (line,) = wd.reconcile(state, [], 460.0, cfg)
        assert line.startswith("[NORMALIZOU]") and "algo errado" in line
        assert wd.reconcile(state, [], 520.0, cfg) == []

    def test_events_are_announced_every_time_and_never_recover(self, cfg):
        state: dict = {}
        ev = F("e", text="lote falhou", kind="event")
        assert wd.reconcile(state, [ev], 100.0, cfg) == ["[ALERTA] lote falhou"]
        assert wd.reconcile(state, [], 200.0, cfg) == []

    def test_failed_check_keeps_its_active_findings(self, cfg):
        state: dict = {}
        wd.reconcile(state, [F("worker:down", confirm=1)], 100.0, cfg)
        lines = wd.reconcile(state, [], 400.0, cfg, skip_prefixes=("worker:",))
        assert lines == [] and "worker:down" in state["active"]


# ───────────────────────── execução ─────────────────────────


def run_check(env, cfg, tmp_path, state_name="state.json"):
    cfg.state_path = str(tmp_path / state_name)
    return wd.run_check(env, cfg)


class TestRunCheck:
    def test_all_green_prints_nothing(self, env, cfg, tmp_path):
        assert run_check(env, cfg, tmp_path) == []

    def test_outage_alerts_on_second_run_then_recovers(self, env, cfg, tmp_path):
        env.http[cfg.worker_url] = wd.HttpResult(None, None, "URLError")
        assert run_check(env, cfg, tmp_path) == []
        env.t += 300
        lines = run_check(env, cfg, tmp_path)
        assert lines[0].startswith("PJe/TJES")
        assert any("[ALERTA]" in x and "Worker" in x for x in lines)
        env.t += 300
        assert run_check(env, cfg, tmp_path) == []  # já anunciado: silêncio
        env.http[cfg.worker_url] = wd.HttpResult(200, healthy_worker(), None)
        env.t += 300
        recovered = run_check(env, cfg, tmp_path)
        assert any("[NORMALIZOU]" in x for x in recovered)

    def test_internal_check_error_is_reported_not_swallowed(
        self, env, cfg, tmp_path, monkeypatch
    ):
        def boom(*a, **k):
            raise RuntimeError("segredo-interno")

        monkeypatch.setattr(wd, "check_host", boom)
        assert run_check(env, cfg, tmp_path) == []  # 1ª vez: confirmação pendente
        env.t += 300
        lines = run_check(env, cfg, tmp_path)
        text = "\n".join(lines)
        assert "host" in text and "RuntimeError" in text
        assert "segredo-interno" not in text

    def test_a_broken_check_does_not_declare_its_problem_solved(
        self, env, cfg, tmp_path, monkeypatch
    ):
        env.http[cfg.worker_url] = wd.HttpResult(None, None, "URLError")
        run_check(env, cfg, tmp_path)
        env.t += 300
        assert any("Worker" in x for x in run_check(env, cfg, tmp_path))  # ativo

        def boom(*a, **k):
            raise RuntimeError("x")

        monkeypatch.setattr(wd, "check_worker", boom)
        env.t += 300
        lines = run_check(env, cfg, tmp_path)
        assert not any("[NORMALIZOU]" in x for x in lines)
        state = json.loads(Path(cfg.state_path).read_text())
        assert "worker:down" in state["active"]

    def test_state_file_is_private(self, env, cfg, tmp_path):
        run_check(env, cfg, tmp_path)
        p = Path(cfg.state_path)
        assert p.exists() and (p.stat().st_mode & 0o777) == 0o600

    def test_corrupt_state_starts_clean(self, env, cfg, tmp_path):
        cfg.state_path = str(tmp_path / "state.json")
        Path(cfg.state_path).write_text("{not json")
        assert wd.run_check(env, cfg) == []

    def test_concurrent_run_exits_quietly(self, env, cfg, tmp_path):
        import fcntl

        cfg.state_path = str(tmp_path / "state.json")
        lock = open(cfg.state_path + ".lock", "w")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            assert wd.run_check(env, cfg) == []
            assert env.http_calls == []
        finally:
            lock.close()


class TestDigest:
    def test_summarizes_without_case_numbers(self, env, cfg, tmp_path):
        cfg.state_path = str(tmp_path / "state.json")
        recent = "2026-10-24T05:00:00+00:00"
        env.t = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc).timestamp()
        env.http[cfg.history_url] = wd.HttpResult(
            200,
            [
                batch("a", "done", processos=4, docs=30, created=recent),
                batch(
                    "b",
                    "partial",
                    processos=2,
                    docs=5,
                    created=recent,
                    error=cnj_like(),
                ),
                batch("old", "failed", created="2026-01-01T00:00:00+00:00"),
            ],
            None,
        )
        text = "\n".join(wd.run_digest(env, cfg))
        assert "resumo diário" in text.lower()
        assert "5000001" not in text
        assert "6605fd8" in text
        assert "partial 1" in text and "done 1" in text
        assert (
            "failed" not in text.split("Lotes")[1].split("\n")[0]
        )  # lote antigo fora da janela

    def test_digest_lists_problems_found_now(self, env, cfg, tmp_path):
        cfg.state_path = str(tmp_path / "state.json")
        env.http[cfg.worker_url] = wd.HttpResult(None, None, "URLError")
        text = "\n".join(wd.run_digest(env, cfg))
        assert "Worker" in text and "ALERTA" in text

    def test_digest_does_not_touch_the_state(self, env, cfg, tmp_path):
        cfg.state_path = str(tmp_path / "state.json")
        wd.run_digest(env, cfg)
        assert not Path(cfg.state_path).exists()


class TestConfig:
    def test_environment_overrides(self):
        cfg = wd.Config.from_environ(
            {
                "WATCHDOG_STATE": "/x/s.json",
                "WATCHDOG_WORKER_URL": "http://h:1/health",
                "WATCHDOG_STUCK_MINUTES": "7",
                "WATCHDOG_REMIND_HOURS": "nao-numero",
            }
        )
        assert cfg.state_path == "/x/s.json"
        assert cfg.worker_url == "http://h:1/health"
        assert cfg.stuck_minutes == 7.0
        assert cfg.remind_hours == wd.Config().remind_hours  # valor inválido ignorado


class TestMain:
    def _run(self, env, tmp_path, ssh_cmd=None, argv=()):
        environ = {"WATCHDOG_STATE": str(tmp_path / "s.json")}
        if ssh_cmd is not None:
            environ["SSH_ORIGINAL_COMMAND"] = ssh_cmd
        return wd.main(list(argv), environ=environ, env=env)

    def test_default_mode_is_check_and_exit_zero(self, env, tmp_path, capsys):
        assert self._run(env, tmp_path) == 0
        assert capsys.readouterr().out == ""

    def test_ssh_digest_command_selects_digest(self, env, tmp_path, capsys):
        assert self._run(env, tmp_path, ssh_cmd="digest") == 0
        assert "resumo" in capsys.readouterr().out.lower()

    @pytest.mark.parametrize("evil", ["rm -rf /", "digest; id", "$(id)", "bash", ""])
    def test_any_other_ssh_command_is_just_a_check(self, env, tmp_path, capsys, evil):
        assert self._run(env, tmp_path, ssh_cmd=evil) == 0
        assert capsys.readouterr().out == ""
        assert all(a[0] in ("docker",) for a in env.argv_log)

    def test_crash_prints_a_short_message_and_exits_nonzero(
        self, env, tmp_path, capsys, monkeypatch
    ):
        monkeypatch.setattr(
            wd,
            "run_check",
            lambda *a, **k: (_ for _ in ()).throw(ValueError("detalhe-sensivel")),
        )
        assert self._run(env, tmp_path) == 1
        out = capsys.readouterr().out
        assert (
            "WATCHDOG" in out and "ValueError" in out and "detalhe-sensivel" not in out
        )


# ───────────────────────── contrato estático ─────────────────────────

SRC = (ROOT / "tools" / "pje_watchdog.py").read_text(encoding="utf-8")
TREE = ast.parse(SRC)


class TestStaticContract:
    def test_standard_library_only(self):
        import sys

        stdlib = set(sys.stdlib_module_names)
        imported = set()
        for node in ast.walk(TREE):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                imported.add((node.module or "").split(".")[0])
        assert imported <= stdlib, f"dependência fora da stdlib: {imported - stdlib}"

    def test_no_shell_no_eval(self):
        assert "shell=True" not in SRC
        names = {
            n.func.id
            for n in ast.walk(TREE)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert not names & {"eval", "exec", "compile", "__import__"}
        attrs = {
            n.func.attr
            for n in ast.walk(TREE)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        assert not attrs & {"system", "popen"}

    def test_ssh_original_command_is_only_compared_never_executed(self):
        uses = [
            n
            for n in ast.walk(TREE)
            if isinstance(n, ast.Constant) and n.value == "SSH_ORIGINAL_COMMAND"
        ]
        assert len(uses) == 1

    def test_deploy_ships_the_tools_directory(self):
        deploy = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(
            encoding="utf-8"
        )
        assert "--exclude='tools" not in deploy and "--exclude='tools/" not in deploy


# ───────────────────────── wrapper do Hermes (Mac mini) ─────────────────────────

WRAPPER = ROOT / "ops" / "hermes" / "pje-watchdog.sh"


class TestHermesWrapper:
    @pytest.fixture
    def sandbox(self, tmp_path):
        """ssh falso no PATH: grava os argumentos e devolve o que o teste mandar."""
        bindir = tmp_path / "bin"
        bindir.mkdir()
        log = tmp_path / "ssh.log"
        fake = bindir / "ssh"
        fake.write_text(
            "#!/bin/bash\n"
            'printf "%s\\n" "$@" > "$SSH_LOG"\n'
            'cat "$SSH_OUT" 2>/dev/null\n'
            'exit "${SSH_RC:-0}"\n'
        )
        fake.chmod(0o755)
        out = tmp_path / "out.txt"
        return {"bin": bindir, "log": log, "out": out, "tmp": tmp_path}

    def _run(self, sandbox, name="pje-watchdog.sh", out="", rc=0):
        import shutil
        import subprocess

        script = sandbox["tmp"] / name
        shutil.copy(WRAPPER, script)
        script.chmod(0o755)
        sandbox["out"].write_text(out)
        env = {
            "PATH": f"{sandbox['bin']}:/usr/bin:/bin",
            "HOME": str(sandbox["tmp"]),
            "SSH_LOG": str(sandbox["log"]),
            "SSH_OUT": str(sandbox["out"]),
            "SSH_RC": str(rc),
        }
        return subprocess.run(
            ["bash", str(script)], capture_output=True, text=True, env=env, timeout=20
        )

    def test_syntax(self):
        import subprocess

        assert subprocess.run(["bash", "-n", str(WRAPPER)]).returncode == 0

    def test_empty_output_is_a_silent_tick(self, sandbox):
        res = self._run(sandbox)
        assert res.returncode == 0 and res.stdout == ""

    def test_output_is_passed_through(self, sandbox):
        res = self._run(sandbox, out="[ALERTA] Worker não responde\n")
        assert res.returncode == 0 and "[ALERTA] Worker" in res.stdout

    def test_ssh_failure_becomes_a_nonzero_exit_with_a_message(self, sandbox):
        res = self._run(sandbox, out="Connection timed out", rc=255)
        assert res.returncode == 1
        assert "WATCHDOG" in res.stdout and "255" in res.stdout

    def test_mode_comes_from_the_file_name(self, sandbox):
        self._run(sandbox, name="pje-watchdog.sh")
        assert sandbox["log"].read_text().split()[-1] == "check"
        self._run(sandbox, name="pje-watchdog-digest.sh")
        assert sandbox["log"].read_text().split()[-1] == "digest"

    def test_ssh_is_strict_and_non_interactive(self, sandbox):
        self._run(sandbox)
        args = sandbox["log"].read_text()
        for opt in ("BatchMode=yes", "IdentitiesOnly=yes", "StrictHostKeyChecking=yes"):
            assert opt in args
        assert "StrictHostKeyChecking=no" not in WRAPPER.read_text()
