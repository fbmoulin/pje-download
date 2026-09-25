#!/usr/bin/env python3
"""Post-deploy MNI credential smoke test (finding F3).

POR QUE ESTE ARQUIVO EXISTE
----------------------------
O antigo passo "Validate MNI credentials" do deploy.yml rodava só
`from mni_client import MNIClient; MNIClient()` — isso constrói o cliente
(lookup em TRIBUNAL_ENDPOINTS + guarda duas strings) e NUNCA chama o zeep
(lazy). Credenciais só são transmitidas em um lugar do código inteiro:
`_call_consultar_processo` (`idConsultante`/`senhaConsultante`). Medido:
esse passo saía 0 tanto com credenciais VAZIAS quanto com credenciais
ERRADAS — só falhava em erro de import (ex.: o typo `MniClient`/`MNIClient`
corrigido em `461a789`). `health_check()` também não ajudaria: busca o WSDL
e lista operações, nunca envia credencial. Ver docs/reports/2026-09-20-full-
analysis.md seção F3.

Este script chama `MNIClient.verify_credentials()`, que emite UMA chamada
`consultarProcesso` real contra um número CNJ sintaticamente válido mas
deliberadamente inexistente, e classifica o resultado reusando a MESMA
classificação que `consultar_processo` já calcula (`MNIResult.status`) — sem
inventar um classificador paralelo.

Uso:
    python -m tools.verify_mni_credentials
    python tools/verify_mni_credentials.py

Códigos de saída:
    0 = credenciais válidas (servidor autenticou a chamada)
    1 = credenciais inválidas (servidor rejeitou — auth_failed)
    2 = inconclusivo (timeout, erro de transporte, falha inesperada, ou erro
        de configuração como tribunal desconhecido) — NUNCA deve derrubar um
        deploy sozinho; o passo do workflow trata este código como aviso.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Allow `python tools/verify_mni_credentials.py` from the repo root: Python
# puts this script's OWN directory (tools/) on sys.path[0], not the repo
# root, so `import config` / `import mni_client` would fail without this.
# Harmless when run as `python -m tools.verify_mni_credentials` too.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _load_env() -> None:
    """Load .env BEFORE importing/constructing MNIClient.

    config.py module-level constants (MNI_USERNAME/MNI_PASSWORD/MNI_TRIBUNAL)
    are captured at first import — they can be empty strings if `.env` has
    not been read yet. See CLAUDE.md "Env Loading (critical gotcha)".
    """
    from config import load_env

    load_env()


def main() -> int:
    _load_env()

    try:
        from mni_client import MNIClient

        client = MNIClient()
    except Exception as exc:  # e.g. unsupported MNI_TRIBUNAL
        print(f"MNI verification inconclusive: could not construct client: {exc}")
        return 2

    try:
        outcome = asyncio.run(client.verify_credentials())
    except Exception as exc:
        # verify_credentials() classifies everything it can reach internally
        # (consultar_processo swallows SOAP/transport errors into MNIResult).
        # Anything that still escapes here is unexpected — treat as
        # inconclusive, never as a confirmed credential failure.
        print(f"MNI verification inconclusive: unexpected error: {exc}")
        return 2

    result = outcome["result"]
    reason = outcome["reason"]
    latency_ms = outcome["latency_ms"]

    if result == "valid":
        print(
            f"MNI credentials are VALID (tribunal={client.tribunal}, latency_ms={latency_ms})"
        )
        return 0
    if result == "invalid":
        print(
            f"MNI credentials are INVALID (tribunal={client.tribunal}, "
            f"latency_ms={latency_ms}): {reason}"
        )
        return 1

    print(
        f"MNI verification INCONCLUSIVE (tribunal={client.tribunal}, "
        f"latency_ms={latency_ms}): {reason}"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
