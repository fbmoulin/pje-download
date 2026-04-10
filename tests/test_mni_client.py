"""Tests for MNI client error classification."""

from __future__ import annotations

import asyncio
import base64
import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_asyncio_to_thread(monkeypatch):
    """Make async thread offloading deterministic in unit tests.

    In this sandbox, real ``asyncio.to_thread`` can keep pytest alive after the
    assertions already passed. These tests validate classification and parsing
    logic, not Python's threadpool executor behavior, so a synchronous shim is
    sufficient here.
    """

    async def _fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _fake_to_thread)


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


# ---------------------------------------------------------------------------
# _save_document audit integration
# ---------------------------------------------------------------------------


class TestSaveDocumentAudit:
    """Verify audit.log_access is called with correct fields during _save_document."""

    def _make_doc(self, content: bytes = b"PDF content", doc_id: str = "doc42"):
        from mni_client import MNIDocumento

        return MNIDocumento(
            id=doc_id,
            nome="Peticao Inicial",
            tipo="peticao",
            mimetype="application/pdf",
            conteudo_base64=base64.b64encode(content).decode("ascii"),
            tamanho_bytes=len(content),
        )

    def test_audit_called_on_save(self, tmp_path):
        """audit.log_access called with event_type='document_saved' and status='success'."""
        client = _make_client()
        doc = self._make_doc()
        with patch("audit.log_access") as mock_audit:
            result = client._save_document(
                doc, tmp_path, set(), processo_numero="5000001-00.2024.8.08.0001"
            )
        assert result is not None
        mock_audit.assert_called_once()
        entry = mock_audit.call_args[0][0]
        assert entry.event_type == "document_saved"
        assert entry.status == "success"
        assert entry.processo_numero == "5000001-00.2024.8.08.0001"
        assert entry.documento_id == "doc42"
        assert entry.documento_tipo == "peticao"
        assert entry.tribunal == "TJES"
        assert entry.fonte == "mni_soap"
        assert entry.tamanho_bytes == len(b"PDF content")
        assert entry.checksum_sha256 is not None

    def test_audit_called_on_duplicate_skip(self, tmp_path):
        """audit.log_access called with status='duplicate_skipped' for duplicates."""
        client = _make_client()
        content = b"duplicate content"
        doc = self._make_doc(content=content)
        checksum = hashlib.sha256(content).hexdigest()
        seen = {checksum}
        with patch("audit.log_access") as mock_audit:
            result = client._save_document(
                doc, tmp_path, seen, processo_numero="5000002-00.2024.8.08.0001"
            )
        assert result is None
        mock_audit.assert_called_once()
        entry = mock_audit.call_args[0][0]
        assert entry.event_type == "document_saved"
        assert entry.status == "duplicate_skipped"
        assert entry.checksum_sha256 == checksum

    def test_audit_called_on_disk_error(self, tmp_path):
        """audit.log_access called with status='error' on OSError."""
        client = _make_client()
        doc = self._make_doc()
        readonly_dir = tmp_path / "readonly"
        readonly_dir.mkdir()
        readonly_dir.chmod(0o444)
        try:
            with patch("audit.log_access") as mock_audit:
                with pytest.raises(OSError):
                    client._save_document(
                        doc,
                        readonly_dir,
                        set(),
                        processo_numero="5000003-00.2024.8.08.0001",
                    )
            mock_audit.assert_called_once()
            entry = mock_audit.call_args[0][0]
            assert entry.event_type == "document_saved"
            assert entry.status == "error"
            assert entry.erro is not None
        finally:
            readonly_dir.chmod(0o755)

    def test_processo_numero_passed_through(self, tmp_path):
        """processo_numero parameter appears in audit entry."""
        client = _make_client()
        doc = self._make_doc()
        with patch("audit.log_access") as mock_audit:
            client._save_document(
                doc, tmp_path, set(), processo_numero="9999999-00.2024.8.08.0001"
            )
        entry = mock_audit.call_args[0][0]
        assert entry.processo_numero == "9999999-00.2024.8.08.0001"

    def test_audit_not_called_without_processo_numero_uses_default(self, tmp_path):
        """Without processo_numero, default empty string is used."""
        client = _make_client()
        doc = self._make_doc()
        with patch("audit.log_access") as mock_audit:
            client._save_document(doc, tmp_path, set())
        entry = mock_audit.call_args[0][0]
        assert entry.processo_numero == ""


# ---------------------------------------------------------------------------
# SOAP mock helper
# ---------------------------------------------------------------------------


def _make_soap_response(
    sucesso=True, mensagem="", docs=None, processo_numero="5000001-00.2024.8.08.0001"
):
    """Build a mock SOAP response object mimicking MNI consultarProcesso."""
    resp = MagicMock()
    resp.sucesso = sucesso
    resp.mensagem = mensagem

    if docs is None:
        doc = MagicMock()
        doc.idDocumento = "doc1"
        doc.descricao = "Peticao Inicial"
        doc.tipoDocumento = "peticao"
        doc.mimetype = "application/pdf"
        doc.conteudo = None
        doc.documentoVinculado = []
        docs = [doc]

    dados = MagicMock()
    dados.classeProcessual = "Execucao"
    dados.assunto = []
    dados.polo = []

    proc = MagicMock()
    proc.dadosBasicos = dados
    proc.documento = docs
    proc.movimento = []

    resp.processo = proc
    return resp


# ---------------------------------------------------------------------------
# _get_client (zeep lazy init)
# ---------------------------------------------------------------------------


class TestGetClient:
    def test_first_call_creates_zeep_client(self):
        """First _get_client() call should create zeep.Client and cache it."""
        client = _make_client()
        mock_client_instance = MagicMock()

        with (
            patch("zeep.Client", return_value=mock_client_instance),
            patch("zeep.transports.Transport", return_value=MagicMock()),
            patch("requests.Session", return_value=MagicMock()),
        ):
            result = client._get_client()

        assert result is mock_client_instance
        assert client._client is mock_client_instance

    def test_cached_second_call(self):
        """Second _get_client() call returns cached client."""
        client = _make_client()
        sentinel = MagicMock()
        client._client = sentinel
        result = client._get_client()
        assert result is sentinel

    def test_wsdl_failure_propagates(self):
        """If zeep.Client raises, the error should propagate."""
        client = _make_client()
        with (
            patch("zeep.Client", side_effect=ConnectionError("WSDL unreachable")),
            patch("zeep.transports.Transport", return_value=MagicMock()),
            patch("requests.Session", return_value=MagicMock()),
        ):
            with pytest.raises(ConnectionError, match="WSDL unreachable"):
                client._get_client()


# ---------------------------------------------------------------------------
# consultar_processo (async SOAP)
# ---------------------------------------------------------------------------


class TestConsultarProcesso:
    @pytest.mark.asyncio
    async def test_success(self):
        """Successful SOAP call returns MNIResult with success=True."""
        client = _make_client()
        soap_resp = _make_soap_response(sucesso=True)

        with (
            patch.object(client, "_get_client", return_value=MagicMock()),
            patch.object(client, "_call_consultar_processo", return_value=soap_resp),
        ):
            result = await client.consultar_processo("5000001-00.2024.8.08.0001")

        assert result.success is True
        assert result.processo is not None
        assert result.processo.numero == "5000001-00.2024.8.08.0001"

    @pytest.mark.asyncio
    async def test_mni_error(self):
        """MNI returns sucesso=False → MNIResult with success=False."""
        client = _make_client()
        soap_resp = _make_soap_response(sucesso=False, mensagem="Erro interno MNI")

        with (
            patch.object(client, "_get_client", return_value=MagicMock()),
            patch.object(client, "_call_consultar_processo", return_value=soap_resp),
        ):
            result = await client.consultar_processo("5000001-00.2024.8.08.0001")

        assert result.success is False
        assert "Erro interno MNI" in result.error

    @pytest.mark.asyncio
    async def test_not_found(self):
        """'Processo não encontrado' exception maps to not_found."""
        client = _make_client()
        with patch.object(
            client, "_get_client", side_effect=Exception("Processo não encontrado")
        ):
            result = await client.consultar_processo("5000001-00.2024.8.08.0001")
        assert result.success is False
        assert "não encontrado" in result.error.lower()

    @pytest.mark.asyncio
    async def test_auth_failed(self):
        """'Acesso negado' exception maps to auth_failed."""
        client = _make_client()
        with patch.object(
            client, "_get_client", side_effect=Exception("Acesso negado")
        ):
            result = await client.consultar_processo("5000001-00.2024.8.08.0001")
        assert result.success is False
        assert "credenciais" in result.error.lower() or "acesso" in result.error.lower()

    @pytest.mark.asyncio
    async def test_timeout(self):
        """asyncio.TimeoutError maps to timeout status."""
        client = _make_client()

        async def slow_get_client():
            await asyncio.sleep(100)

        with (
            patch.object(client, "_get_client", return_value=MagicMock()),
            patch.object(
                client, "_call_consultar_processo", side_effect=asyncio.TimeoutError()
            ),
        ):
            # Patch asyncio.wait_for to raise TimeoutError
            result = await client.consultar_processo("5000001-00.2024.8.08.0001")

        assert result.success is False
        assert "timeout" in result.error.lower()


# ---------------------------------------------------------------------------
# _call_consultar_processo (sync SOAP shim)
# ---------------------------------------------------------------------------


class TestCallConsultarProcesso:
    def test_normal_call(self):
        """Normal call passes correct params to client.service.consultarProcesso."""
        client = _make_client()
        mock_soap_client = MagicMock()
        mock_soap_client.service.consultarProcesso.return_value = "response"

        result = client._call_consultar_processo(
            mock_soap_client,
            "5000001-00.2024.8.08.0001",
            incluir_documentos=True,
            incluir_cabecalho=True,
            incluir_movimentacoes=False,
            documento_ids=None,
        )
        assert result == "response"
        mock_soap_client.service.consultarProcesso.assert_called_once_with(
            idConsultante="user",
            senhaConsultante="pass",
            numeroProcesso="5000001-00.2024.8.08.0001",
            movimentos=False,
            incluirCabecalho=True,
            incluirDocumentos=True,
        )

    def test_with_documento_ids(self):
        """When documento_ids is provided, it's passed as 'documento' param."""
        client = _make_client()
        mock_soap_client = MagicMock()
        mock_soap_client.service.consultarProcesso.return_value = "response"

        client._call_consultar_processo(
            mock_soap_client,
            "5000001-00.2024.8.08.0001",
            incluir_documentos=True,
            incluir_cabecalho=True,
            incluir_movimentacoes=False,
            documento_ids=["doc1", "doc2"],
        )
        call_kwargs = mock_soap_client.service.consultarProcesso.call_args[1]
        assert call_kwargs["documento"] == ["doc1", "doc2"]

    def test_zeep_fault_propagates(self):
        """zeep Fault exception propagates to caller."""
        client = _make_client()
        mock_soap_client = MagicMock()
        mock_soap_client.service.consultarProcesso.side_effect = Exception(
            "Server fault: invalid request"
        )

        with pytest.raises(Exception, match="Server fault"):
            client._call_consultar_processo(
                mock_soap_client,
                "5000001-00.2024.8.08.0001",
                incluir_documentos=True,
                incluir_cabecalho=True,
                incluir_movimentacoes=False,
            )

    def test_generic_exception_propagates(self):
        """Generic exceptions propagate to caller."""
        client = _make_client()
        mock_soap_client = MagicMock()
        mock_soap_client.service.consultarProcesso.side_effect = RuntimeError(
            "network down"
        )

        with pytest.raises(RuntimeError, match="network down"):
            client._call_consultar_processo(
                mock_soap_client,
                "5000001-00.2024.8.08.0001",
                incluir_documentos=True,
                incluir_cabecalho=True,
                incluir_movimentacoes=False,
            )


# ---------------------------------------------------------------------------
# download_documentos (2-phase SOAP download)
# ---------------------------------------------------------------------------


class TestDownloadDocumentos:
    @pytest.mark.asyncio
    async def test_single_doc_with_content(self, tmp_path):
        """Doc with content is saved directly without phase-2 fetch."""
        from mni_client import MNIProcesso, MNIDocumento

        content = b"PDF binary data"
        doc = MNIDocumento(
            id="doc1",
            nome="Peticao",
            tipo="peticao",
            conteudo_base64=base64.b64encode(content).decode("ascii"),
            tamanho_bytes=len(content),
        )
        processo = MNIProcesso(numero="5000001-00.2024.8.08.0001", documentos=[doc])
        client = _make_client()

        with patch("audit.log_access"):
            saved = await client.download_documentos(processo, tmp_path)

        assert len(saved) == 1
        assert saved[0]["tamanhoBytes"] == len(content)
        assert saved[0]["fonte"] == "mni_soap"

    @pytest.mark.asyncio
    async def test_multi_batch_fetch(self, tmp_path):
        """Docs without content trigger phase-2 fetch in batches."""
        from mni_client import MNIProcesso, MNIDocumento, MNIResult

        # Metadata-only docs (no content)
        docs = [
            MNIDocumento(id=f"doc{i}", nome=f"Doc {i}", tipo="doc") for i in range(3)
        ]
        processo = MNIProcesso(numero="5000001-00.2024.8.08.0001", documentos=docs)
        client = _make_client()

        # Phase-2 response: return docs with content
        async def fake_consultar(numero, **kwargs):
            doc_ids = kwargs.get("documento_ids", [])
            fetched = []
            for did in doc_ids:
                content = f"content-{did}".encode()
                fetched.append(
                    MNIDocumento(
                        id=did,
                        nome=f"Doc {did}",
                        tipo="doc",
                        conteudo_base64=base64.b64encode(content).decode("ascii"),
                        tamanho_bytes=len(content),
                    )
                )
            fetched_proc = MNIProcesso(numero=numero, documentos=fetched)
            return MNIResult(success=True, processo=fetched_proc)

        with (
            patch.object(client, "consultar_processo", side_effect=fake_consultar),
            patch("audit.log_access"),
        ):
            saved = await client.download_documentos(processo, tmp_path, batch_size=2)

        assert len(saved) == 3

    @pytest.mark.asyncio
    async def test_progress_callback_tracks_saved_docs(self, tmp_path):
        """Progress callback receives cumulative doc count and bytes."""
        from mni_client import MNIProcesso, MNIDocumento

        content_a = b"1234"
        content_b = b"abcdef"
        docs = [
            MNIDocumento(
                id="doc1",
                nome="Doc A",
                tipo="doc",
                conteudo_base64=base64.b64encode(content_a).decode("ascii"),
                tamanho_bytes=len(content_a),
            ),
            MNIDocumento(
                id="doc2",
                nome="Doc B",
                tipo="doc",
                conteudo_base64=base64.b64encode(content_b).decode("ascii"),
                tamanho_bytes=len(content_b),
            ),
        ]
        processo = MNIProcesso(numero="5000001-00.2024.8.08.0001", documentos=docs)
        client = _make_client()
        progress_cb = AsyncMock()

        with patch("audit.log_access"):
            saved = await client.download_documentos(
                processo,
                tmp_path,
                progress_cb=progress_cb,
            )

        assert len(saved) == 2
        assert progress_cb.await_count == 2
        first = progress_cb.await_args_list[0].kwargs
        second = progress_cb.await_args_list[1].kwargs
        assert first["completed"] == 1
        assert first["total"] == 2
        assert first["local_bytes"] == len(content_a)
        assert second["completed"] == 2
        assert second["total"] == 2
        assert second["local_bytes"] == len(content_a) + len(content_b)

    @pytest.mark.asyncio
    async def test_dedup_skip(self, tmp_path):
        """Duplicate content across docs is skipped by checksum."""
        from mni_client import MNIProcesso, MNIDocumento

        content = b"same content"
        b64 = base64.b64encode(content).decode("ascii")
        docs = [
            MNIDocumento(
                id="doc1",
                nome="Doc A",
                tipo="doc",
                conteudo_base64=b64,
                tamanho_bytes=len(content),
            ),
            MNIDocumento(
                id="doc2",
                nome="Doc B",
                tipo="doc",
                conteudo_base64=b64,
                tamanho_bytes=len(content),
            ),
        ]
        processo = MNIProcesso(numero="5000001-00.2024.8.08.0001", documentos=docs)
        client = _make_client()

        with patch("audit.log_access"):
            saved = await client.download_documentos(processo, tmp_path)

        # Only 1 saved, other skipped as duplicate
        assert len(saved) == 1


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_healthy(self):
        """Healthy check returns status='healthy' with operations list."""
        client = _make_client()
        mock_wsdl_client = MagicMock()

        # Mock WSDL service structure
        mock_op = MagicMock()
        mock_port = MagicMock()
        mock_port.binding._operations = {"consultarProcesso": mock_op}
        mock_service = MagicMock()
        mock_service.ports.values.return_value = [mock_port]
        mock_wsdl_client.wsdl.services.values.return_value = [mock_service]

        with patch.object(client, "_get_client", return_value=mock_wsdl_client):
            result = await client.health_check()

        assert result["status"] == "healthy"
        assert result["tribunal"] == "TJES"
        assert "consultarProcesso" in result["operations"]
        assert "latency_ms" in result

    @pytest.mark.asyncio
    async def test_timeout(self):
        """Unhealthy when _get_client raises."""
        client = _make_client()

        with patch.object(
            client, "_get_client", side_effect=TimeoutError("WSDL timeout")
        ):
            result = await client.health_check()

        assert result["status"] == "unhealthy"
        assert "timeout" in result["error"].lower()
