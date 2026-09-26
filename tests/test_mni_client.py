"""Tests for MNI client error classification."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
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
async def test_consultar_processo_403_classified_as_blocked():
    """HTTP 403 from the SOAP endpoint is classified as `blocked` — not
    `auth_failed` (the MNI rejects credentials with a SOAP fault, never 403;
    a bare 403 is CloudFront geo-restriction) and not `error` — and the
    message must not expose the raw URL."""
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
    assert result.status == "blocked"
    assert "403" not in result.error or "Forbidden" in result.error
    # Must not expose raw URL
    assert "pje.tjes.jus.br" not in result.error
    # Must mention the tribunal
    assert "TJES" in result.error
    # Just verify the call completed without raising
    assert result.error  # non-empty user-friendly message


@pytest.mark.asyncio
async def test_consultar_processo_forbidden_string_classified_as_blocked():
    """'Forbidden' in error message (non-requests exception) also maps to `blocked`."""
    client = _make_client()

    with patch.object(client, "_get_client", side_effect=Exception("Forbidden access")):
        result = await client.consultar_processo("5000002-00.2024.8.08.0001")

    assert result.success is False
    assert result.status == "blocked"
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
# forbid_external SSRF hardening
# (docs/specs/2026-09-25-zeep-forbid-external.md)
# ---------------------------------------------------------------------------


class TestForbidExternalSettings:
    """_get_client must pass Settings(forbid_external=...) scoped per tribunal."""

    def test_hardened_tribunal_gets_forbid_external_true(self):
        """TJES is in MNI_FORBID_EXTERNAL_TRIBUNALS (the default hardened set)."""
        client = _make_client()
        assert client.tribunal == "TJES"

        with (
            patch("zeep.Client", return_value=MagicMock()) as mock_client_cls,
            patch("zeep.transports.Transport", return_value=MagicMock()),
            patch("requests.Session", return_value=MagicMock()),
        ):
            client._get_client()

        settings = mock_client_cls.call_args.kwargs["settings"]
        assert settings.forbid_external is True

    def test_unhardened_tribunal_gets_forbid_external_false(self):
        """A tribunal not yet measured (e.g. TJBA) must see zero behavior
        change — forbid_external stays False, matching zeep's own default."""
        from mni_client import MNIClient

        client = MNIClient(tribunal="TJBA", username="u", password="p")

        with (
            patch("zeep.Client", return_value=MagicMock()) as mock_client_cls,
            patch("zeep.transports.Transport", return_value=MagicMock()),
            patch("requests.Session", return_value=MagicMock()),
        ):
            client._get_client()

        settings = mock_client_cls.call_args.kwargs["settings"]
        assert settings.forbid_external is False


class TestForbidExternalSSRFFixture:
    """Offline, deterministic proof (real zeep/lxml, no live network) that
    forbid_external actually closes the SSRF window an external
    schemaLocation opens — not just that the kwarg gets threaded through."""

    FIXTURE = str(
        Path(__file__).parent / "fixtures" / "wsdl_external_schema_location.wsdl"
    )

    class _FetchAttempted(Exception):
        """Raised by the mocked transport in place of a real network call —
        proves the resolver reached the point of fetching the external URL."""

    def test_without_forbid_external_the_fetch_is_attempted(self):
        """Documents the vulnerability window: zeep's own default
        (forbid_external=False) dereferences the external schemaLocation."""
        import requests
        from zeep import Client

        with patch.object(
            requests.Session, "get", side_effect=self._FetchAttempted("fetched")
        ):
            with pytest.raises(self._FetchAttempted):
                Client(wsdl=self.FIXTURE)

    def test_forbid_external_blocks_the_fetch_before_any_network_attempt(self):
        """The load-bearing assertion: with forbid_external=True, zeep must
        refuse the import before session.get is ever called."""
        import requests
        from zeep import Client, Settings
        from zeep.exceptions import ExternalReferenceForbidden

        with patch.object(
            requests.Session, "get", side_effect=self._FetchAttempted("fetched")
        ) as mock_get:
            with pytest.raises(ExternalReferenceForbidden):
                Client(wsdl=self.FIXTURE, settings=Settings(forbid_external=True))

        mock_get.assert_not_called()


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


# ─────────────────────────────────────────────
# Sprint 5B regression — H3: SOAP timeout retry
# ─────────────────────────────────────────────


class TestSoapTimeoutRetry:
    @pytest.mark.asyncio
    async def test_soap_timeout_retried_up_to_three_attempts(self, monkeypatch):
        """BEFORE FIX: asyncio.TimeoutError on the first SOAP attempt immediately
        returned MNIResult(success=False) with no retry. A single transient TCP
        timeout caused the entire processo to be marked failed.

        After fix: AsyncRetry wraps the SOAP call with 3 attempts. A processo that
        times out twice but succeeds on the third attempt completes successfully.
        """
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())

        client = _make_client()
        call_count = 0

        def _call_that_times_out_twice(soap_client, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise asyncio.TimeoutError("transient mock timeout")
            mock_result = MagicMock()
            mock_result.sucesso = True
            mock_result.mensagem = ""
            return mock_result

        mock_soap_client = MagicMock()

        with (
            patch.object(client, "_get_client", return_value=mock_soap_client),
            patch.object(
                client,
                "_call_consultar_processo",
                side_effect=_call_that_times_out_twice,
            ),
            patch.object(
                client, "_parse_processo", return_value=MagicMock(documentos=[])
            ),
        ):
            result = await client.consultar_processo("5000001-00.2024.8.08.0001")

        assert result.success is True, (
            "BEFORE FIX: first timeout → MNIResult(success=False), no retry. "
            "After fix: 3-attempt AsyncRetry → succeeds on attempt 3. "
            f"Got: success={result.success!r}, error={result.error!r}"
        )
        assert call_count == 3, f"Expected exactly 3 SOAP attempts, got {call_count}"


# ---------------------------------------------------------------------------
# verify_credentials() — F3: post-deploy MNI credential smoke test
#
# These tests reuse consultar_processo's OWN classification (MNIResult.status)
# via the same mocking seams as TestConsultarProcesso above — no parallel
# classifier is introduced in production code, so none is exercised here
# either.
# ---------------------------------------------------------------------------


class TestVerifyCredentials:
    @pytest.mark.asyncio
    async def test_not_found_reply_is_valid(self):
        """A 'Processo não encontrado' exception (status=not_found) means the
        server authenticated us — the probed CNJ simply doesn't exist."""
        client = _make_client()
        with patch.object(
            client, "_get_client", side_effect=Exception("Processo não encontrado")
        ):
            outcome = await client.verify_credentials()

        assert outcome["result"] == "valid"
        assert "latency_ms" in outcome
        assert isinstance(outcome["latency_ms"], float)

    @pytest.mark.asyncio
    async def test_body_level_not_found_reply_is_valid(self):
        """sucesso=False business reply (no exception) worded as 'not found'
        also means the server authenticated the request. Uses the REAL
        production TJES wording ('Processo de número X não encontrado!'),
        different word order than the exception branch's literal
        'Processo não encontrado' — proves the narrower 'não encontrado'
        match (not the old 'anything not auth_failed' bucket) still
        recognizes it."""
        client = _make_client()
        soap_resp = _make_soap_response(
            sucesso=False,
            mensagem="Processo de número 00000000000008080000 não encontrado!",
        )

        with (
            patch.object(client, "_get_client", return_value=MagicMock()),
            patch.object(client, "_call_consultar_processo", return_value=soap_resp),
        ):
            outcome = await client.verify_credentials()

        assert outcome["result"] == "valid", outcome

    @pytest.mark.asyncio
    async def test_body_level_unrecognized_rejection_is_inconclusive_not_valid(self):
        """Code-review finding on #46/#47: a body-level rejection worded as
        neither 'não encontrado' nor an auth failure used to fall into the
        bare 'mni_error' bucket, which verify_credentials treated as VALID —
        so a tribunal wording its rejection differently than expected, for
        ANY reason including actually-bad credentials, would misreport as
        valid. It must be inconclusive instead: this synthetic, deliberately-
        nonexistent CNJ has exactly one expected legitimate rejection
        ('not found'); anything else worded differently is unknown, not
        confirmed-safe."""
        client = _make_client()
        soap_resp = _make_soap_response(
            sucesso=False, mensagem="Erro interno do servidor"
        )

        with (
            patch.object(client, "_get_client", return_value=MagicMock()),
            patch.object(client, "_call_consultar_processo", return_value=soap_resp),
        ):
            outcome = await client.verify_credentials()

        assert outcome["result"] == "inconclusive", outcome

    @pytest.mark.asyncio
    async def test_body_level_acesso_negado_is_invalid(self):
        """Review of #47: a credential rejection can travel in the BODY
        (sucesso=false + mensagem="Acesso negado") instead of as a SOAP fault.
        The fault branch already classified that text as auth_failed; the body
        branch mapped everything to mni_error, which the probe reads as
        "valid" — so a deploy with dead credentials would have passed."""
        client = _make_client()
        soap_resp = _make_soap_response(sucesso=False, mensagem="Acesso negado")

        with (
            patch.object(client, "_get_client", return_value=MagicMock()),
            patch.object(client, "_call_consultar_processo", return_value=soap_resp),
        ):
            outcome = await client.verify_credentials()

        assert outcome["result"] == "invalid", outcome
        assert "Acesso negado" in outcome["reason"]

    # Wording measured live against TJES on 2026-09-26 with a deliberately
    # fake CPF/password: the rejection arrives in the BODY (sucesso=false) as
    # this exact text. Before this fix it read as "inconclusive", which the
    # deploy step treats as a warning, so dead credentials still deployed.
    TJES_LOGIN_FAILED = (
        "Erro ao realizar login via MNI. exception invoking: loginFailed"
    )

    @pytest.mark.asyncio
    async def test_body_level_tjes_login_failed_is_invalid(self):
        client = _make_client()
        soap_resp = _make_soap_response(sucesso=False, mensagem=self.TJES_LOGIN_FAILED)

        with (
            patch.object(client, "_get_client", return_value=MagicMock()),
            patch.object(client, "_call_consultar_processo", return_value=soap_resp),
        ):
            outcome = await client.verify_credentials()

        assert outcome["result"] == "invalid", outcome

    @pytest.mark.asyncio
    async def test_fault_level_tjes_login_failed_is_invalid(self):
        client = _make_client()
        with patch.object(
            client, "_get_client", side_effect=Exception(self.TJES_LOGIN_FAILED)
        ):
            outcome = await client.verify_credentials()

        assert outcome["result"] == "invalid", outcome

    @pytest.mark.asyncio
    async def test_success_reply_is_valid(self):
        """The extremely unlikely case where the dummy CNJ resolves is still
        a 'valid' credential signal."""
        client = _make_client()
        soap_resp = _make_soap_response(sucesso=True)

        with (
            patch.object(client, "_get_client", return_value=MagicMock()),
            patch.object(client, "_call_consultar_processo", return_value=soap_resp),
        ):
            outcome = await client.verify_credentials()

        assert outcome["result"] == "valid"

    @pytest.mark.asyncio
    async def test_acesso_negado_is_invalid(self):
        """'Acesso negado' exception (status=auth_failed) → invalid."""
        client = _make_client()
        with patch.object(
            client, "_get_client", side_effect=Exception("Acesso negado")
        ):
            outcome = await client.verify_credentials()

        assert outcome["result"] == "invalid"
        assert outcome["reason"]

    @pytest.mark.asyncio
    async def test_403_forbidden_is_inconclusive_not_invalid(self):
        """A bare HTTP 403 is NOT a credential verdict.

        The MNI rejects credentials with a SOAP fault ("Acesso negado"); a 403
        arrives before any SOAP envelope, and the documented source of it in
        this deployment is CloudFront's geo-restriction (2026-07-18 incident:
        non-BR IP -> 403 from POP BOS50). Reporting it as "invalid" would fail
        a deploy with "credentials rejected" during a geo incident and send
        the operator after the wrong cause. The probe must say inconclusive
        and name the likely cause.
        """
        client = _make_client()
        with patch.object(
            client, "_get_client", side_effect=Exception("403 Forbidden")
        ):
            outcome = await client.verify_credentials()

        assert outcome["result"] == "inconclusive"
        assert "not a credential verdict" in outcome["reason"]
        assert "geo" in outcome["reason"]

    @pytest.mark.asyncio
    async def test_timeout_is_inconclusive(self):
        client = _make_client()
        with (
            patch.object(client, "_get_client", return_value=MagicMock()),
            patch.object(
                client, "_call_consultar_processo", side_effect=asyncio.TimeoutError()
            ),
        ):
            outcome = await client.verify_credentials()

        assert outcome["result"] == "inconclusive"
        assert "timeout" in outcome["reason"].lower()

    @pytest.mark.asyncio
    async def test_generic_transport_error_is_inconclusive(self):
        """An unrecognized exception message (e.g. connection refused) must
        NOT be classified as invalid — that would fail a deploy on a network
        blip. It must fall through to consultar_processo's generic 'error'
        status, which verify_credentials treats as inconclusive."""
        client = _make_client()
        with patch.object(
            client, "_get_client", side_effect=Exception("Connection refused")
        ):
            outcome = await client.verify_credentials()

        assert outcome["result"] == "inconclusive"

    @pytest.mark.asyncio
    async def test_unexpected_parse_fault_is_inconclusive(self):
        """A malformed-but-authenticated SOAP reply (parse_error) is an
        unexpected fault, not proof of bad credentials."""
        client = _make_client()
        soap_resp = _make_soap_response(sucesso=True)

        with (
            patch.object(client, "_get_client", return_value=MagicMock()),
            patch.object(client, "_call_consultar_processo", return_value=soap_resp),
            patch.object(client, "_parse_processo", side_effect=Exception("boom")),
        ):
            outcome = await client.verify_credentials()

        assert outcome["result"] == "inconclusive"

    @pytest.mark.asyncio
    async def test_password_never_in_returned_dict(self):
        client = _make_client()
        client.password = "SUPER_SECRET_PW"
        with patch.object(
            client, "_get_client", side_effect=Exception("Acesso negado")
        ):
            outcome = await client.verify_credentials()

        assert "SUPER_SECRET_PW" not in json.dumps(outcome)

    @pytest.mark.asyncio
    async def test_password_never_logged(self, monkeypatch):
        """structlog output in this codebase is NOT bridged to stdlib
        logging (no structlog.configure() at module scope in mni_client.py),
        so `caplog` cannot observe it here — verified directly: a bare
        `structlog.get_logger().info(...)` in this environment produces zero
        `caplog.records`. Spy on the module logger instead."""
        client = _make_client()
        client.password = "SUPER_SECRET_PW"
        spy_log = MagicMock()
        monkeypatch.setattr("mni_client.log", spy_log)

        with patch.object(
            client, "_get_client", side_effect=Exception("Acesso negado")
        ):
            await client.verify_credentials()

        for call in spy_log.mock_calls:
            assert "SUPER_SECRET_PW" not in str(call)

    @pytest.mark.asyncio
    async def test_uses_syntactically_valid_but_nonexistent_cnj(self):
        """The probe number must match config.CNJ_PATTERN (so the tribunal
        doesn't reject it as malformed input) but must not be a real case
        number — this repo redacts real CNJs everywhere."""
        import config
        import mni_client as _mni_client_mod

        assert config.is_valid_processo(_mni_client_mod._MNI_VERIFY_TEST_PROCESSO)

        client = _make_client()
        with (
            patch.object(client, "_get_client", return_value=MagicMock()),
            patch.object(
                client, "_call_consultar_processo", return_value=_make_soap_response()
            ) as mock_call,
        ):
            await client.verify_credentials()

        # _call_consultar_processo(client, numero_processo, incluir_documentos,
        # incluir_cabecalho, incluir_movimentacoes, documento_ids) — index 1
        # is the CNJ number.
        called_numero = mock_call.call_args[0][1]
        assert called_numero == _mni_client_mod._MNI_VERIFY_TEST_PROCESSO


# ---------------------------------------------------------------------------
# tools/verify_mni_credentials.py — CLI exit-code mapping
# ---------------------------------------------------------------------------


class TestVerifyMniCredentialsCli:
    def _fake_client(self, outcome: dict, tribunal: str = "TJES"):
        fake = MagicMock()
        fake.tribunal = tribunal
        fake.verify_credentials = AsyncMock(return_value=outcome)
        return fake

    def test_exit_0_on_valid(self, capsys):
        from tools.verify_mni_credentials import main

        fake = self._fake_client(
            {"result": "valid", "reason": "ok", "latency_ms": 12.3}
        )
        with (
            patch("mni_client.MNIClient", return_value=fake),
            patch("config.load_env"),
        ):
            code = main()

        assert code == 0
        assert "VALID" in capsys.readouterr().out

    def test_exit_1_on_invalid(self, capsys):
        from tools.verify_mni_credentials import main

        fake = self._fake_client(
            {"result": "invalid", "reason": "auth rejected", "latency_ms": 12.3}
        )
        with (
            patch("mni_client.MNIClient", return_value=fake),
            patch("config.load_env"),
        ):
            code = main()

        assert code == 1
        assert "INVALID" in capsys.readouterr().out

    def test_exit_2_on_inconclusive(self, capsys):
        from tools.verify_mni_credentials import main

        fake = self._fake_client(
            {"result": "inconclusive", "reason": "timeout", "latency_ms": 12.3}
        )
        with (
            patch("mni_client.MNIClient", return_value=fake),
            patch("config.load_env"),
        ):
            code = main()

        assert code == 2
        assert "INCONCLUSIVE" in capsys.readouterr().out

    def test_exit_2_on_construction_error(self, capsys):
        """An unsupported MNI_TRIBUNAL (or any construction failure) must
        not crash the deploy step — it maps to inconclusive, not invalid."""
        from tools.verify_mni_credentials import main

        with (
            patch("mni_client.MNIClient", side_effect=ValueError("bad tribunal")),
            patch("config.load_env"),
        ):
            code = main()

        assert code == 2
        assert "inconclusive" in capsys.readouterr().out.lower()

    def test_exit_2_on_unexpected_exception_from_verify(self, capsys):
        from tools.verify_mni_credentials import main

        fake = MagicMock()
        fake.tribunal = "TJES"
        fake.verify_credentials = AsyncMock(side_effect=RuntimeError("boom"))
        with (
            patch("mni_client.MNIClient", return_value=fake),
            patch("config.load_env"),
        ):
            code = main()

        assert code == 2
        assert "inconclusive" in capsys.readouterr().out.lower()
