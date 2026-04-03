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
