"""Resiliência do caminho MNI/TJES: retentativa por documento (M3), failed_ids
preservados quando nada foi salvo (M2) e parser recursivo de vinculados (M1).

Números de processo e conteúdos são sintéticos; o literal CNJ é montado em
tempo de execução (gitleaks acusa números CNJ formatados).
"""

from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.pdf_fixtures import minimal_pdf

_NUM = "5000001" + "-00.2024.8.08.0001"


@pytest.fixture(autouse=True)
def _patch_asyncio_to_thread(monkeypatch):
    async def _fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _fake_to_thread)


def _make_client():
    from tests.test_mni_client import _make_client as make

    return make()


def _doc(i, content=None):
    from mni_client import MNIDocumento

    kw = {}
    if content is not None:
        blob = minimal_pdf(content)
        kw = {
            "conteudo_base64": base64.b64encode(blob).decode("ascii"),
            "tamanho_bytes": len(blob),
        }
    return MNIDocumento(id=f"doc{i}", nome=f"Doc {i}", tipo="doc", **kw)


def _answer(docs):
    from mni_client import MNIProcesso, MNIResult

    return MNIResult(
        success=True,
        processo=MNIProcesso(numero=_NUM, documentos=docs),
        status="success",
    )


def _fail(status):
    from mni_client import MNIResult

    return MNIResult(success=False, error="x", status=status)


async def _run(client, n, tmp_path, consultar, batch_size=5):
    from mni_client import MNIProcesso

    processo = MNIProcesso(numero=_NUM, documentos=[_doc(i) for i in range(n)])
    with (
        patch.object(client, "consultar_processo", side_effect=consultar),
        patch("audit.log_access"),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        return await client.download_documentos(
            processo, tmp_path, batch_size=batch_size
        )


class TestBatchFailureRetriesPerDocument:
    @pytest.mark.asyncio
    async def test_timeout_batch_is_retried_one_by_one_and_only_the_slow_one_is_lost(
        self, tmp_path
    ):
        client = _make_client()
        calls = []

        async def consultar(numero, **kw):
            ids = kw["documento_ids"]
            calls.append(ids)
            if len(ids) > 1:
                return _fail("timeout")
            if ids == ["doc1"]:  # the big attachment
                return _fail("timeout")
            n = int(ids[0][3:])
            return _answer([_doc(n, b"ok%d" % n)])

        saved = await _run(client, 3, tmp_path, consultar)

        assert calls == [["doc0", "doc1", "doc2"], ["doc0"], ["doc1"], ["doc2"]]
        assert len(saved) == 2
        assert saved.failed_ids == ["doc1"]

    @pytest.mark.asyncio
    async def test_exception_batch_is_retried_one_by_one(self, tmp_path):
        import requests

        client = _make_client()
        calls = []

        async def consultar(numero, **kw):
            ids = kw["documento_ids"]
            calls.append(ids)
            if len(ids) > 1:
                raise requests.exceptions.ConnectionError("reset")
            n = int(ids[0][3:])
            return _answer([_doc(n, b"ok%d" % n)])

        saved = await _run(client, 2, tmp_path, consultar)

        assert calls == [["doc0", "doc1"], ["doc0"], ["doc1"]]
        assert len(saved) == 2
        assert saved.failed_ids == []

    @pytest.mark.asyncio
    async def test_only_the_missing_docs_of_a_successful_answer_are_retried(
        self, tmp_path
    ):
        client = _make_client()
        calls = []

        async def consultar(numero, **kw):
            ids = kw["documento_ids"]
            calls.append(ids)
            if len(ids) > 1:
                return _answer([_doc(0, b"zero")])  # doc1 absent
            return _answer([_doc(1, b"one")])

        saved = await _run(client, 2, tmp_path, consultar)

        assert calls == [["doc0", "doc1"], ["doc1"]]
        assert len(saved) == 2
        assert saved.failed_ids == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "status", ["mni_error", "not_found", "auth_failed", "blocked"]
    )
    async def test_business_or_credential_errors_are_not_retried(
        self, tmp_path, status
    ):
        client = _make_client()
        calls = []

        async def consultar(numero, **kw):
            calls.append(kw["documento_ids"])
            return _fail(status)

        saved = await _run(client, 3, tmp_path, consultar)

        assert len(calls) == 1
        assert saved.failed_ids == ["doc0", "doc1", "doc2"]

    @pytest.mark.asyncio
    async def test_a_single_document_batch_is_not_asked_again(self, tmp_path):
        client = _make_client()
        calls = []

        async def consultar(numero, **kw):
            calls.append(kw["documento_ids"])
            return _fail("timeout")

        saved = await _run(client, 1, tmp_path, consultar)

        assert len(calls) == 1
        assert saved.failed_ids == ["doc0"]

    @pytest.mark.asyncio
    async def test_only_one_round_of_individual_retries(self, tmp_path):
        client = _make_client()
        calls = []

        async def consultar(numero, **kw):
            calls.append(kw["documento_ids"])
            return _fail("timeout")

        saved = await _run(client, 3, tmp_path, consultar)

        # 1 batch call + 3 individual calls, never a second round.
        assert len(calls) == 4
        assert saved.failed_ids == ["doc0", "doc1", "doc2"]

    @pytest.mark.asyncio
    async def test_disk_error_is_not_swallowed_nor_retried(self, tmp_path):
        from file_utils import DiskWriteError

        client = _make_client()
        calls = []

        async def consultar(numero, **kw):
            calls.append(kw["documento_ids"])
            return _answer([_doc(0, b"a"), _doc(1, b"b")])

        with patch.object(
            client, "_save_document", side_effect=DiskWriteError(28, "No space")
        ):
            with pytest.raises(DiskWriteError):
                await _run(client, 2, tmp_path, consultar)

        assert len(calls) == 1


# ---------------------------------------------------------------------------
# M2 — worker keeps failed_ids when MNI saved nothing
# ---------------------------------------------------------------------------


class TestWorkerKeepsFailedIdsWhenNothingSaved:
    @staticmethod
    def _worker():
        from tests.test_download_phases import _load_worker_module, _make_worker

        w = _load_worker_module()
        worker = _make_worker(w)
        worker.mni_client = MagicMock()
        phase1 = SimpleNamespace(
            success=True,
            processo=SimpleNamespace(
                documentos=[SimpleNamespace(tipo="doc", vinculados=[], id="P1")]
            ),
        )
        worker.mni_client.consultar_processo = AsyncMock(return_value=phase1)
        return w, worker

    @pytest.mark.asyncio
    async def test_empty_result_with_failed_ids_is_returned(self, tmp_path):
        w, worker = self._worker()
        worker.mni_client.download_documentos = AsyncMock(
            return_value=w.DownloadedFiles([], ["P1"])
        )

        files, pendentes, expected = await worker._try_mni_download(_NUM, tmp_path)

        assert files is not None and list(files) == []
        assert files.failed_ids == ["P1"]
        assert pendentes == 0  # nothing saved: annex fallback has nothing to complement
        assert expected == 1

    @pytest.mark.asyncio
    async def test_empty_result_without_failures_is_still_none(self, tmp_path):
        w, worker = self._worker()
        worker.mni_client.download_documentos = AsyncMock(
            return_value=w.DownloadedFiles([], [])
        )

        files, _, _ = await worker._try_mni_download(_NUM, tmp_path)

        assert files is None

    @pytest.mark.asyncio
    async def test_dormant_fallbacks_still_end_failed_and_name_the_lost_docs(
        self, tmp_path
    ):
        w, worker = self._worker()
        worker.mni_client.download_documentos = AsyncMock(
            return_value=w.DownloadedFiles([], ["P1"])
        )
        worker.is_session_expired = lambda: False
        worker._ensure_browser = AsyncMock(return_value=False)
        worker._try_official_api = AsyncMock(return_value=None)
        worker._detect_captcha = AsyncMock(return_value=False)
        worker._download_via_browser = AsyncMock(return_value=[])

        result = await worker.download_process(
            {"jobId": "j-empty", "numeroProcesso": _NUM}
        )

        assert result["status"] == "failed", result
        assert "P1" in result["errorMessage"]
        assert "MNI" in result["errorMessage"]


# ---------------------------------------------------------------------------
# M1 — recursive documentoVinculado
# ---------------------------------------------------------------------------


def _raw(id_, vinculados=()):
    return SimpleNamespace(
        idDocumento=id_,
        descricao=f"d{id_}",
        tipoDocumento="t",
        mimetype="application/pdf",
        conteudo=None,
        documentoVinculado=list(vinculados),
    )


def _response(docs):
    proc = SimpleNamespace(dadosBasicos=None, documento=docs, movimento=[])
    return SimpleNamespace(processo=proc)


class TestParserIsRecursive:
    def test_annex_of_an_annex_is_parsed_with_its_parent_id(self):
        client = _make_client()
        resp = _response([_raw("1", [_raw("2", [_raw("3")])])])

        processo = client._parse_processo(resp, _NUM)

        (root,) = processo.documentos
        (annex,) = root.vinculados
        (deep,) = annex.vinculados
        assert (root.id, annex.id, deep.id) == ("1", "2", "3")
        assert annex.id_pai == "1"
        assert deep.id_pai == "2"

    def test_download_plan_counts_nested_annexes(self):
        from mni_client import _flatten_documents

        client = _make_client()
        resp = _response([_raw("1", [_raw("2", [_raw("3")])]), _raw("4")])

        processo = client._parse_processo(resp, _NUM)

        assert [d.id for d in _flatten_documents(processo.documentos)] == [
            "1",
            "2",
            "3",
            "4",
        ]

    def test_depth_is_bounded_against_hostile_xml(self):
        from mni_client import _flatten_documents

        client = _make_client()
        node = _raw("leaf")
        for i in range(50):
            node = _raw(f"n{i}", [node])

        processo = client._parse_processo(_response([node]), _NUM)

        # Bounded: far fewer than the 51 levels sent, and no RecursionError.
        assert len(list(_flatten_documents(processo.documentos))) <= 8

    def test_none_vinculado_is_skipped(self):
        client = _make_client()
        raw = _raw("1")
        raw.documentoVinculado = None

        processo = client._parse_processo(_response([raw]), _NUM)

        assert processo.documentos[0].vinculados == []
