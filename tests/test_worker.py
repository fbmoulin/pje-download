"""Tests for worker.py — session lock release, MNI credentials fail-fast."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import pytest


def _load_worker_module():
    """Import worker with heavy dependencies mocked out."""
    import importlib
    import os

    # Ensure DOWNLOAD_BASE_DIR points somewhere writable before module-level mkdir runs
    os.environ.setdefault("DOWNLOAD_BASE_DIR", "/tmp/pje-test-downloads")
    os.environ.setdefault("SESSION_STATE_PATH", "/tmp/pje-test-session.json")

    mock_redis_module = MagicMock()
    mock_redis_module.from_url = MagicMock(return_value=AsyncMock())
    mock_playwright_module = MagicMock()

    with patch.dict(
        "sys.modules",
        {
            "redis": mock_redis_module,
            "redis.asyncio": mock_redis_module,
            "playwright": mock_playwright_module,
            "playwright.async_api": mock_playwright_module,
            "mni_client": MagicMock(),
        },
    ):
        import worker as w

        importlib.reload(w)
        return w


class TestInvalidateSession:
    """invalidate_session() must release the session lock."""

    @pytest.mark.asyncio
    async def test_invalidate_session_releases_lock(self):
        """Lock file handle must be closed and cleared after invalidate_session()."""
        w = _load_worker_module()
        worker = w.PJeSessionWorker()

        released = []
        worker._release_session_lock = lambda: released.append(True)
        worker.page = None
        worker.context = None
        worker._browser = None

        await worker.invalidate_session()
        assert released == [True]

    @pytest.mark.asyncio
    async def test_invalidate_session_clears_browser_refs(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()

        mock_page = AsyncMock()
        mock_ctx = AsyncMock()
        mock_browser = AsyncMock()
        worker.page = mock_page
        worker.context = mock_ctx
        worker._browser = mock_browser
        worker._release_session_lock = lambda: None

        await worker.invalidate_session()

        mock_page.close.assert_awaited_once()
        mock_ctx.close.assert_awaited_once()
        mock_browser.close.assert_awaited_once()
        assert worker.page is None
        assert worker.context is None
        assert worker._browser is None


class TestWorkerInit:
    """init() must log error and skip MNI when credentials are missing."""

    @pytest.mark.asyncio
    async def test_missing_mni_username_logs_error(self, monkeypatch):
        monkeypatch.delenv("MNI_USERNAME", raising=False)
        monkeypatch.setenv("MNI_PASSWORD", "pass")

        w = _load_worker_module()
        w.MNI_ENABLED = True

        worker = w.PJeSessionWorker()
        logged = []

        def fake_error(event, **kwargs):
            logged.append(event)

        worker_log = MagicMock()
        worker_log.error = fake_error

        with patch.object(w, "log", worker_log):
            await worker.init()

        assert any("credentials" in e for e in logged)
        assert worker.mni_client is None

    @pytest.mark.asyncio
    async def test_missing_mni_password_leaves_client_none(self, monkeypatch):
        monkeypatch.setenv("MNI_USERNAME", "user")
        monkeypatch.delenv("MNI_PASSWORD", raising=False)

        w = _load_worker_module()
        w.MNI_ENABLED = True

        worker = w.PJeSessionWorker()
        with patch.object(w, "log", MagicMock()):
            await worker.init()

        assert worker.mni_client is None


class TestUniqueFilename:
    def test_no_collision(self, tmp_path):
        w = _load_worker_module()
        name = w._unique_filename(tmp_path, "doc.pdf")
        assert name == "doc.pdf"

    def test_collision_appends_suffix(self, tmp_path):
        w = _load_worker_module()
        (tmp_path / "doc.pdf").write_bytes(b"x")
        name = w._unique_filename(tmp_path, "doc.pdf")
        assert name == "doc_1.pdf"

    def test_multiple_collisions(self, tmp_path):
        w = _load_worker_module()
        (tmp_path / "doc.pdf").write_bytes(b"x")
        (tmp_path / "doc_1.pdf").write_bytes(b"x")
        name = w._unique_filename(tmp_path, "doc.pdf")
        assert name == "doc_2.pdf"


class TestIsSessionExpired:
    def test_no_session_returns_true(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        worker.session_started_at = None
        assert worker.is_session_expired() is True

    def test_recent_session_not_expired(self):
        from datetime import datetime, UTC

        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        worker.session_started_at = datetime.now(UTC)
        assert worker.is_session_expired() is False

    def test_old_session_expired(self):
        from datetime import datetime, timedelta, UTC

        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        worker.session_started_at = datetime.now(UTC) - timedelta(minutes=120)
        assert worker.is_session_expired() is True


class TestDetectCaptcha:
    @pytest.mark.asyncio
    async def test_no_page_returns_false(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        worker.page = None
        assert await worker._detect_captcha() is False

    @pytest.mark.asyncio
    async def test_captcha_in_content(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        mock_page = AsyncMock()
        mock_page.content.return_value = '<div class="g-recaptcha">challenge</div>'
        mock_page.url = "https://pje.tjes.jus.br/pje/login.seam"
        worker.page = mock_page
        with patch.object(w, "log", MagicMock()):
            assert await worker._detect_captcha() is True

    @pytest.mark.asyncio
    async def test_no_captcha_in_clean_page(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        mock_page = AsyncMock()
        mock_page.content.return_value = "<html><body>Normal PJe page</body></html>"
        worker.page = mock_page
        assert await worker._detect_captcha() is False

    @pytest.mark.asyncio
    async def test_content_error_returns_false(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        mock_page = AsyncMock()
        mock_page.content.side_effect = Exception("page closed")
        worker.page = mock_page
        assert await worker._detect_captcha() is False


class TestResultHelper:
    def test_success_result(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        r = worker._result(
            "job1", "5000001-00.2024.8.08.0001", "success", [{"nome": "doc.pdf"}]
        )
        assert r["status"] == "success"
        assert r["jobId"] == "job1"
        assert r["numeroProcesso"] == "5000001-00.2024.8.08.0001"
        assert len(r["arquivosDownloaded"]) == 1

    def test_failed_result_with_error(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        r = worker._result(
            "job2", "5000002-00.2024.8.08.0001", "failed", error="timeout"
        )
        assert r["status"] == "failed"
        assert r["errorMessage"] == "timeout"

    def test_result_without_files(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        r = worker._result("job3", "5000003-00.2024.8.08.0001", "session_expired")
        assert r["arquivosDownloaded"] == []

    def test_result_has_timestamp(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        r = worker._result("j", "5000001-00.2024.8.08.0001", "success")
        assert "downloadedAt" in r
        assert "T" in r["downloadedAt"]  # ISO format


class TestLogJobResult:
    @pytest.mark.asyncio
    async def test_writes_json_to_logs_dir(self, tmp_path):
        w = _load_worker_module()
        w.DOWNLOAD_BASE_DIR = tmp_path
        worker = w.PJeSessionWorker()
        files = [{"nome": "doc.pdf", "tamanhoBytes": 1024}]
        await worker._log_job_result("j1", "5000001-00.2024.8.08.0001", files)
        log_file = tmp_path / "_logs" / "j1.json"
        assert log_file.exists()
        import json

        data = json.loads(log_file.read_text())
        assert data["jobId"] == "j1"
        assert len(data["arquivos"]) == 1


class TestWorkerClose:
    @pytest.mark.asyncio
    async def test_close_all_resources(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        mock_page = AsyncMock()
        mock_ctx = AsyncMock()
        mock_browser = AsyncMock()
        mock_redis = AsyncMock()
        worker.page = mock_page
        worker.context = mock_ctx
        worker._browser = mock_browser
        worker.redis = mock_redis
        await worker.close()
        mock_page.close.assert_awaited_once()
        mock_ctx.close.assert_awaited_once()
        mock_browser.close.assert_awaited_once()
        mock_redis.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_with_none_resources(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        worker.page = None
        worker.context = None
        worker._browser = None
        worker.redis = None
        await worker.close()  # Should not raise
