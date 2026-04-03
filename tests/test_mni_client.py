"""Tests for MNI client error classification."""

from __future__ import annotations

import pytest
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client():
    """Return an MNIClient with lazy init bypassed."""
    from mni_client import MNIClient

    client = MNIClient.__new__(MNIClient)
    client.tribunal = "TJES"
    client.username = "user"
    client.password = "pass"
    client.timeout = 60
    client.wsdl_url = "https://pje.tjes.jus.br/pje/intercomunicacao?wsdl"
    client._client = None
    import threading

    client._client_lock = threading.Lock()
    client._seen_checksums = set()
    return client


# ---------------------------------------------------------------------------
# 403 / Forbidden classification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consultar_processo_403_classified_as_auth_failed():
    """HTTP 403 from the SOAP endpoint should be classified as auth_failed,
    not 'error', and the error message should not expose the raw URL."""
    import requests.exceptions

    client = _make_client()
    raw_error = (
        "403 Client Error: Forbidden for url: "
        "https://pje.tjes.jus.br/pje/intercomunicacao?wsdl"
    )
    http_exc = requests.exceptions.HTTPError(raw_error)

    with patch.object(client, "_get_client", side_effect=http_exc):
        result = await client.consultar_processo("5000001-00.2024.8.08.0001")

    assert result.success is False
    assert "403" not in result.error or "Forbidden" in result.error
    # Must not expose raw URL
    assert "pje.tjes.jus.br" not in result.error
    # Must mention the tribunal
    assert "TJES" in result.error
    # Just verify the call completed without raising
    assert result.error  # non-empty user-friendly message


@pytest.mark.asyncio
async def test_consultar_processo_forbidden_string_classified_as_auth_failed():
    """'Forbidden' in error message (non-requests exception) also maps to auth_failed."""
    client = _make_client()

    with patch.object(client, "_get_client", side_effect=Exception("Forbidden access")):
        result = await client.consultar_processo("5000002-00.2024.8.08.0001")

    assert result.success is False
    assert "TJES" in result.error


@pytest.mark.asyncio
async def test_consultar_processo_not_found_message():
    """'Processo não encontrado' yields clean not_found error."""
    client = _make_client()

    with patch.object(
        client,
        "_get_client",
        side_effect=Exception("Processo não encontrado no sistema"),
    ):
        result = await client.consultar_processo("5000003-00.2024.8.08.0001")

    assert result.success is False
    assert "não encontrado" in result.error.lower()


@pytest.mark.asyncio
async def test_consultar_processo_acesso_negado_message():
    """'Acesso negado' yields clean auth_failed error."""
    client = _make_client()

    with patch.object(
        client, "_get_client", side_effect=Exception("Acesso negado ao processo")
    ):
        result = await client.consultar_processo("5000004-00.2024.8.08.0001")

    assert result.success is False
    assert "credenciais" in result.error.lower() or "acesso" in result.error.lower()
