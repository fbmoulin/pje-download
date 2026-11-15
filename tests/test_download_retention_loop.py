"""The dashboard loop that runs `retention.purge_old_downloads` (spec 2026-11-15)."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
import structlog

import config
import dashboard_api
from retention import RetentionResult


async def _wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.005)
    return predicate()


async def _stop(app):
    task = app.get(dashboard_api.RETENTION_TASK_KEY)
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.fixture
def app(tmp_path):
    return dashboard_api.create_app(tmp_path)


class TestConfigIsFailSafe:
    """A typo must never turn into irreversible deletion."""

    @pytest.mark.parametrize(
        "raw, days, invalid",
        [
            ("14", 14, False),
            (" 7 ", 7, False),
            ("0", 0, False),
            ("-5", 0, True),
            ("off", 0, True),
            ("0d", 0, True),
            ("", 0, True),
            ("1.5", 0, True),
        ],
    )
    def test_parse_retention_days(self, raw, days, invalid):
        assert config.parse_retention_days(raw) == (days, invalid)

    @pytest.mark.parametrize(
        "raw, expected", [("3600", 3600), ("10", 60), ("1d", 86400), ("", 86400)]
    )
    def test_interval_typo_falls_back_instead_of_crash_looping(self, raw, expected):
        assert config._retention_interval(raw) == expected

    def test_defaults(self):
        assert config.DOWNLOAD_RETENTION_DAYS == 14
        assert config.DOWNLOAD_RETENTION_DRY_RUN is False  # real deletion, authorized
        assert config.DOWNLOAD_RETENTION_INTERVAL_SECS == 86400

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("true", True),
            ("TRUE", True),
            ("1", True),
            ("no", True),
            ("", True),
            ("false", False),
            (" False ", False),
        ],
    )
    def test_only_a_literal_false_turns_dry_run_off(self, raw, expected):
        assert config.parse_dry_run(raw) is expected


@pytest.mark.asyncio
async def test_runs_once_at_startup_without_waiting_an_interval(app):
    calls = []

    def fake_purge(base, max_days, protect=frozenset(), **kw):
        calls.append((max_days, kw.get("dry_run")))
        return RetentionResult()

    with (
        patch.object(dashboard_api, "DOWNLOAD_RETENTION_DAYS", 14),
        patch.object(dashboard_api, "DOWNLOAD_RETENTION_DRY_RUN", True),
        patch.object(dashboard_api, "DOWNLOAD_RETENTION_INTERVAL_SECS", 3600),
        patch.object(dashboard_api, "purge_old_downloads", side_effect=fake_purge),
    ):
        await dashboard_api._on_startup(app)
        try:
            assert await _wait_until(lambda: len(calls) == 1)
        finally:
            await _stop(app)

    assert calls == [(14, True)]


@pytest.mark.asyncio
async def test_repeats_every_interval(app):
    calls = []

    def fake_purge(base, max_days, protect=frozenset(), **kw):
        calls.append(1)
        return RetentionResult()

    with (
        patch.object(dashboard_api, "DOWNLOAD_RETENTION_DAYS", 14),
        patch.object(dashboard_api, "DOWNLOAD_RETENTION_INTERVAL_SECS", 0.01),
        patch.object(dashboard_api, "purge_old_downloads", side_effect=fake_purge),
    ):
        await dashboard_api._on_startup(app)
        try:
            assert await _wait_until(lambda: len(calls) >= 3)
        finally:
            await _stop(app)


@pytest.mark.asyncio
async def test_disabled_creates_no_task_and_never_purges(app):
    with (
        patch.object(dashboard_api, "DOWNLOAD_RETENTION_DAYS", 0),
        patch.object(dashboard_api, "purge_old_downloads") as purge,
        structlog.testing.capture_logs() as logs,
    ):
        await dashboard_api._on_startup(app)

    assert app.get(dashboard_api.RETENTION_TASK_KEY) is None
    purge.assert_not_called()
    assert any(e["event"] == "dashboard.retention.disabled" for e in logs)


@pytest.mark.asyncio
async def test_invalid_config_logs_an_error_with_the_name_never_the_value(app):
    with (
        patch.object(dashboard_api, "DOWNLOAD_RETENTION_DAYS", 0),
        patch.object(dashboard_api, "DOWNLOAD_RETENTION_DAYS_INVALID", True),
        structlog.testing.capture_logs() as logs,
    ):
        await dashboard_api._on_startup(app)

    errors = [e for e in logs if e["log_level"] == "error"]
    assert errors and errors[0]["variable"] == "DOWNLOAD_RETENTION_DAYS"
    assert "value" not in errors[0]


@pytest.mark.asyncio
async def test_a_failing_pass_does_not_kill_the_loop(app):
    calls = []

    def flaky(base, max_days, protect=frozenset(), **kw):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("disk went away")
        return RetentionResult()

    with (
        patch.object(dashboard_api, "DOWNLOAD_RETENTION_DAYS", 14),
        patch.object(dashboard_api, "DOWNLOAD_RETENTION_INTERVAL_SECS", 0.01),
        patch.object(dashboard_api, "purge_old_downloads", side_effect=flaky),
        structlog.testing.capture_logs() as logs,
    ):
        await dashboard_api._on_startup(app)
        try:
            assert await _wait_until(lambda: len(calls) >= 3)
        finally:
            await _stop(app)

    failed = [e for e in logs if e["event"] == "dashboard.retention.failed"]
    assert failed and failed[0]["error_type"] == "OSError"


@pytest.mark.asyncio
async def test_current_and_running_batches_are_protected(app):
    state = app[dashboard_api.APP_CTX_KEY].state
    state.current_batch_id = "20260101_120000_aaaaaa"
    state.batches["20260102_120000_bbbbbb"] = dashboard_api.BatchJob(
        id="20260102_120000_bbbbbb", processos=[], status="running"
    )
    state.batches["20260103_120000_cccccc"] = dashboard_api.BatchJob(
        id="20260103_120000_cccccc", processos=[], status="done"
    )
    seen = {}

    def fake_purge(base, max_days, protect=frozenset(), **kw):
        seen["protect"] = set(protect)
        return RetentionResult()

    with (
        patch.object(dashboard_api, "DOWNLOAD_RETENTION_DAYS", 14),
        patch.object(dashboard_api, "purge_old_downloads", side_effect=fake_purge),
    ):
        await dashboard_api._on_startup(app)
        try:
            assert await _wait_until(lambda: "protect" in seen)
        finally:
            await _stop(app)

    assert seen["protect"] == {"20260101_120000_aaaaaa", "20260102_120000_bbbbbb"}


@pytest.mark.asyncio
async def test_completion_line_carries_counts_and_never_names(app):
    result = RetentionResult(
        candidates=3,
        purged=2,
        bytes_freed=1234,
        errors=1,
        skipped_active=1,
        unmatched_dirs=1,
        error_type="PermissionError",
    )
    with (
        patch.object(dashboard_api, "DOWNLOAD_RETENTION_DAYS", 14),
        patch.object(dashboard_api, "DOWNLOAD_RETENTION_DRY_RUN", True),
        patch.object(dashboard_api, "purge_old_downloads", return_value=result),
        structlog.testing.capture_logs() as logs,
    ):
        await dashboard_api._on_startup(app)
        try:
            assert await _wait_until(
                lambda: any(e["event"] == "dashboard.retention.complete" for e in logs)
            )
        finally:
            await _stop(app)

    line = next(e for e in logs if e["event"] == "dashboard.retention.complete")
    for key in (
        "candidates",
        "purged",
        "bytes_freed",
        "errors",
        "skipped_active",
        "unmatched_dirs",
        "dry_run",
        "retention_days",
    ):
        assert key in line
    assert line["log_level"] == "warning"  # errors > 0
    assert line["error_type"] == "PermissionError"


@pytest.mark.asyncio
async def test_cleanup_cancels_the_task(app):
    with (
        patch.object(dashboard_api, "DOWNLOAD_RETENTION_DAYS", 14),
        patch.object(
            dashboard_api, "purge_old_downloads", return_value=RetentionResult()
        ),
    ):
        await dashboard_api._on_startup(app)
        task = app.get(dashboard_api.RETENTION_TASK_KEY)
        assert isinstance(task, asyncio.Task)
        await dashboard_api._on_cleanup(app)

    assert task.cancelled() or task.done()


@pytest.mark.asyncio
async def test_dry_run_logs_one_would_purge_line_per_candidate(app):
    result = RetentionResult(
        candidates=2,
        details=[
            {
                "batch_id": "20260101_120000_aaaaaa",
                "status": "done",
                "age_days": 20.0,
                "bytes": 5,
            },
            {
                "batch_id": "20260102_120000_bbbbbb",
                "status": "failed",
                "age_days": 30.0,
                "bytes": 9,
            },
        ],
    )
    with (
        patch.object(dashboard_api, "DOWNLOAD_RETENTION_DAYS", 14),
        patch.object(dashboard_api, "DOWNLOAD_RETENTION_DRY_RUN", True),
        patch.object(dashboard_api, "purge_old_downloads", return_value=result),
        structlog.testing.capture_logs() as logs,
    ):
        await dashboard_api._on_startup(app)
        try:
            assert await _wait_until(
                lambda: any(e["event"] == "dashboard.retention.complete" for e in logs)
            )
        finally:
            await _stop(app)

    lines = [e for e in logs if e["event"] == "dashboard.retention.would_purge"]
    assert [e["batch_id"] for e in lines] == [
        "20260101_120000_aaaaaa",
        "20260102_120000_bbbbbb",
    ]
