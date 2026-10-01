"""The saved Playwright session holds live PJe cookies; it must be 0600.

`pje_session.interactive_login` already wrote it with 0600, but
`worker.load_session` handed the path to Playwright's `storage_state(path=...)`,
which writes with the process umask (0644 here): readable by every user on the
host, contradicting the "Session file written with 0600" rule in CLAUDE.md.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.test_worker import _load_worker_module


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _playwright_whose_login_succeeds():
    """Like Playwright: `storage_state(path=...)` writes the file itself."""
    page = MagicMock()
    page.goto = AsyncMock()
    page.wait_for_url = AsyncMock()  # login completes
    page.url = "https://pje.tjes.jus.br/pje/home.seam"

    async def write_state(path):
        Path(path).write_text('{"cookies": []}', encoding="utf-8")

    ctx = MagicMock()
    ctx.new_page = AsyncMock(return_value=page)
    ctx.storage_state = AsyncMock(side_effect=write_state)
    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=ctx)
    playwright = MagicMock()
    playwright.chromium.launch = AsyncMock(return_value=browser)
    return playwright


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
@pytest.mark.asyncio
async def test_session_state_is_saved_owner_only(tmp_path, monkeypatch):
    w = _load_worker_module()
    session_file = tmp_path / "pje-session.json"
    monkeypatch.setattr(w, "SESSION_STATE_PATH", session_file)
    worker = w.PJeSessionWorker()
    worker.mni_client = None

    old_umask = os.umask(0o022)  # the usual default that yields 0644
    try:
        assert await worker.load_session(_playwright_whose_login_succeeds()) is True
    finally:
        os.umask(old_umask)

    assert session_file.exists()
    assert _mode(session_file) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
@pytest.mark.asyncio
async def test_an_existing_world_readable_session_file_is_tightened(
    tmp_path, monkeypatch
):
    w = _load_worker_module()
    session_file = tmp_path / "pje-session.json"
    session_file.write_text("{}", encoding="utf-8")
    session_file.chmod(0o644)
    # Present but expired: forces the manual-login branch that re-saves it.
    monkeypatch.setattr(w, "SESSION_STATE_PATH", session_file)
    worker = w.PJeSessionWorker()
    worker.mni_client = None
    playwright = _playwright_whose_login_succeeds()
    page = playwright.chromium.launch.return_value.new_context.return_value.new_page.return_value
    page.url = "https://pje.tjes.jus.br/pje/login.seam"  # redirected to login

    assert await worker.load_session(playwright) is True
    assert _mode(session_file) == 0o600
