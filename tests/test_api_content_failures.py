"""Known REST document losses must survive the download strategy cascade."""

import errno
from unittest.mock import AsyncMock, MagicMock

import pytest

from file_utils import DiskWriteError
from tests.pdf_fixtures import minimal_pdf


def make_worker(tmp_path, monkeypatch):
    import worker as module

    monkeypatch.setattr(module, "DOWNLOAD_BASE_DIR", tmp_path)
    worker = module.PJeSessionWorker()
    worker.mni_client = None
    worker.page = AsyncMock()
    worker.page.url = "https://synthetic.invalid/pje/"
    worker.context = AsyncMock()
    worker._publish_progress = AsyncMock()
    worker._log_job_result = AsyncMock()
    worker._download_via_browser = AsyncMock(return_value=[])
    worker._detect_captcha = AsyncMock(return_value=False)
    worker.is_session_expired = MagicMock(return_value=False)
    listing = AsyncMock(status=200)
    listing.json.return_value = [
        {"id": "good", "nome": "good.pdf"},
        {"id": "bad", "nome": "bad.pdf"},
    ]
    good = AsyncMock(status=200)
    good.headers = {"content-type": "application/pdf"}
    good.body.return_value = minimal_pdf(b"synthetic")
    bad = AsyncMock(status=200)
    bad.headers = {"content-type": "application/pdf"}
    bad.body.return_value = b'<html><form action="/login"></form></html>'

    async def request(url, **kwargs):
        if "/api/processos/" in url:
            return listing
        return good if url.endswith("/good/download") else bad

    worker.page.request.get.side_effect = request
    return worker


@pytest.mark.asyncio
async def test_rest_list_keeps_invalid_document_id_in_failed_ids(tmp_path, monkeypatch):
    worker = make_worker(tmp_path, monkeypatch)
    files = await worker._try_official_api("synthetic", tmp_path)
    assert len(files) == 1
    assert files.failed_ids == ["bad"]


@pytest.mark.asyncio
async def test_real_rest_body_rejection_yields_partial_job(tmp_path, monkeypatch):
    worker = make_worker(tmp_path, monkeypatch)
    result = await worker.download_process(
        {"jobId": "synthetic", "numeroProcesso": "synthetic"}
    )
    assert result["status"] == "partial_success"
    assert "API REST" in result["errorMessage"]
    assert "bad" in result["errorMessage"]
    assert len(result["arquivosDownloaded"]) == 1
    assert worker._publish_progress.await_args.kwargs["status"] == "partial"
    worker._download_via_browser.assert_not_awaited()


@pytest.mark.asyncio
async def test_rest_disk_error_escapes_the_listing_catch(tmp_path, monkeypatch):
    worker = make_worker(tmp_path, monkeypatch)
    worker._download_document_api = AsyncMock(
        side_effect=DiskWriteError(errno.ENOSPC, "No space")
    )
    with pytest.raises(DiskWriteError):
        await worker._try_official_api("synthetic", tmp_path)


@pytest.mark.asyncio
async def test_rest_disk_error_fails_job_without_browser_fallback(
    tmp_path, monkeypatch
):
    worker = make_worker(tmp_path, monkeypatch)
    worker._download_document_api = AsyncMock(
        side_effect=DiskWriteError(errno.ENOSPC, "No space")
    )
    result = await worker.download_process(
        {"jobId": "synthetic", "numeroProcesso": "synthetic"}
    )
    assert result["status"] == "failed"
    assert "No space" in result["errorMessage"]
    worker._download_via_browser.assert_not_awaited()


@pytest.mark.asyncio
async def test_all_rest_losses_remain_visible_after_unverified_browser_result(
    tmp_path, monkeypatch
):
    worker = make_worker(tmp_path, monkeypatch)
    worker._download_document_api = AsyncMock(return_value=None)
    worker._download_via_browser.return_value = [
        {"nome": "consolidado.pdf", "tamanhoBytes": 50, "checksum": "synthetic"}
    ]
    result = await worker.download_process(
        {"jobId": "synthetic", "numeroProcesso": "synthetic"}
    )
    assert result["status"] == "partial_success"
    assert "2 documento" in result["errorMessage"]
    assert "API REST" in result["errorMessage"]
    worker._download_via_browser.assert_awaited_once()


@pytest.mark.asyncio
async def test_rest_network_oserror_can_still_fall_back(tmp_path, monkeypatch):
    import requests

    worker = make_worker(tmp_path, monkeypatch)
    worker.page.request.get.side_effect = requests.ConnectionError("synthetic reset")
    monkeypatch.setattr("worker.asyncio.sleep", AsyncMock())
    assert await worker._try_official_api("synthetic", tmp_path) is None
