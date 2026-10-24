"""The audit-log rotation also runs periodically, not only when the dashboard boots.

Before: ``rotate_logs`` ran once in ``create_app``. A dashboard that stayed up for months
never rotated, and the next restart deleted everything past the window at once. Now
``_on_startup`` starts a background task (``AUDIT_ROTATE_TASK_KEY``) that sleeps one
interval, rotates in a thread, and repeats; ``_on_cleanup`` cancels it.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
import structlog
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import dashboard_api

ROOT = Path(__file__).parent.parent


async def _wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.005)
    return predicate()


def _plain_app() -> web.Application:
    # No AppContext and no syncer: the path where _on_startup used to return early.
    return web.Application()


@pytest.mark.asyncio
async def test_task_exists_without_syncer_and_calls_rotation_repeatedly():
    calls: list[int] = []

    def fake_rotate(max_days=120):
        calls.append(max_days)
        return 0

    app = _plain_app()
    assert app.get(dashboard_api.AUDIT_SYNCER_KEY) is None
    with (
        patch.object(dashboard_api, "AUDIT_ROTATE_INTERVAL_SECS", 0.01),
        patch.object(dashboard_api, "AUDIT_LOG_RETENTION_DAYS", 45),
        patch("audit.rotate_logs", side_effect=fake_rotate),
    ):
        await dashboard_api._on_startup(app)
        task = app.get(dashboard_api.AUDIT_ROTATE_TASK_KEY)
        assert isinstance(task, asyncio.Task)
        assert await _wait_until(lambda: len(calls) >= 2)
        assert not task.done()
        await dashboard_api._on_cleanup(app)

    assert set(calls) == {45}


@pytest.mark.asyncio
async def test_loop_sleeps_before_the_first_rotation():
    """The startup rotation already ran in create_app; do not run it twice in a row."""
    calls: list[int] = []
    app = _plain_app()
    with (
        patch.object(dashboard_api, "AUDIT_ROTATE_INTERVAL_SECS", 3600),
        patch("audit.rotate_logs", side_effect=lambda **kw: calls.append(1) or 0),
    ):
        await dashboard_api._on_startup(app)
        await asyncio.sleep(0.05)
        await dashboard_api._on_cleanup(app)
    assert calls == []


@pytest.mark.asyncio
async def test_cleanup_cancels_and_awaits_the_task():
    app = _plain_app()
    with (
        patch.object(dashboard_api, "AUDIT_ROTATE_INTERVAL_SECS", 3600),
        patch("audit.rotate_logs", return_value=0),
    ):
        await dashboard_api._on_startup(app)
        task = app[dashboard_api.AUDIT_ROTATE_TASK_KEY]
        assert not task.done()
        # must neither raise CancelledError nor wait out the interval
        await asyncio.wait_for(dashboard_api._on_cleanup(app), timeout=2.0)
    assert task.cancelled()


@pytest.mark.asyncio
async def test_cleanup_without_a_rotation_task_is_fine():
    await dashboard_api._on_cleanup(_plain_app())


@pytest.mark.asyncio
async def test_rotation_error_is_logged_without_details_and_does_not_kill_the_loop():
    outcomes = [OSError("/data/audit/secret-case-path"), 1, 1]
    calls: list[int] = []

    def fake_rotate(max_days=120):
        calls.append(1)
        item = outcomes[min(len(calls) - 1, len(outcomes) - 1)]
        if isinstance(item, Exception):
            raise item
        return item

    app = _plain_app()
    with structlog.testing.capture_logs() as logs:
        with (
            patch.object(dashboard_api, "AUDIT_ROTATE_INTERVAL_SECS", 0.01),
            patch("audit.rotate_logs", side_effect=fake_rotate),
        ):
            await dashboard_api._on_startup(app)
            assert await _wait_until(lambda: len(calls) >= 3)
            task = app[dashboard_api.AUDIT_ROTATE_TASK_KEY]
            assert not task.done()
            await dashboard_api._on_cleanup(app)

    failures = [e for e in logs if e["event"] == "dashboard.audit.rotation_failed"]
    assert failures, "the failure must be logged"
    assert all("secret-case-path" not in repr(e) for e in logs)
    assert failures[0]["error_type"] == "OSError"
    assert any(e["event"] == "dashboard.audit.rotation_complete" for e in logs)


@pytest.mark.asyncio
async def test_rotation_runs_off_the_event_loop_thread():
    seen: list[int] = []
    main = threading.get_ident()
    app = _plain_app()
    with (
        patch.object(dashboard_api, "AUDIT_ROTATE_INTERVAL_SECS", 0.01),
        patch(
            "audit.rotate_logs",
            side_effect=lambda **kw: seen.append(threading.get_ident()) or 0,
        ),
    ):
        await dashboard_api._on_startup(app)
        assert await _wait_until(lambda: bool(seen))
        await dashboard_api._on_cleanup(app)
    assert seen and all(t != main for t in seen)


@pytest.mark.asyncio
async def test_create_app_wires_the_task_even_when_audit_sync_is_off(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(dashboard_api, "APP_ENV", "development")
    monkeypatch.setattr(dashboard_api, "AUDIT_SYNC_ENABLED", False)
    monkeypatch.setattr(dashboard_api, "AUDIT_ROTATE_INTERVAL_SECS", 3600)
    with patch("audit.rotate_logs", return_value=0):
        app = dashboard_api.create_app(tmp_path)
        async with TestClient(TestServer(app)):
            task = app.get(dashboard_api.AUDIT_ROTATE_TASK_KEY)
            assert isinstance(task, asyncio.Task)
            assert not task.done()
    assert task.done()


def _config_value(tmp_path, env_value: str | None) -> str:
    # A COPY of config.py: load_env() reads a .env next to the module.
    shutil.copy(ROOT / "config.py", tmp_path / "config.py")
    env = {k: v for k, v in os.environ.items() if k != "AUDIT_ROTATE_INTERVAL_SECS"}
    if env_value is not None:
        env["AUDIT_ROTATE_INTERVAL_SECS"] = env_value
    env["PYTHONPATH"] = str(tmp_path)
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "import config; print(config.AUDIT_ROTATE_INTERVAL_SECS)",
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
        check=True,
    )
    return out.stdout.strip()


def test_config_default_is_one_day(tmp_path):
    assert _config_value(tmp_path, None) == "86400"


def test_config_honours_the_env_var(tmp_path):
    assert _config_value(tmp_path, "7200") == "7200"


def test_config_clamps_a_tiny_interval_to_a_sane_floor(tmp_path):
    # 0 / negative / 1 s would make the loop spin on the disk lock.
    assert _config_value(tmp_path, "0") == "60"
    assert _config_value(tmp_path, "-5") == "60"
    assert _config_value(tmp_path, "1") == "60"
