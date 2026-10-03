#!/usr/bin/env python3
"""Prompt gate do Claude Code para este repo (spec docs/specs/2026-10-03-prompt-quality-gate.md).

Nucleo deterministico, so stdlib. `avaliar` decide sobre um prompt digitado:

- regras de qualidade (avisam; bloqueiam so as listadas em `bloquear`), avaliadas
  apenas no primeiro prompt avaliado da sessao e nunca em prompt isento.

As isencoes (`/cmd`, termina em `?`, ate 6 palavras, prefixo `!!`) valem SO para as
regras de qualidade.
"""

from __future__ import annotations

import re
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

_MENSAGENS = {
    "alvo_ou_sintoma": "cite o arquivo/símbolo a mudar ou o sintoma reproduzível "
    "(erro, código HTTP, nome do teste)",
    "possui_dod": "diga como saber que terminou (ex.: 'pronto quando pytest "
    "tests/test_x.py passar')",
    "multi_tarefa": "há 3+ tarefas empilhadas; considere um plano ou um prompt por tarefa",
}


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
    if eh_isento(prompt) or not primeiro_prompt:
        return PASSA
    regras = _regras_de_qualidade(prompt, raiz)
    if not regras:
        return PASSA
    decisao: Decisao = "bloqueia" if bloquear.intersection(regras) else "aviso"
    motivo = "; ".join(f"{r}: {_MENSAGENS[r]}" for r in regras)
    return Resultado(decisao, regras, motivo)
