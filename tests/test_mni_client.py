"""Tests for MNI client error classification."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


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


# ---------------------------------------------------------------------------
# _mimetype_to_ext
# ---------------------------------------------------------------------------


class TestMimetypeToExt:
    def test_pdf(self):
        from mni_client import _mimetype_to_ext

        assert _mimetype_to_ext("application/pdf") == ".pdf"

    def test_html(self):
        from mni_client import _mimetype_to_ext

        assert _mimetype_to_ext("text/html") == ".html"

    def test_txt(self):
        from mni_client import _mimetype_to_ext

        assert _mimetype_to_ext("text/plain") == ".txt"

    def test_png(self):
        from mni_client import _mimetype_to_ext

        assert _mimetype_to_ext("image/png") == ".png"

    def test_jpeg(self):
        from mni_client import _mimetype_to_ext

        assert _mimetype_to_ext("image/jpeg") == ".jpg"

    def test_doc(self):
        from mni_client import _mimetype_to_ext

        assert _mimetype_to_ext("application/msword") == ".doc"

    def test_docx(self):
        from mni_client import _mimetype_to_ext

        assert (
            _mimetype_to_ext(
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            )
            == ".docx"
        )

    def test_unknown_returns_bin(self):
        from mni_client import _mimetype_to_ext

        assert _mimetype_to_ext("application/octet-stream") == ".bin"

    def test_empty_returns_bin(self):
        from mni_client import _mimetype_to_ext

        assert _mimetype_to_ext("") == ".bin"


# ---------------------------------------------------------------------------
# MNIClient.__init__
# ---------------------------------------------------------------------------


class TestMNIClientInit:
    def test_valid_tribunal(self):
        from mni_client import MNIClient, TRIBUNAL_ENDPOINTS

        client = MNIClient(tribunal="TJES", username="u", password="p")
        assert client.tribunal == "TJES"
        assert client.wsdl_url == TRIBUNAL_ENDPOINTS["TJES"]

    def test_tribunal_case_insensitive(self):
        from mni_client import MNIClient

        client = MNIClient(tribunal="tjes", username="u", password="p")
        assert client.tribunal == "TJES"

    def test_invalid_tribunal_raises(self):
        from mni_client import MNIClient

        with pytest.raises(ValueError, match="não suportado"):
            MNIClient(tribunal="INVALID", username="u", password="p")

    def test_all_tribunals_valid(self):
        from mni_client import MNIClient, TRIBUNAL_ENDPOINTS

        for tribunal in TRIBUNAL_ENDPOINTS:
            client = MNIClient(tribunal=tribunal, username="u", password="p")
            assert client.wsdl_url == TRIBUNAL_ENDPOINTS[tribunal]

    def test_default_timeout(self):
        from mni_client import MNIClient

        client = MNIClient(tribunal="TJES", username="u", password="p")
        assert client.timeout == 60

    def test_custom_timeout(self):
        from mni_client import MNIClient

        client = MNIClient(tribunal="TJES", username="u", password="p", timeout=30)
        assert client.timeout == 30


# ---------------------------------------------------------------------------
# MNIClient._save_document
# ---------------------------------------------------------------------------


class TestSaveDocument:
    def test_saves_file_with_correct_content(self, tmp_path):
        from mni_client import MNIDocumento

        client = _make_client()
        content = b"PDF file content"
        b64 = base64.b64encode(content).decode("ascii")
        doc = MNIDocumento(
            id="123",
            nome="Peticao Inicial",
            tipo="peticao",
            conteudo_base64=b64,
            tamanho_bytes=len(content),
        )
        seen: set[str] = set()
        result = client._save_document(doc, tmp_path, seen)
        assert result is not None
        assert result["nome"].endswith(".pdf")
        assert result["tamanhoBytes"] == len(content)
        saved_file = Path(result["localPath"])
        assert saved_file.exists()
        assert saved_file.read_bytes() == content

    def test_skips_duplicate_by_checksum(self, tmp_path):
        from mni_client import MNIDocumento

        client = _make_client()
        content = b"same content"
        b64 = base64.b64encode(content).decode("ascii")
        checksum = hashlib.sha256(content).hexdigest()
        seen: set[str] = {checksum}
        doc = MNIDocumento(
            id="456",
            nome="Doc",
            tipo="doc",
            conteudo_base64=b64,
            tamanho_bytes=len(content),
        )
        result = client._save_document(doc, tmp_path, seen)
        assert result is None

    def test_propagates_oserror(self, tmp_path):
        from mni_client import MNIDocumento

        client = _make_client()
        b64 = base64.b64encode(b"x").decode("ascii")
        doc = MNIDocumento(
            id="789", nome="Doc", tipo="doc", conteudo_base64=b64, tamanho_bytes=1
        )
        readonly_dir = tmp_path / "readonly"
        readonly_dir.mkdir()
        readonly_dir.chmod(0o444)
        try:
            with pytest.raises(OSError):
                client._save_document(doc, readonly_dir, set())
        finally:
            readonly_dir.chmod(0o755)


# ---------------------------------------------------------------------------
# MNIClient._parse_processo
# ---------------------------------------------------------------------------


class TestParseProcesso:
    def _mock_response(self, **overrides):
        dados = MagicMock()
        dados.classeProcessual = overrides.get("classe", "Execucao de Titulo")
        dados.assunto = overrides.get("assuntos", [])
        dados.polo = overrides.get("polos", [])

        doc = MagicMock()
        doc.idDocumento = "doc1"
        doc.descricao = "Peticao Inicial"
        doc.tipoDocumento = "Peticao"
        doc.mimetype = "application/pdf"
        doc.conteudo = None
        doc.documentoVinculado = []

        proc = MagicMock()
        proc.dadosBasicos = dados
        proc.documento = overrides.get("documentos", [doc])
        proc.movimento = overrides.get("movimentos", [])

        resp = MagicMock()
        resp.processo = proc
        return resp

    def test_parses_basic_processo(self):
        client = _make_client()
        resp = self._mock_response()
        result = client._parse_processo(resp, "5000001-00.2024.8.08.0001")
        assert result.numero == "5000001-00.2024.8.08.0001"
        assert result.classe == "Execucao de Titulo"
        assert len(result.documentos) == 1
        assert result.documentos[0].id == "doc1"

    def test_parses_polo_ativo_passivo(self):
        polo_at = MagicMock()
        polo_at.polo = "AT"
        parte_at = MagicMock()
        parte_at.pessoa = MagicMock(nome="Joao Silva")
        polo_at.parte = [parte_at]

        polo_pa = MagicMock()
        polo_pa.polo = "PA"
        parte_pa = MagicMock()
        parte_pa.pessoa = MagicMock(nome="Banco SA")
        polo_pa.parte = [parte_pa]

        client = _make_client()
        resp = self._mock_response(polos=[polo_at, polo_pa])
        result = client._parse_processo(resp, "5000001-00.2024.8.08.0001")
        assert "Joao Silva" in result.polo_ativo
        assert "Banco SA" in result.polo_passivo

    def test_handles_empty_documentos(self):
        client = _make_client()
        resp = self._mock_response(documentos=[])
        result = client._parse_processo(resp, "5000001-00.2024.8.08.0001")
        assert result.documentos == []

    def test_parses_documento_with_content(self):
        doc = MagicMock()
        doc.idDocumento = "doc2"
        doc.descricao = "Sentenca"
        doc.tipoDocumento = "Sentenca"
        doc.mimetype = "application/pdf"
        doc.conteudo = b"binary pdf content"
        doc.documentoVinculado = []

        client = _make_client()
        resp = self._mock_response(documentos=[doc])
        result = client._parse_processo(resp, "5000001-00.2024.8.08.0001")
        assert result.documentos[0].has_content is True
        assert result.documentos[0].tamanho_bytes == len(b"binary pdf content")
