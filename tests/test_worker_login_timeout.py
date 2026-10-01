"""The manual-login wait must not share a knob with the download cap.

`load_session` waits on a human logging in; it used to reuse
`PLAYWRIGHT_FULL_DOWNLOAD_TIMEOUT_MS`, so lowering the full-download cap
(Phase 2 T2.2B) would have silently shortened the time allowed to log in.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.test_worker import _load_worker_module


def _playwright_with_login_page(page):
    ctx = MagicMock()
    ctx.new_page = AsyncMock(return_value=page)
    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=ctx)
    playwright = MagicMock()
    playwright.chromium.launch = AsyncMock(return_value=browser)
    return playwright


def _login_page():
    page = MagicMock()
    page.goto = AsyncMock()
    page.wait_for_url = AsyncMock(side_effect=TimeoutError("login not completed"))
    return page


def test_default_is_five_minutes_like_the_old_shared_value():
    w = _load_worker_module()
    assert w.PLAYWRIGHT_LOGIN_TIMEOUT_MS == 300_000


@pytest.mark.asyncio
async def test_manual_login_wait_uses_the_login_timeout(tmp_path, monkeypatch):
    w = _load_worker_module()
    monkeypatch.setattr(w, "SESSION_STATE_PATH", tmp_path / "absent-session.json")
    monkeypatch.setattr(w, "PLAYWRIGHT_LOGIN_TIMEOUT_MS", 777_000)
    worker = w.PJeSessionWorker()
    worker.mni_client = None
    page = _login_page()

    result = await worker.load_session(_playwright_with_login_page(page))

    assert result is False
    assert page.wait_for_url.await_args.kwargs["timeout"] == 777_000


@pytest.mark.asyncio
async def test_lowering_the_download_cap_does_not_shorten_the_login_wait(
    tmp_path, monkeypatch
):
    w = _load_worker_module()
    monkeypatch.setattr(w, "SESSION_STATE_PATH", tmp_path / "absent-session.json")
    monkeypatch.setattr(w, "PLAYWRIGHT_FULL_DOWNLOAD_TIMEOUT_MS", 1_234)
    worker = w.PJeSessionWorker()
    worker.mni_client = None
    page = _login_page()

    await worker.load_session(_playwright_with_login_page(page))

    assert (
        page.wait_for_url.await_args.kwargs["timeout"] == w.PLAYWRIGHT_LOGIN_TIMEOUT_MS
    )
    assert page.wait_for_url.await_args.kwargs["timeout"] != 1_234
