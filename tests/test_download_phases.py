"""Phase-isolation tests for download_process sub-methods.

Each test exercises a single _phase_* method without Playwright, MNI, or GDrive
fixtures. DownloadContext is the shared state carrier; mocking is scoped to the
specific sub-method under test.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("DOWNLOAD_BASE_DIR", "/tmp/pje-phase-test-downloads")
os.environ.setdefault("SESSION_STATE_PATH", "/tmp/pje-phase-test-session.json")


def _load_worker_module():
    import importlib
    import redis as _real_redis

    mock_redis = MagicMock()
    mock_redis.from_url = MagicMock(return_value=AsyncMock())
    mock_redis.ConnectionError = _real_redis.ConnectionError
    mock_redis.TimeoutError = _real_redis.TimeoutError
    mock_playwright = MagicMock()

    with patch.dict(
        "sys.modules",
        {
            "redis": mock_redis,
            "redis.asyncio": mock_redis,
            "playwright": mock_playwright,
            "playwright.async_api": mock_playwright,
            "mni_client": MagicMock(),
        },
    ):
        import worker as w

        importlib.reload(w)
        return w


def _make_ctx(w, *, num_files=0, anexos=0, gdrive_url=None, incluir_anexos=True):
    ctx = w.DownloadContext(
        job={"jobId": "j1", "numeroProcesso": "0001234-56.2024.8.08.0001"},
        job_id="j1",
        numero_processo="0001234-56.2024.8.08.0001",
        tipos_documento=None,
        incluir_anexos=incluir_anexos,
        gdrive_url=gdrive_url,
        output_dir=Path("/tmp/pje-phase-test-downloads/test"),
    )
    ctx.downloaded_files = [
        {"name": f"doc{i}.pdf", "tamanhoBytes": 100} for i in range(num_files)
    ]
    ctx.anexos_pendentes = anexos
    return ctx


def _make_worker(w):
    worker = w.PJeSessionWorker()
    worker._publish_progress = AsyncMock()
    worker._log_job_result = AsyncMock()
    worker.mni_client = None
    worker.page = None
    worker.context = None
    return worker


class TestPhaseGdrive:
    @pytest.mark.asyncio
    async def test_gdrive_merges_files_and_continues_when_session_available(self):
        """GDrive returns files, MNI client exists → merges files, returns None (continue)."""
        w = _load_worker_module()
        worker = _make_worker(w)
        worker.mni_client = MagicMock()  # MNI available
        ctx = _make_ctx(w, gdrive_url="https://drive.google.com/folder/abc")

        gdrive_files = [{"name": "scan1.pdf", "tamanhoBytes": 500}]
        mock_gdrive = MagicMock()
        mock_gdrive.download_gdrive_folder = AsyncMock(return_value=gdrive_files)
        with patch.dict("sys.modules", {"gdrive_downloader": mock_gdrive}):
            result = await worker._phase_gdrive(ctx)

        assert result is None
        assert len(ctx.downloaded_files) == 1

    @pytest.mark.asyncio
    async def test_gdrive_returns_partial_without_mni_and_no_session(self):
        """GDrive returns files, no MNI, no page → early exit with partial_success."""
        w = _load_worker_module()
        worker = _make_worker(w)
        worker.mni_client = None
        worker.page = None
        worker.context = None
        ctx = _make_ctx(w, gdrive_url="https://drive.google.com/folder/abc")

        gdrive_files = [{"name": "scan1.pdf", "tamanhoBytes": 500}]

        mock_gdrive = MagicMock()
        mock_gdrive.download_gdrive_folder = AsyncMock(return_value=gdrive_files)
        with patch.dict("sys.modules", {"gdrive_downloader": mock_gdrive}):
            result = await worker._phase_gdrive(ctx)

        assert result is not None
        assert result["status"] == "partial_success"
        assert len(ctx.downloaded_files) == 1


class TestPhaseMni:
    @pytest.mark.asyncio
    async def test_mni_success_no_annexes_returns_success(self):
        """MNI returns 2 files with 0 annexes → early success exit."""
        w = _load_worker_module()
        worker = _make_worker(w)
        ctx = _make_ctx(w)

        mni_files = [
            {"name": "doc1.pdf", "tamanhoBytes": 200},
            {"name": "doc2.pdf", "tamanhoBytes": 300},
        ]
        worker._try_mni_download = AsyncMock(return_value=(mni_files, 0, 2))

        result = await worker._phase_mni(ctx)

        assert result is not None
        assert result["status"] == "success"
        assert len(ctx.downloaded_files) == 2
        assert ctx.anexos_pendentes == 0

    @pytest.mark.asyncio
    async def test_mni_with_annexes_returns_none_to_continue(self):
        """MNI returns files with pending annexes → returns None (continue to browser phase)."""
        w = _load_worker_module()
        worker = _make_worker(w)
        ctx = _make_ctx(w)

        mni_files = [{"name": "doc1.pdf", "tamanhoBytes": 200}]
        worker._try_mni_download = AsyncMock(return_value=(mni_files, 3, 4))

        result = await worker._phase_mni(ctx)

        assert result is None
        assert ctx.anexos_pendentes == 3
        assert ctx.expected_total_docs == 4
        assert len(ctx.downloaded_files) == 1

    @pytest.mark.asyncio
    async def test_mni_two_tuple_result_sets_expected_from_file_count(self):
        """MNI returning 2-tuple sets expected_total_docs from len(mni_files)."""
        w = _load_worker_module()
        worker = _make_worker(w)
        ctx = _make_ctx(w)

        mni_files = [{"name": f"d{i}.pdf", "tamanhoBytes": 100} for i in range(5)]
        worker._try_mni_download = AsyncMock(return_value=(mni_files, 0))

        result = await worker._phase_mni(ctx)

        assert result is not None
        assert result["status"] == "success"
        assert ctx.expected_total_docs == 5


class TestPhaseApiAndBrowser:
    @pytest.mark.asyncio
    async def test_api_fallback_finds_files_returns_true(self):
        """API returns files → True, ctx.downloaded_files updated."""
        w = _load_worker_module()
        worker = _make_worker(w)
        ctx = _make_ctx(w)

        api_files = [{"name": "api_doc.pdf", "tamanhoBytes": 400}]
        worker._try_official_api = AsyncMock(return_value=api_files)

        found = await worker._phase_api_fallback(ctx)

        assert found is True
        assert len(ctx.downloaded_files) == 1

    @pytest.mark.asyncio
    async def test_api_fallback_empty_returns_false(self):
        """API returns nothing → False (trigger browser fallback)."""
        w = _load_worker_module()
        worker = _make_worker(w)
        ctx = _make_ctx(w)

        worker._try_official_api = AsyncMock(return_value=[])

        found = await worker._phase_api_fallback(ctx)

        assert found is False
        assert ctx.downloaded_files == []

    @pytest.mark.asyncio
    async def test_browser_fallback_captcha_detected(self):
        """CAPTCHA present → captcha_required result dict."""
        w = _load_worker_module()
        worker = _make_worker(w)
        ctx = _make_ctx(w)
        worker._detect_captcha = AsyncMock(return_value=True)

        result = await worker._phase_browser_fallback(ctx)

        assert result["status"] == "captcha_required"

    @pytest.mark.asyncio
    async def test_browser_fallback_downloads_files_returns_none_for_success_path(self):
        """Browser downloads files → returns None (caller continues to shared success path)."""
        w = _load_worker_module()
        worker = _make_worker(w)
        ctx = _make_ctx(w)
        worker._detect_captcha = AsyncMock(return_value=False)

        browser_files = [{"name": "browser_doc.pdf", "tamanhoBytes": 600}]
        worker._download_via_browser = AsyncMock(return_value=browser_files)

        result = await worker._phase_browser_fallback(ctx)

        assert result is None
        assert len(ctx.downloaded_files) == 1
