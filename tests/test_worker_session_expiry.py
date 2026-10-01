"""F7: the operational session timeout must not abort batches in MNI mode.

Found while reviewing F4 (2026-09-24), pre-existing on `e4ca0b2`:
`load_session`'s MNI branch stamps `session_started_at` at boot for a browser
that never exists, and `download_process` checked `is_session_expired()` before
the fallback strategies regardless of whether any browser existed. After
`SESSION_TIMEOUT_MINUTES` (60) of *worker uptime* — days, in production — every
processo where MNI returned nothing came back `session_expired`, which
`dashboard_api._FATAL_WORKER_STATUSES` treats as fatal: the dashboard LREM-ed
the remaining jobs and failed the whole batch.

Measured on the pre-fix code::

    uptime=   59 min  MNI=no documents  ->  status=failed           aborts batch: False
    uptime=   61 min  MNI=no documents  ->  status=session_expired  aborts batch: True
    uptime= 1440 min  MNI=no documents  ->  status=session_expired  aborts batch: True

These tests pin the fix and, separately, that browser-primary mode kept its
original semantics untouched.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

import dashboard_api
from tests.test_worker_lazy_browser import _load_worker_module

_JOB = {"jobId": "J-F7", "numeroProcesso": "0000000-00.2024.8.08.0000"}


def _mni_worker(w, tmp_path, *, uptime_minutes: int):
    """A worker in MNI mode, as production runs it, `uptime_minutes` after boot."""
    w.DOWNLOAD_BASE_DIR = tmp_path
    worker = w.PJeSessionWorker()
    worker.mni_client = MagicMock()
    worker.redis = None  # _publish_progress becomes a no-op
    worker._log_job_result = AsyncMock()
    # Exactly what load_session's MNI early-return leaves behind: a start
    # stamp, no browser, and (F4) a stashed Playwright handle or None.
    worker.session_started_at = datetime.now(UTC) - timedelta(minutes=uptime_minutes)
    worker.page = None
    worker.context = None
    worker._playwright = None  # no session file scenario: _ensure_browser -> False
    return worker


class TestMniModeNeverTurnsUptimeIntoSessionExpired:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("uptime_minutes", [61, 24 * 60, 7 * 24 * 60])
    async def test_mni_miss_after_timeout_is_failed_not_fatal(
        self, tmp_path, uptime_minutes
    ):
        """The measured bug: MNI finds nothing, worker up > 60 min."""
        w = _load_worker_module()
        worker = _mni_worker(w, tmp_path, uptime_minutes=uptime_minutes)
        worker._try_mni_download = AsyncMock(return_value=(None, 0, 0))

        result = await worker.download_process(dict(_JOB))

        assert result["status"] == "failed", result
        assert result["status"] not in dashboard_api._FATAL_WORKER_STATUSES, (
            "one MNI miss must never abort the rest of the batch"
        )
        assert worker._health_status == "ready"

    @pytest.mark.asyncio
    async def test_same_job_before_timeout_gives_the_same_answer(self, tmp_path):
        """Control: the outcome must not depend on worker uptime at all."""
        w = _load_worker_module()
        young = _mni_worker(w, tmp_path, uptime_minutes=5)
        young._try_mni_download = AsyncMock(return_value=(None, 0, 0))
        old = _mni_worker(w, tmp_path, uptime_minutes=90 * 24 * 60)
        old._try_mni_download = AsyncMock(return_value=(None, 0, 0))

        r_young = await young.download_process(dict(_JOB))
        r_old = await old.download_process(dict(_JOB))

        assert r_young["status"] == r_old["status"] == "failed"


class TestMniModeExpiredHelperBrowserIsClosedNotFatal:
    @pytest.mark.asyncio
    async def test_helper_browser_past_timeout_is_closed_and_session_file_kept(
        self, tmp_path
    ):
        """With F4 a helper browser can exist in MNI mode. Past the operational
        timeout it is closed — but the operator's session file survives, and the
        processo reports the honest partial result instead of aborting the batch.

        This is the "no relaunch possible" case: `_playwright` is `None` (see
        `_mni_worker`), so after `_close_browser()` the code review fix's
        `_ensure_browser()` call short-circuits to `False` without launching
        anything. The "relaunch succeeds" case — a real `_playwright` handle
        available — is `TestExpiredHelperIsRelaunchedInTheSameJob` below."""
        w = _load_worker_module()
        worker = _mni_worker(w, tmp_path, uptime_minutes=61)
        session_file = tmp_path / "pje-session.json"
        session_file.write_text("{}")
        w.SESSION_STATE_PATH = session_file
        page, context, browser = AsyncMock(), AsyncMock(), AsyncMock()
        worker.page, worker.context, worker._browser = page, context, browser
        worker.session_valid = worker.fallback_ready = True
        worker._try_mni_download = AsyncMock(
            return_value=(
                [{"nome": "principal.pdf", "checksum": "p1", "tamanhoBytes": 10}],
                1,  # anexos pending -> a fallback would be wanted
            )
        )

        result = await worker.download_process(dict(_JOB))

        assert result["status"] == "partial_success", result
        assert result["status"] not in dashboard_api._FATAL_WORKER_STATUSES
        assert "sem sessão PJe" in (result["errorMessage"] or "")
        page.close.assert_awaited_once()
        context.close.assert_awaited_once()
        browser.close.assert_awaited_once()
        assert worker.page is None and worker.context is None
        assert worker.fallback_ready is False
        assert session_file.exists(), (
            "an operational timeout must not delete the operator's session file"
        )


class TestExpiredHelperIsRelaunchedInTheSameJob:
    @pytest.mark.asyncio
    async def test_expired_helper_is_relaunched_from_the_kept_session_file_in_the_same_job(
        self, tmp_path
    ):
        """Code-review finding: F7's expiry block used to run AFTER
        `_ensure_browser()`, so a helper browser that just crossed
        `SESSION_TIMEOUT_MINUTES` was closed but never relaunched in the same
        job — the job fell into `partial_success` "sem sessão PJe" even though
        a fresh headless launch from the kept session file would have worked.
        With F7 moved above `_ensure_browser()`, the old helper is closed and
        immediately relaunched, and the job reaches the fallback strategies in
        this same call."""
        w = _load_worker_module()
        worker = _mni_worker(w, tmp_path, uptime_minutes=61)
        session_file = tmp_path / "pje-session.json"
        session_file.write_text("{}")
        w.SESSION_STATE_PATH = session_file

        # The OLD, expired helper — must be closed.
        old_page, old_context, old_browser = AsyncMock(), AsyncMock(), AsyncMock()
        worker.page, worker.context, worker._browser = (
            old_page,
            old_context,
            old_browser,
        )
        worker.session_valid = worker.fallback_ready = True

        # A working Playwright handle — the relaunch this test is pinning.
        # Copied from test_worker_lazy_browser.py::TestEnsureBrowserSuccess.
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

        worker._try_mni_download = AsyncMock(
            return_value=(
                [{"nome": "principal.pdf", "checksum": "p1", "tamanhoBytes": 10}],
                1,  # anexos pending -> a fallback is wanted
            )
        )
        worker._try_official_api = AsyncMock(return_value=None)
        worker._download_via_browser = AsyncMock(return_value=None)

        result = await worker.download_process(dict(_JOB))

        old_page.close.assert_awaited_once()
        old_context.close.assert_awaited_once()
        old_browser.close.assert_awaited_once()
        fake_playwright.chromium.launch.assert_awaited_once()
        worker._try_official_api.assert_awaited_once()
        assert not (
            result["status"] == "partial_success"
            and "sem sessão" in (result.get("errorMessage") or "")
        )
        assert result["status"] not in dashboard_api._FATAL_WORKER_STATUSES
        assert session_file.exists()


class TestBrowserPrimaryModeUnchanged:
    @pytest.mark.asyncio
    async def test_without_mni_an_expired_session_is_still_fatal(self, tmp_path):
        """No MNI: the browser IS the pipeline, so the original semantics stay —
        expired -> session_expired, and the dashboard rightly aborts the batch."""
        w = _load_worker_module()
        w.DOWNLOAD_BASE_DIR = tmp_path
        worker = w.PJeSessionWorker()
        worker.mni_client = None
        worker.redis = None
        worker.page, worker.context = AsyncMock(), AsyncMock()
        worker.session_started_at = datetime.now(UTC) - timedelta(minutes=61)

        result = await worker.download_process(dict(_JOB))

        assert result["status"] == "session_expired"
        assert result["status"] in dashboard_api._FATAL_WORKER_STATUSES
        assert worker._health_status == "session_expired"


class TestMniModeSessionLostDuringJobKeepsSessionFile:
    """Code-review finding on #46/#47: F4's `_ensure_browser()` makes
    `self.page` reachable in MNI mode, which reopened a branch in
    `download_process`'s outer `except` — the `session_lost` check
    (`"login" in self.page.url.lower()`) — that used to be dead code there
    (`self.page` was always `None` in MNI mode before F4). That branch still
    called the unconditional-delete `invalidate_session()`, exactly the call
    F7 (above) replaced with `_close_browser()` everywhere else in MNI mode.
    A stale mid-job redirect glitch would force an unwanted manual re-login."""

    @pytest.mark.asyncio
    async def test_session_lost_mid_job_in_mni_mode_closes_but_keeps_file(
        self, tmp_path
    ):
        w = _load_worker_module()
        worker = _mni_worker(w, tmp_path, uptime_minutes=5)  # not a timeout case
        session_file = tmp_path / "pje-session.json"
        session_file.write_text("{}")
        w.SESSION_STATE_PATH = session_file

        page, context, browser = AsyncMock(), AsyncMock(), AsyncMock()
        page.url = "https://pje.tjes.jus.br/pje/login.seam"
        worker.page, worker.context, worker._browser = page, context, browser
        worker.session_valid = worker.fallback_ready = True
        worker._try_mni_download = AsyncMock(side_effect=RuntimeError("boom"))

        result = await worker.download_process(dict(_JOB))

        assert result["status"] == "session_expired"
        assert worker._health_status == "session_expired"
        page.close.assert_awaited_once()
        context.close.assert_awaited_once()
        browser.close.assert_awaited_once()
        assert worker.page is None and worker.context is None
        assert session_file.exists(), (
            "a helper browser dying mid-job in MNI mode must not delete the "
            "operator's session file — only a human re-login should"
        )
        assert worker.session_started_at is not None, (
            "_close_browser (unlike invalidate_session) must not clear "
            "session_started_at"
        )

    @pytest.mark.asyncio
    async def test_session_lost_mid_job_without_mni_still_deletes_file(self, tmp_path):
        """Control: browser-primary mode keeps the original, stricter
        behavior — a mid-job login redirect really does mean the session is
        dead, and there is no MNI pipeline to fall back to."""
        w = _load_worker_module()
        w.DOWNLOAD_BASE_DIR = tmp_path
        worker = w.PJeSessionWorker()
        worker.mni_client = None
        worker.redis = None
        session_file = tmp_path / "pje-session.json"
        session_file.write_text("{}")
        w.SESSION_STATE_PATH = session_file

        page, context, browser = AsyncMock(), AsyncMock(), AsyncMock()
        page.url = "https://pje.tjes.jus.br/pje/login.seam"
        worker.page, worker.context, worker._browser = page, context, browser
        worker.session_started_at = datetime.now(UTC)
        worker._try_official_api = AsyncMock(side_effect=RuntimeError("boom"))

        result = await worker.download_process(dict(_JOB))

        assert result["status"] == "session_expired"
        assert not session_file.exists(), (
            "browser-primary mode must keep deleting the session file on a "
            "mid-job session loss, as before this fix"
        )
        assert worker.session_started_at is None
