"""Tests for worker.py F4 — lazy browser un-deferral in MNI mode.

Pre-F4, `self.page`/`self.context` were assigned in exactly one place: the
non-MNI branch of `load_session`. In MNI mode they stayed `None` forever, so
strategies 2 (`_try_official_api`) and 3 (`_download_via_browser`) could
never run — processos whose MNI result left `anexos_pendentes > 0` always
fell into the `partial_success` / "sem sessão PJe disponível" branch.

F4 adds `_ensure_browser()`, a lazy, headless, non-blocking un-deferral that
reuses a session file saved earlier via `/api/session/login` or
`python pje_session.py login`. It never launches Chromium eagerly and never
waits on manual login — see `worker.py::PJeSessionWorker._ensure_browser`.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def _load_worker_module():
    """Import worker with heavy dependencies mocked out (mirrors test_worker.py)."""
    import importlib
    import os

    import redis as _real_redis

    os.environ.setdefault("DOWNLOAD_BASE_DIR", "/tmp/pje-test-downloads")
    os.environ.setdefault("SESSION_STATE_PATH", "/tmp/pje-test-session.json")

    mock_redis_module = MagicMock()
    mock_redis_module.from_url = MagicMock(return_value=AsyncMock())
    mock_redis_module.ConnectionError = _real_redis.ConnectionError
    mock_redis_module.TimeoutError = _real_redis.TimeoutError
    mock_redis_module.ResponseError = _real_redis.ResponseError
    mock_playwright_module = MagicMock()

    from unittest.mock import patch

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


class TestEnsureBrowserAlreadyReady:
    @pytest.mark.asyncio
    async def test_short_circuits_when_page_and_context_present(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        worker.page = AsyncMock()
        worker.context = AsyncMock()
        worker._playwright = None  # would blow up below if actually touched

        result = await worker._ensure_browser()

        assert result is True


class TestEnsureBrowserNoPlaywright:
    @pytest.mark.asyncio
    async def test_returns_false_without_launching(self, tmp_path):
        w = _load_worker_module()
        session_file = tmp_path / "session.json"
        session_file.write_text("{}")
        w.SESSION_STATE_PATH = session_file
        worker = w.PJeSessionWorker()
        worker._playwright = None

        result = await worker._ensure_browser()

        assert result is False
        assert worker.page is None
        assert worker.context is None


class TestEnsureBrowserNoSessionFile:
    """(a) MNI enabled + no session file -> False, chromium.launch never called."""

    @pytest.mark.asyncio
    async def test_returns_false_without_launching_chromium(self, tmp_path):
        w = _load_worker_module()
        w.SESSION_STATE_PATH = tmp_path / "does-not-exist.json"
        worker = w.PJeSessionWorker()
        fake_playwright = MagicMock()
        fake_playwright.chromium.launch = AsyncMock(
            side_effect=AssertionError("chromium.launch must not be called")
        )
        worker._playwright = fake_playwright

        result = await worker._ensure_browser()

        assert result is False
        assert worker.page is None
        assert worker.context is None
        fake_playwright.chromium.launch.assert_not_awaited()


class TestEnsureBrowserSuccess:
    """(b) MNI enabled + session file + fake playwright without a login
    redirect -> True, page/context set, fallback_ready True."""

    @pytest.mark.asyncio
    async def test_reuses_saved_session_headlessly(self, tmp_path):
        w = _load_worker_module()
        session_file = tmp_path / "session.json"
        session_file.write_text("{}")
        w.SESSION_STATE_PATH = session_file
        worker = w.PJeSessionWorker()
        worker._detect_captcha = AsyncMock(return_value=False)

        fake_page = AsyncMock()
        fake_page.url = "https://pje.tjes.jus.br/pje/Painel/painel_usuario/list.seam"
        fake_context = AsyncMock()
        fake_context.new_page = AsyncMock(return_value=fake_page)
        fake_browser = AsyncMock()
        fake_browser.new_context = AsyncMock(return_value=fake_context)
        fake_playwright = MagicMock()
        fake_playwright.chromium.launch = AsyncMock(return_value=fake_browser)
        worker._playwright = fake_playwright

        result = await worker._ensure_browser()

        assert result is True
        assert worker.page is fake_page
        assert worker.context is fake_context
        assert worker.session_valid is True
        assert worker.fallback_ready is True
        assert worker.session_started_at is not None
        fake_playwright.chromium.launch.assert_awaited_once_with(headless=True)
        fake_browser.new_context.assert_awaited_once_with(
            storage_state=str(session_file)
        )


class TestEnsureBrowserExpiredSession:
    """(c) session file present but page redirects to login -> False,
    everything closed and set back to None."""

    @pytest.mark.asyncio
    async def test_dead_session_is_cleaned_up(self, tmp_path):
        w = _load_worker_module()
        session_file = tmp_path / "session.json"
        session_file.write_text("{}")
        w.SESSION_STATE_PATH = session_file
        worker = w.PJeSessionWorker()
        worker._detect_captcha = AsyncMock(return_value=False)

        fake_page = AsyncMock()
        fake_page.url = "https://pje.tjes.jus.br/login.seam"
        fake_context = AsyncMock()
        fake_context.new_page = AsyncMock(return_value=fake_page)
        fake_browser = AsyncMock()
        fake_browser.new_context = AsyncMock(return_value=fake_context)
        fake_playwright = MagicMock()
        fake_playwright.chromium.launch = AsyncMock(return_value=fake_browser)
        worker._playwright = fake_playwright

        result = await worker._ensure_browser()

        assert result is False
        assert worker.page is None
        assert worker.context is None
        assert worker._browser is None
        assert worker.session_valid is False
        fake_page.close.assert_awaited_once()
        fake_context.close.assert_awaited_once()
        fake_browser.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_captcha_on_reuse_is_treated_as_dead_session(self, tmp_path):
        w = _load_worker_module()
        session_file = tmp_path / "session.json"
        session_file.write_text("{}")
        w.SESSION_STATE_PATH = session_file
        worker = w.PJeSessionWorker()
        worker._detect_captcha = AsyncMock(return_value=True)

        fake_page = AsyncMock()
        fake_page.url = "https://pje.tjes.jus.br/pje/Painel/painel_usuario/list.seam"
        fake_context = AsyncMock()
        fake_context.new_page = AsyncMock(return_value=fake_page)
        fake_browser = AsyncMock()
        fake_browser.new_context = AsyncMock(return_value=fake_context)
        fake_playwright = MagicMock()
        fake_playwright.chromium.launch = AsyncMock(return_value=fake_browser)
        worker._playwright = fake_playwright

        result = await worker._ensure_browser()

        assert result is False
        assert worker.page is None
        assert worker.context is None


class TestEnsureBrowserLaunchFailure:
    @pytest.mark.asyncio
    async def test_exception_during_launch_cleans_up_and_returns_false(self, tmp_path):
        w = _load_worker_module()
        session_file = tmp_path / "session.json"
        session_file.write_text("{}")
        w.SESSION_STATE_PATH = session_file
        worker = w.PJeSessionWorker()
        fake_playwright = MagicMock()
        fake_playwright.chromium.launch = AsyncMock(side_effect=RuntimeError("boom"))
        worker._playwright = fake_playwright

        result = await worker._ensure_browser()

        assert result is False
        assert worker.page is None
        assert worker.context is None
        assert worker._browser is None


class TestDownloadProcessReachesFallbackViaLazyBrowser:
    """(d) MNI leaves anexos_pendentes > 0 and a working lazy browser ->
    download_process now reaches strategy 2 (_try_official_api) instead of
    short-circuiting to partial_success "sem sessão PJe disponível"."""

    @pytest.mark.asyncio
    async def test_lazy_browser_unblocks_api_fallback(self, tmp_path):
        w = _load_worker_module()
        w.DOWNLOAD_BASE_DIR = tmp_path
        worker = w.PJeSessionWorker()
        worker.mni_client = MagicMock()
        worker._try_mni_download = AsyncMock(
            return_value=(
                [
                    {
                        "nome": "principal.pdf",
                        "checksum": "p1",
                        "tamanhoBytes": 10,
                        "fonte": "mni",
                    }
                ],
                1,  # anexos_pendentes
            )
        )
        worker._log_job_result = AsyncMock()
        worker.is_session_expired = MagicMock(return_value=False)

        async def fake_ensure_browser():
            worker.page = AsyncMock()
            worker.context = AsyncMock()
            return True

        worker._ensure_browser = fake_ensure_browser
        worker._try_official_api = AsyncMock(return_value=None)
        worker._download_via_browser = AsyncMock(return_value=None)

        result = await worker.download_process(
            {
                "jobId": "J9",
                "numeroProcesso": "5000009-00.2024.8.08.0001",
            }
        )

        worker._try_official_api.assert_awaited_once()
        assert "sem sess" not in (result.get("errorMessage") or "")


class TestDownloadProcessUnchangedWithoutSessionFile:
    """(e) same scenario but no session file on disk -> unchanged behaviour:
    partial_success / "sem sessão PJe disponível", strategies 2/3 never run."""

    @pytest.mark.asyncio
    async def test_no_session_file_still_short_circuits(self, tmp_path):
        w = _load_worker_module()
        w.DOWNLOAD_BASE_DIR = tmp_path
        w.SESSION_STATE_PATH = tmp_path / "no-such-session.json"
        worker = w.PJeSessionWorker()
        worker.mni_client = MagicMock()
        worker._playwright = MagicMock()  # available, but no session file
        worker._try_mni_download = AsyncMock(
            return_value=(
                [
                    {
                        "nome": "principal.pdf",
                        "checksum": "p1",
                        "tamanhoBytes": 10,
                        "fonte": "mni",
                    }
                ],
                1,
            )
        )
        worker._log_job_result = AsyncMock()
        worker.is_session_expired = MagicMock(return_value=False)
        worker._try_official_api = AsyncMock()
        worker._download_via_browser = AsyncMock()

        result = await worker.download_process(
            {
                "jobId": "J10",
                "numeroProcesso": "5000010-00.2024.8.08.0001",
            }
        )

        assert result["status"] == "partial_success"
        assert "sem sess" in result["errorMessage"]
        worker._try_official_api.assert_not_awaited()
        worker._download_via_browser.assert_not_awaited()
