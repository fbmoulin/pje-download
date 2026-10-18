"""Offline regressions for transport decoding, content guards and atomic publication."""

import base64
import errno
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from tests.pdf_fixtures import minimal_pdf

PDF = minimal_pdf()
LOGIN = b'<html><form action="/login"><input type="password"></form></html>'


@pytest.mark.parametrize("payload", ["!!!!", "YWJj=bad", "YQ", "\u00a0YQ=="])
def test_strict_base64_rejects_corrupt_transport(payload):
    from document_validation import decode_document_base64

    with pytest.raises(ValueError):
        decode_document_base64(payload)


def test_base64_accepts_only_transport_ascii_whitespace():
    from document_validation import decode_document_base64

    assert decode_document_base64(" YQ==\r\n\t\v\f") == b"a"


@pytest.mark.parametrize("payload", [b"", LOGIN, b"not a PDF", b"%PDF-1.4\ntruncated"])
def test_mni_rejects_invalid_pdf_without_file_or_checksum(tmp_path, payload):
    from mni_client import MNIClient, MNIDocumento

    client = MNIClient(username="synthetic", password="synthetic", tribunal="TJES")
    doc = MNIDocumento(
        id="synthetic",
        nome="peca",
        tipo="doc",
        conteudo_base64=base64.b64encode(payload).decode(),
    )
    seen = set()
    outcome = {}
    with patch("audit.log_document_saved") as audit:
        assert client._save_document(doc, tmp_path, seen, outcome=outcome) is None
    assert outcome["status"] == "failed"
    assert not seen
    assert list(tmp_path.iterdir()) == []
    assert audit.call_args.kwargs["status"] == "error"


def test_mni_legitimate_html_and_document_identity(tmp_path):
    from mni_client import MNIClient, MNIDocumento

    client = MNIClient(username="synthetic", password="synthetic", tribunal="TJES")
    content = b"<html><body><h1>Decisao</h1><p>Defiro o pedido.</p></body></html>"
    doc = MNIDocumento(
        id="child",
        id_pai="parent",
        nome="decisao",
        tipo="decisao",
        mimetype="text/html",
        conteudo_base64=base64.b64encode(content).decode(),
    )
    result = client._save_document(doc, tmp_path, set())
    assert result["documentoId"] == "child"
    assert result["documentoPaiId"] == "parent"
    assert result["mimeType"] == "text/html"
    assert result["documentos"][0]["parentId"] == "parent"


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [b"", LOGIN, b"%PDF-1.4\ntruncated"])
async def test_rest_rejects_false_pdf(tmp_path, payload):
    from worker import PJeSessionWorker

    worker = PJeSessionWorker()
    response = AsyncMock(status=200)
    response.headers = {"content-type": "application/pdf"}
    response.body.return_value = payload
    worker.page = AsyncMock()
    worker.page.request.get.return_value = response
    with patch.object(worker, "_audit_document_saved") as audit:
        assert (
            await worker._download_document_api(
                {"id": "doc", "nome": "doc.pdf"}, tmp_path, "synthetic"
            )
            is None
        )
    assert not list(tmp_path.iterdir())
    assert audit.call_args.kwargs["status"] == "error"


def test_atomic_failure_preserves_existing_and_does_not_mark_seen(tmp_path):
    from file_utils import DiskWriteError
    from mni_client import MNIClient, MNIDocumento

    client = MNIClient(username="synthetic", password="synthetic", tribunal="TJES")
    doc = MNIDocumento(
        id="doc",
        nome="peca",
        tipo="doc",
        conteudo_base64=base64.b64encode(PDF).decode(),
    )
    dest = tmp_path / "peca_doc.pdf"
    dest.write_bytes(b"old")
    seen = set()
    with patch("os.replace", side_effect=OSError(errno.ENOSPC, "No space")):
        with pytest.raises(DiskWriteError):
            client._save_document(doc, tmp_path, seen)
    assert dest.read_bytes() == b"old"
    assert list(tmp_path.iterdir()) == [dest]
    assert not seen


def test_pdf_guard_rejects_trailing_non_whitespace():
    from document_validation import validate_document_content

    with pytest.raises(ValueError):
        validate_document_content(PDF + b"garbage", "application/pdf", "doc.pdf")


def test_pdf_guard_accepts_format_identifiers_without_claiming_full_parse():
    from document_validation import validate_document_content

    validate_document_content(PDF, "application/pdf", "doc.pdf")


def test_atomic_write_leaves_no_temp_after_partial_write(tmp_path):
    from document_validation import atomic_write_document
    from file_utils import DiskWriteError

    def fail_after_write(path, data):
        with path.open("wb") as stream:
            stream.write(data[:3])
        raise OSError(errno.ENOSPC, "No space")

    dest = tmp_path / "document.pdf"
    with patch.object(Path, "write_bytes", fail_after_write):
        with pytest.raises(DiskWriteError):
            atomic_write_document(dest, PDF)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("payload", ["!!!!", "YQ", "YWJj=bad"])
def test_mni_base64_corruption_is_audited(tmp_path, payload):
    from mni_client import MNIClient, MNIDocumento

    client = MNIClient(username="synthetic", password="synthetic", tribunal="TJES")
    doc = MNIDocumento(id="doc", nome="peca", tipo="doc", conteudo_base64=payload)
    with patch("audit.log_document_saved") as audit:
        assert client._save_document(doc, tmp_path, set()) is None
    assert audit.call_args.kwargs["status"] == "error"
    assert not list(tmp_path.iterdir())


def test_mni_transport_whitespace_and_duplicate_outcome(tmp_path):
    from mni_client import MNIClient, MNIDocumento

    client = MNIClient(username="synthetic", password="synthetic", tribunal="TJES")
    encoded = base64.b64encode(PDF).decode()
    doc = MNIDocumento(
        id="doc",
        nome="peca",
        tipo="doc",
        conteudo_base64="\r\n".join(
            encoded[i : i + 20] for i in range(0, len(encoded), 20)
        ),
    )
    seen = set()
    assert client._save_document(doc, tmp_path, seen) is not None
    outcome = {}
    assert client._save_document(doc, tmp_path, seen, outcome=outcome) is None
    assert outcome["status"] == "duplicate"
    assert outcome["checksum"] in seen
    assert outcome["documento"]["id"] == "doc"


@pytest.mark.asyncio
async def test_rest_legitimate_html_metadata(tmp_path):
    from worker import PJeSessionWorker

    worker = PJeSessionWorker()
    content = b"<html><body><h1>Decisao</h1><p>Defiro.</p></body></html>"
    response = AsyncMock(status=200)
    response.headers = {"content-type": "text/html;charset=utf-8"}
    response.body.return_value = content
    worker.page = AsyncMock()
    worker.page.request.get.return_value = response
    result = await worker._download_document_api(
        {"id": 7, "id_pai": 6, "nome": "decisao.html", "tipo": "decisao"},
        tmp_path,
        "synthetic",
    )
    assert result["documentoId"] == "7"
    assert result["documentoPaiId"] == "6"
    assert result["documentos"][0]["parentId"] == "6"
    assert Path(result["localPath"]).read_bytes() == content


@pytest.mark.asyncio
async def test_rest_disk_error_is_audited_and_propagates(tmp_path):
    from file_utils import DiskWriteError
    from worker import PJeSessionWorker

    worker = PJeSessionWorker()
    response = AsyncMock(status=200)
    response.headers = {"content-type": "application/pdf"}
    response.body.return_value = PDF
    worker.page = AsyncMock()
    worker.page.request.get.return_value = response
    with (
        patch("os.replace", side_effect=OSError(errno.ENOSPC, "No space")),
        patch.object(worker, "_audit_document_saved") as audit,
    ):
        with pytest.raises(DiskWriteError):
            await worker._download_document_api(
                {"id": "doc", "nome": "doc.pdf"}, tmp_path, "synthetic"
            )
    assert audit.call_args.kwargs["status"] == "error"
    assert not list(tmp_path.iterdir())
