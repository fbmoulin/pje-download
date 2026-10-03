#!/usr/bin/env python3
"""Prompt gate do Claude Code para este repo (spec docs/specs/2026-10-03-prompt-quality-gate.md).

Nucleo deterministico, so stdlib. `avaliar` decide sobre um prompt digitado:

- PII (CPF/CNPJ com digito verificador valido, nu ou pontuado) e checada PRIMEIRO e
  sempre bloqueia — nenhuma isencao a pula, nem "!!";
- regras de qualidade (avisam; bloqueiam so as listadas em `bloquear`), avaliadas
  apenas no primeiro prompt avaliado da sessao e nunca em prompt isento.

As isencoes (`/cmd`, termina em `?`, ate 6 palavras, prefixo `!!`) valem SO para as
regras de qualidade.

O `motivo` de um bloqueio por PII nao carrega o valor, nem mascarado: o stderr do hook
vai para o transcript da sessao.

Como hook `UserPromptSubmit` (stdin = JSON do Claude Code), `main()`:

- `PROMPT_GATE_DISABLE=1` -> exit 0 (kill switch; nunca fixado no settings);
- PII antes de qualquer git/disco; PII -> exit 2. Qualquer excecao ate a PII estar
  decidida -> exit 2 (fail-closed). Timeout do hook e exit != 2 continuam falhando
  abertos no Claude Code — limite declarado no modelo de ameaca da spec;
- grava, fora do repo, o snapshot do turno (HEAD + sha256 dos .py sujos/nao
  rastreados) que o Stop hook compara depois;
- qualidade so na vaga de "primeiro prompt avaliado" da sessao; aviso -> exit 0 com
  `additionalContext` (modelo) e `systemMessage` (usuario, com id para rotular);
  regra em `PROMPT_GATE_BLOCK_RULES` -> exit 2. Excecao aqui -> exit 0 (fail-open).

Telemetria (fora do repo, sem texto nem hash do prompt) em
`${XDG_STATE_HOME:-~/.local/state}/pje-prompt-gate/telemetria.jsonl`; registros de PII
guardam so `ts`, `regras` e `decisao`. Uso manual:

    python3 tools/prompt_gate.py --label <id> fp|tp   # rotula um aviso
    python3 tools/prompt_gate.py --report             # avisos/rotulos por regra

Como hook `Stop` (`--stop`): compara o repo com o snapshot do turno e, se algum .py
mudou DURANTE o turno, roda ruff (o do CI, `uvx ruff@0.14.14`) nesses arquivos e pytest
em `tests/test_<mod>*.py` + testes tocados. Violacao de lint ou teste falhando ->
`{"decision": "block"}`; ferramenta ausente, erro de coleta ou timeout -> so
`systemMessage`. `stop_hook_active`, kill switch, sem snapshot ou qualquer excecao ->
exit 0. `PROMPT_GATE_PYTHON` escolhe o interpretador do pytest (ex.: o do venv) e
`PROMPT_GATE_RUFF` o comando do ruff.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Decisao = Literal["passa", "aviso", "bloqueia"]


@dataclass(frozen=True)
class Resultado:
    decisao: Decisao
    regras: tuple[str, ...]  # ids das regras disparadas, em ordem estavel
    motivo: str  # texto para o usuario; nunca contem PII


PASSA = Resultado("passa", (), "")

# Marcadores de "definition of done". Comparados em minusculas, por substring.
MARCADORES_DOD = (
    "pronto quando",
    "deve passar",
    "devem passar",
    "pytest",
    "critério de aceite",
    "criterio de aceite",
    "definition of done",
    "ruff check",
    "verify_spec",
    "verify_gitleaks_rules",
    "verify.sh",
    "até o teste passar",
    "ate o teste passar",
    "com teste",
    "teste novo",
)

ISENCAO_MAX_PALAVRAS = 6
MULTI_TAREFA_MIN_ITENS = 3

_EXTENSOES = "py|md|ya?ml|json|toml|sh|js|ts|html|css|sql|txt|ini|cfg|lock"
# Arquivo com extensao conhecida, com ou sem diretorio ("worker.py", "tests/test_x.py").
_RE_ARQUIVO = re.compile(rf"(?<![\w/.-])((?:[\w.-]+/)*[\w.-]+\.(?:{_EXTENSOES}))\b")
# Diretorio relativo terminado em "/" ("ops/monitoring/", "src/utils/").
_RE_DIRETORIO = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)+)(?=[\s,;:.)`'\"]|$)")
# Endpoint HTTP ("/health", "/api/status") — alvo concreto, mesmo sem arquivo.
_RE_ENDPOINT = re.compile(
    r"(?:^|[\s(`'\"])/(?:api/)?[a-z][\w-]*(?:/[\w-]+)*(?![\w./-])"
)
_RE_SINTOMA = re.compile(
    r"\b\w*(?:Error|Exception)\b"
    r"|\bTraceback\b"
    r"|\bHTTP\s*\d{3}\b"
    r"|\b(?:retorna|devolve|responde|status)\s+(?:um\s+|o\s+)?\d{3}\b"
    r"|\btest_\w+"
)
_RE_ITEM_LINHA = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+\S", re.MULTILINE)
_RE_ITEM_INLINE = re.compile(r"(?:^|\s)\d+\)\s+\S")

# Formas com separadores. A nua vem de validate_br_pii (RE_CPF_NU / RE_CNPJ_NU); aqui
# cobrimos ponto, espaco, traco so no DV, ponto sem traco etc. O DV filtra o resto.
_RE_CPF_SEPARADO = re.compile(
    r"(?<![\d.])\d{3}[.\s]?\d{3}[.\s]?\d{3}[-.\s]?\d{2}(?![\d])"
)
# CNPJ numerico com separadores (inclui espacos).
_RE_CNPJ_SEPARADO = re.compile(
    r"(?<![\d.])\d{2}[.\s]?\d{3}[.\s]?\d{3}[/\s]?\d{4}[-\s]?\d{2}(?!\d)"
)
# CNPJ alfanumerico (IN RFB 2.229/2024), nu ou pontuado, maiusculo ou minusculo. Sem
# espacos: com letras, espaco opcional casaria trechos de frase comum.
_RE_CNPJ_ALFA = re.compile(
    r"(?<![A-Za-z0-9.])[A-Za-z0-9]{2}\.?[A-Za-z0-9]{3}\.?[A-Za-z0-9]{3}/?"
    r"[A-Za-z0-9]{4}-?\d{2}(?![A-Za-z0-9])"
)

_MENSAGENS = {
    "pii_cpf": "o prompt contém um CPF válido; remova-o e use um placeholder",
    "pii_cnpj": "o prompt contém um CNPJ válido; remova-o e use um placeholder",
    "alvo_ou_sintoma": "cite o arquivo/símbolo a mudar ou o sintoma reproduzível "
    "(erro, código HTTP, nome do teste)",
    "possui_dod": "diga como saber que terminou (ex.: 'pronto quando pytest "
    "tests/test_x.py passar')",
    "multi_tarefa": "há 3+ tarefas empilhadas; considere um plano ou um prompt por tarefa",
}


def _validadores():
    """Import tardio: uma falha aqui precisa chegar ao main() (fail-closed, Task 4)."""
    try:
        from tools import validate_br_pii
    except ImportError:  # executado como script: sys.path[0] e tools/
        import validate_br_pii
    return validate_br_pii


def detectar_pii(prompt: str) -> tuple[str, ...]:
    """Ids das regras de PII disparadas, em ordem estavel. Nao devolve os valores."""
    v = _validadores()
    regras = []
    cpfs = v.RE_CPF_NU.findall(prompt) + _RE_CPF_SEPARADO.findall(prompt)
    if any(v.cpf_valido(c) for c in cpfs):
        regras.append("pii_cpf")
    cnpjs = (
        v.RE_CNPJ_NU.findall(prompt)
        + _RE_CNPJ_SEPARADO.findall(prompt)
        + _RE_CNPJ_ALFA.findall(prompt)
    )
    if any(v.cnpj_valido(c) for c in cnpjs):
        regras.append("pii_cnpj")
    return tuple(regras)


def eh_isento(prompt: str) -> bool:
    """Prompt que nunca passa pelas regras de qualidade (nem consome a vaga)."""
    s = prompt.strip()
    return (
        s.startswith("/")
        or s.startswith("!!")
        or s.endswith("?")
        or len(s.split()) <= ISENCAO_MAX_PALAVRAS
    )


def _tem_alvo_ou_sintoma(prompt: str, raiz: Path) -> bool:
    if _RE_SINTOMA.search(prompt) or _RE_ENDPOINT.search(prompt):
        return True
    caminhos = _RE_ARQUIVO.findall(prompt) + _RE_DIRETORIO.findall(prompt)
    return any((raiz / c).exists() for c in caminhos)


def _tem_dod(prompt: str) -> bool:
    baixo = prompt.lower()
    return any(m in baixo for m in MARCADORES_DOD)


def _empilha_tarefas(prompt: str) -> bool:
    itens = max(
        len(_RE_ITEM_LINHA.findall(prompt)), len(_RE_ITEM_INLINE.findall(prompt))
    )
    return itens >= MULTI_TAREFA_MIN_ITENS


def _regras_de_qualidade(prompt: str, raiz: Path) -> tuple[str, ...]:
    regras = []
    if not _tem_alvo_ou_sintoma(prompt, raiz):
        regras.append("alvo_ou_sintoma")
    if not _tem_dod(prompt):
        regras.append("possui_dod")
    if _empilha_tarefas(prompt):
        regras.append("multi_tarefa")
    return tuple(regras)


def avaliar(
    prompt: str,
    raiz: Path,
    primeiro_prompt: bool,
    bloquear: frozenset[str] = frozenset(),
) -> Resultado:
    pii = detectar_pii(prompt)
    if pii:
        return Resultado("bloqueia", pii, _motivo(pii))
    if eh_isento(prompt) or not primeiro_prompt:
        return PASSA
    regras = _regras_de_qualidade(prompt, raiz)
    if not regras:
        return PASSA
    decisao: Decisao = "bloqueia" if bloquear.intersection(regras) else "aviso"
    return Resultado(decisao, regras, _motivo(regras))


def _motivo(regras: tuple[str, ...]) -> str:
    return "; ".join(f"{r}: {_MENSAGENS[r]}" for r in regras)


# --- hook UserPromptSubmit ---------------------------------------------------

ESTADO_MAX_DIAS = 30
_GIT_TIMEOUT_S = 3
_FALHA_PII = (
    "prompt gate: a checagem de PII falhou ({erro}); o prompt foi bloqueado por "
    "seguranca. Para desligar o gate: saia e relance com "
    "PROMPT_GATE_DISABLE=1 claude --continue"
)


def _base_estado() -> Path:
    raiz = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(raiz) / "pje-prompt-gate"


def _dir_sessao(session_id: object) -> Path:
    nome = re.sub(r"[^A-Za-z0-9_-]", "", str(session_id or "")) or "sem-sessao"
    return _base_estado() / nome


def _limpar_estados_velhos(base: Path, atual: Path) -> None:
    limite = time.time() - ESTADO_MAX_DIAS * 86400
    for d in base.iterdir() if base.is_dir() else ():
        if d != atual and d.is_dir() and d.stat().st_mtime < limite:
            shutil.rmtree(d, ignore_errors=True)


def _git(raiz: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(raiz), *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=_GIT_TIMEOUT_S,
    ).stdout


def _sha256(caminho: Path) -> str:
    return hashlib.sha256(caminho.read_bytes()).hexdigest()


def snapshot(raiz: Path) -> dict:
    """HEAD e sha256 dos .py sujos (vs HEAD) ou nao rastreados, relativos a raiz."""
    head = _git(raiz, "rev-parse", "HEAD").strip()
    nomes = set(_git(raiz, "diff", "--name-only", "-z", "HEAD").split("\0"))
    nomes |= set(_git(raiz, "ls-files", "-o", "-z", "--exclude-standard").split("\0"))
    nomes.discard("")
    arquivos = {
        n: _sha256(raiz / n)
        for n in sorted(nomes)
        if n.endswith(".py") and (raiz / n).is_file()
    }
    return {"head": head, "arquivos": arquivos}


def _regras_bloqueadas() -> frozenset[str]:
    bruto = os.environ.get("PROMPT_GATE_BLOCK_RULES", "")
    return frozenset(r.strip() for r in bruto.split(",") if r.strip())


def _ler_entrada() -> dict:
    dados = json.loads(sys.stdin.read())
    if not isinstance(dados, dict) or not isinstance(dados.get("prompt"), str):
        raise ValueError("stdin sem campo 'prompt' textual")
    return dados


def _gravar_snapshot(sessao: Path, raiz: Path) -> None:
    try:
        (sessao / "turno.json").write_text(json.dumps(snapshot(raiz)), "utf-8")
    except (OSError, subprocess.SubprocessError, ValueError):
        # Sem git/repo: o Stop hook simplesmente nao checa nada neste turno.
        (sessao / "turno.json").unlink(missing_ok=True)


def _agora() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _anexar(nome: str, registro: dict) -> None:
    base = _base_estado()
    base.mkdir(parents=True, exist_ok=True)
    with open(base / nome, "a", encoding="utf-8") as f:
        f.write(json.dumps(registro, ensure_ascii=False) + "\n")


def _ler_jsonl(nome: str) -> list[dict]:
    arq = _base_estado() / nome
    if not arq.exists():
        return []
    registros = []
    for linha in arq.read_text("utf-8").splitlines():
        try:
            registros.append(json.loads(linha))
        except ValueError:
            continue  # linha truncada por escrita concorrente: ignora
    return registros


def _hook_prompt() -> int:
    pii_decidida = False
    inicio = time.perf_counter()
    try:
        if os.environ.get("PROMPT_GATE_DISABLE") == "1":
            return 0
        dados = _ler_entrada()
        prompt = dados["prompt"]
        pii = detectar_pii(prompt)
        if pii:
            print(f"prompt gate: {_motivo(pii)}", file=sys.stderr)
            _anexar(
                "telemetria.jsonl",
                {"ts": _agora(), "regras": list(pii), "decisao": "bloqueia"},
            )
            return 2
        pii_decidida = True

        raiz = Path(os.environ.get("CLAUDE_PROJECT_DIR") or dados.get("cwd") or ".")
        sessao = _dir_sessao(dados.get("session_id"))
        sessao.mkdir(parents=True, exist_ok=True)
        os.utime(sessao)  # sessao retomada (--resume) nao envelhece
        _limpar_estados_velhos(sessao.parent, sessao)
        _gravar_snapshot(sessao, raiz)

        vaga = sessao / "avaliado"
        primeiro = not vaga.exists()
        resultado = avaliar(prompt, raiz, primeiro, _regras_bloqueadas())
        avaliado = primeiro and not eh_isento(prompt)
        rotulo = uuid.uuid4().hex[:8]
        if avaliado and resultado.decisao != "bloqueia":
            vaga.touch()  # bloqueado nao consome a vaga: reenviar igual bloqueia de novo
        if avaliado:
            _anexar(
                "telemetria.jsonl",
                {
                    "ts": _agora(),
                    "id": rotulo,
                    "session_id": str(dados.get("session_id") or ""),
                    "prompt_id": str(dados.get("prompt_id") or ""),
                    "regras": list(resultado.regras),
                    "decisao": resultado.decisao,
                    "latencia_ms": round((time.perf_counter() - inicio) * 1000, 1),
                },
            )
        if resultado.decisao == "passa":
            return 0

        if resultado.decisao == "bloqueia":
            print(
                f"prompt gate [{rotulo}]: {resultado.motivo}. Comece com !! para "
                "passar mesmo assim, ou use /refine-prompt para reescrever.",
                file=sys.stderr,
            )
            return 2
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "UserPromptSubmit",
                        "additionalContext": (
                            "Prompt gate (aviso, nao bloqueia): "
                            f"{resultado.motivo}. Se faltar informacao para fazer "
                            "a tarefa com seguranca, pergunte ao usuario antes de "
                            "assumir."
                        ),
                    },
                    "systemMessage": (
                        f"gate [{rotulo}] {', '.join(resultado.regras)} — rotule: "
                        f"python3 tools/prompt_gate.py --label {rotulo} fp|tp"
                    ),
                },
                ensure_ascii=False,
            )
        )
        return 0
    except BaseException as erro:  # noqa: BLE001 — o hook nunca pode vazar excecao
        if not pii_decidida:
            print(_FALHA_PII.format(erro=type(erro).__name__), file=sys.stderr)
            return 2
        return 0


# --- hook Stop ------------------------------------------------------------------

RUFF_PADRAO = "uvx ruff@0.14.14"
# Prazo unico para tudo o que o Stop roda; fica abaixo do timeout registrado no
# settings (180 s), senao o Claude Code mata o hook antes de ele reportar.
STOP_ORCAMENTO_S = 165
PYTEST_TIMEOUT_S = 120
RUFF_TIMEOUT_S = 60
SONDA_TIMEOUT_S = 15
RESUMO_MAX_LINHAS = 20
_RE_MODULO_AUSENTE = re.compile(r"No module named '([\w.]+)'")


def arquivos_do_turno(raiz: Path, anterior: dict) -> list[str]:
    """.py que existem e mudaram desde o snapshot do UserPromptSubmit."""
    atual = snapshot(raiz)
    antes = anterior.get("arquivos", {})
    mudaram = {n for n, h in atual["arquivos"].items() if antes.get(n) != h}
    if anterior.get("head") and anterior["head"] != atual["head"]:
        commitados = _git(
            raiz,
            "diff",
            "--name-only",
            "-z",
            "--diff-filter=d",
            f"{anterior['head']}..HEAD",
        ).split("\0")
        mudaram |= {n for n in commitados if n.endswith(".py")}
    return sorted(n for n in mudaram if (raiz / n).is_file())


def testes_para(raiz: Path, arquivos: list[str]) -> list[str]:
    testes = set()
    for nome in arquivos:
        caminho = Path(nome)
        if caminho.parts[:1] == ("tests",):
            if not caminho.name.startswith("test_"):
                return ["tests"]  # conftest/helper compartilhado: a pasta inteira
            testes.add(nome)
            continue
        for t in (raiz / "tests").glob(f"test_{caminho.stem}*.py"):
            testes.add(str(t.relative_to(raiz)))
    return sorted(testes)


def _resumo(texto: str) -> str:
    return "\n".join(texto.strip().splitlines()[-RESUMO_MAX_LINHAS:])


def _restante(prazo: float, teto: float) -> float:
    return max(0.0, min(teto, prazo - time.monotonic()))


def _checar_lint(
    raiz: Path, arquivos: list[str], prazo: float
) -> tuple[str | None, str | None]:
    """(motivo de bloqueio, aviso). Roda `check` e `format --check`, como o CI."""
    cmd = shlex.split(os.environ.get("PROMPT_GATE_RUFF") or RUFF_PADRAO)
    if not shutil.which(cmd[0]):
        return None, f"ruff não rodou ({cmd[0]} ausente)"
    problemas, avisos = [], []
    for sub, sinal in ((["check"], "Found"), (["format", "--check"], "Would reformat")):
        tempo = _restante(prazo, RUFF_TIMEOUT_S)
        if tempo < 1:
            avisos.append("sem tempo para o ruff")
            break
        try:
            p = subprocess.run(
                [*cmd, *sub, *arquivos],
                cwd=raiz,
                capture_output=True,
                text=True,
                timeout=tempo,
            )
        except subprocess.TimeoutExpired:
            avisos.append(f"ruff {sub[0]} excedeu o tempo")
            continue
        saida = p.stdout + p.stderr
        if p.returncode == 1 and sinal in saida:
            problemas.append(f"ruff {' '.join(sub)}:\n{_resumo(saida)}")
        elif p.returncode != 0:
            avisos.append(f"ruff {sub[0]} não rodou (exit {p.returncode})")
    return ("\n\n".join(problemas) or None), ("; ".join(avisos) or None)


def _modulo_do_projeto(raiz: Path, modulo: str) -> bool:
    topo = modulo.split(".")[0]
    return (raiz / f"{topo}.py").exists() or (raiz / topo).is_dir()


def _checar_testes(
    raiz: Path, testes: list[str], prazo: float
) -> tuple[str | None, str | None]:
    py = os.environ.get("PROMPT_GATE_PYTHON") or sys.executable
    try:
        sonda = subprocess.run(
            [py, "-c", "import pytest"],
            capture_output=True,
            timeout=_restante(prazo, SONDA_TIMEOUT_S) or 1,
        )
        if sonda.returncode:
            return None, f"pytest indisponível em {py} (defina PROMPT_GATE_PYTHON)"
        tempo = _restante(prazo, PYTEST_TIMEOUT_S)
        if tempo < 5:
            return None, "sem tempo para o pytest"
        p = subprocess.run(
            [
                py,
                "-m",
                "pytest",
                "-q",
                "--no-header",
                "-p",
                "no:cacheprovider",
                *testes,
            ],
            cwd=raiz,
            capture_output=True,
            text=True,
            timeout=tempo,
        )
    except subprocess.TimeoutExpired:
        return None, "pytest excedeu o tempo do hook"
    except OSError as erro:
        return None, f"pytest não rodou ({type(erro).__name__})"
    saida = p.stdout + p.stderr
    if p.returncode == 1:
        return f"testes falhando:\n{_resumo(saida)}", None
    if p.returncode == 2:
        # Coleta interrompida: dependencia externa ausente e ambiente; import do proprio
        # projeto quebrado (nome removido, modulo apagado) e defeito do turno.
        ausentes = _RE_MODULO_AUSENTE.findall(saida)
        if ausentes and not any(_modulo_do_projeto(raiz, m) for m in ausentes):
            faltam = ", ".join(sorted(set(ausentes)))
            return None, f"pytest sem dependências no ambiente ({faltam})"
        return f"testes não coletam:\n{_resumo(saida)}", None
    if p.returncode not in (0, 5):  # 5 = nenhum teste coletado
        return None, f"pytest não rodou os testes (exit {p.returncode})"
    return None, None


def _hook_stop() -> int:
    try:
        if os.environ.get("PROMPT_GATE_DISABLE") == "1":
            return 0
        dados = json.loads(sys.stdin.read())
        if dados.get("stop_hook_active"):
            return 0
        raiz = Path(os.environ.get("CLAUDE_PROJECT_DIR") or dados.get("cwd") or ".")
        arq = _dir_sessao(dados.get("session_id")) / "turno.json"
        if not arq.exists():
            return 0
        arquivos = arquivos_do_turno(raiz, json.loads(arq.read_text("utf-8")))
        if not arquivos:
            return 0
        prazo = time.monotonic() + STOP_ORCAMENTO_S
        testes = testes_para(raiz, arquivos)
        checagens = [_checar_lint(raiz, arquivos, prazo)]
        if testes:
            checagens.append(_checar_testes(raiz, testes, prazo))
        bloqueios = [m for m, _ in checagens if m]
        avisos = [a for _, a in checagens if a]
        if bloqueios:
            razao = "Prompt gate (Stop): corrija antes de encerrar.\n" + "\n\n".join(
                bloqueios
            )
            print(
                json.dumps({"decision": "block", "reason": razao}, ensure_ascii=False)
            )
        elif avisos:
            msg = "prompt gate (Stop): " + "; ".join(avisos)
            print(json.dumps({"systemMessage": msg}, ensure_ascii=False))
        return 0
    except BaseException:  # noqa: BLE001 — Stop falha aberto
        return 0


# --- uso manual: --label / --report -----------------------------------------


def _rotular(args: list[str]) -> int:
    if len(args) != 2 or args[1] not in ("fp", "tp"):
        print("uso: prompt_gate.py --label <id> fp|tp", file=sys.stderr)
        return 1
    rotulo, valor = args
    ids = {r.get("id") for r in _ler_jsonl("telemetria.jsonl")}
    if rotulo not in ids:
        print(f"id desconhecido: {rotulo}", file=sys.stderr)
        return 1
    _anexar("rotulos.jsonl", {"ts": _agora(), "id": rotulo, "rotulo": valor})
    print(f"rotulado: {rotulo} = {valor}")
    return 0


REGRAS_DE_QUALIDADE = ("alvo_ou_sintoma", "possui_dod", "multi_tarefa")


def _relatorio() -> int:
    telemetria = _ler_jsonl("telemetria.jsonl")
    rotulos = {r["id"]: r["rotulo"] for r in _ler_jsonl("rotulos.jsonl")}  # ultimo vale
    avaliados = [r for r in telemetria if "id" in r]
    pii = sum(1 for r in telemetria if "id" not in r)
    print(f"prompts avaliados: {len(avaliados)}")
    print(f"bloqueios por PII: {pii}")
    print(f"{'regra':<16} avisos rotulados fp tp")
    for regra in REGRAS_DE_QUALIDADE:
        com = [r for r in avaliados if regra in r.get("regras", [])]
        rot = [rotulos[r["id"]] for r in com if r["id"] in rotulos]
        print(f"{regra:<16} {len(com)} {len(rot)} {rot.count('fp')} {rot.count('tp')}")
    pendentes = [r for r in avaliados if r.get("regras") and r["id"] not in rotulos]
    print(f"pendentes de rotulo ({len(pendentes)}; mais recentes primeiro):")
    for r in reversed(pendentes[-20:]):
        print(
            f"  {r['id']}  {r['ts']}  sessao={r.get('session_id')} "
            f"prompt={r.get('prompt_id')}  {','.join(r['regras'])}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["--label"]:
        return _rotular(args[1:])
    if args[:1] == ["--report"]:
        return _relatorio()
    if args[:1] == ["--stop"]:
        return _hook_stop()
    return _hook_prompt()


if __name__ == "__main__":
    sys.exit(main())
