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
"""

from __future__ import annotations

import hashlib
import json
import os
import re
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
_RE_ENDPOINT = re.compile(r"(?:^|[\s(`'\"])/(?:api/)?[a-z][\w-]*(?:/[\w-]+)*")
_RE_SINTOMA = re.compile(
    r"\b\w*(?:Error|Exception)\b"
    r"|\bTraceback\b"
    r"|\bHTTP\s*\d{3}\b"
    r"|\b(?:retorna|devolve|responde|status)\s+(?:um\s+|o\s+)?\d{3}\b"
    r"|\btest_\w+"
)
_RE_ITEM_LINHA = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+\S", re.MULTILINE)
_RE_ITEM_INLINE = re.compile(r"(?:^|\s)\d+\)\s+\S")

# Formas pontuadas. As nuas vem de validate_br_pii (RE_CPF_NU / RE_CNPJ_NU).
_RE_CPF_PONTUADO = re.compile(r"(?<![\d.])\d{3}\.\d{3}\.\d{3}-\d{2}(?!\d)")
_RE_CNPJ_PONTUADO = re.compile(
    r"(?<![A-Z0-9.])[A-Z0-9]{2}\.[A-Z0-9]{3}\.[A-Z0-9]{3}/[A-Z0-9]{4}-\d{2}(?!\d)"
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
    cpfs = v.RE_CPF_NU.findall(prompt) + _RE_CPF_PONTUADO.findall(prompt)
    if any(v.cpf_valido(c) for c in cpfs):
        regras.append("pii_cpf")
    cnpjs = v.RE_CNPJ_NU.findall(prompt) + _RE_CNPJ_PONTUADO.findall(prompt)
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


def _limpar_estados_velhos(base: Path) -> None:
    limite = time.time() - ESTADO_MAX_DIAS * 86400
    for d in base.iterdir() if base.is_dir() else ():
        if d.is_dir() and d.stat().st_mtime < limite:
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
    nomes = set(_git(raiz, "diff", "--name-only", "HEAD").split())
    nomes |= set(_git(raiz, "ls-files", "-o", "--exclude-standard").split())
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


def _hook_prompt() -> int:
    pii_decidida = False
    try:
        if os.environ.get("PROMPT_GATE_DISABLE") == "1":
            return 0
        dados = _ler_entrada()
        prompt = dados["prompt"]
        pii = detectar_pii(prompt)
        if pii:
            print(f"prompt gate: {_motivo(pii)}", file=sys.stderr)
            return 2
        pii_decidida = True

        raiz = Path(os.environ.get("CLAUDE_PROJECT_DIR") or dados.get("cwd") or ".")
        sessao = _dir_sessao(dados.get("session_id"))
        sessao.mkdir(parents=True, exist_ok=True)
        _limpar_estados_velhos(sessao.parent)
        _gravar_snapshot(sessao, raiz)

        vaga = sessao / "avaliado"
        primeiro = not vaga.exists()
        resultado = avaliar(prompt, raiz, primeiro, _regras_bloqueadas())
        if primeiro and not eh_isento(prompt):
            vaga.touch()
        if resultado.decisao == "passa":
            return 0

        rotulo = uuid.uuid4().hex[:8]
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


def main(argv: list[str] | None = None) -> int:
    return _hook_prompt()


if __name__ == "__main__":
    sys.exit(main())
