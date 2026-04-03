"""Tests for dashboard_api — max batch size, progress cache, graceful shutdown."""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer

from dashboard_api import MAX_BATCH_SIZE, DashboardState, create_app


# ─────────────────────────────────────────────
# MAX_BATCH_SIZE constant
# ─────────────────────────────────────────────


def test_max_batch_size_is_500():
    assert MAX_BATCH_SIZE == 500


# ─────────────────────────────────────────────
# DashboardState
# ─────────────────────────────────────────────


class TestDashboardState:
    def test_init_creates_empty_state(self, tmp_path):
        ds = DashboardState(tmp_path)
        assert ds.batches == {}
        assert ds.current_batch_id is None
        assert ds._task is None

    def test_get_current_progress_returns_none_when_no_batch(self, tmp_path):
        ds = DashboardState(tmp_path)
        assert ds.get_current_progress() is None

    def test_progress_cache_serves_from_memory(self, tmp_path):
        ds = DashboardState(tmp_path)
        # Inject a fake running batch
        from dashboard_api import BatchJob

        job = BatchJob(
            id="testbatch",
            processos=["a"],
            status="running",
            output_dir=str(tmp_path / "testbatch"),
        )
        ds.batches["testbatch"] = job
        ds.current_batch_id = "testbatch"

        # Write a progress file
        batch_dir = tmp_path / "testbatch"
        batch_dir.mkdir()
        progress_data = {
            "summary": {"total": 1, "done": 0, "failed": 0, "pending": 1},
            "processos": {},
        }
        (batch_dir / "_progress.json").write_text(
            json.dumps(progress_data), encoding="utf-8"
        )

        # First call reads from disk
        result1 = ds.get_current_progress()
        assert result1 is not None
        assert result1["batch_id"] == "testbatch"

        # Modify disk file
        progress_data["summary"]["done"] = 1
        (batch_dir / "_progress.json").write_text(
            json.dumps(progress_data), encoding="utf-8"
        )

        # Second call within 1s should use cache (not reflect disk change)
        result2 = ds.get_current_progress()
        assert result2["summary"]["done"] == 0  # cache still has old value

    def test_progress_cache_cleared_on_job_done(self, tmp_path):
        ds = DashboardState(tmp_path)
        ds._progress_cache = {"some": "data"}
        ds._progress_cache_time = time.monotonic()

        from dashboard_api import BatchJob

        job = BatchJob(
            id="b2",
            processos=["x"],
            status="done",
            output_dir=str(tmp_path),
            progress={"total": 1, "done": 1, "failed": 0, "processos": {}},
        )
        ds.batches["b2"] = job
        ds.current_batch_id = "b2"

        result = ds.get_current_progress()
        assert result["status"] == "done"
        assert ds._progress_cache is None  # cleared

    def test_load_history_loads_report_files(self, tmp_path):
        batch_dir = tmp_path / "20240101_120000_abc123"
        batch_dir.mkdir()
        report = {
            "completed_at": "2024-01-01T12:00:00+00:00",
            "processos": {"1234567-89.2024.8.08.0001": {"status": "done"}},
        }
        (batch_dir / "_report.json").write_text(json.dumps(report), encoding="utf-8")

        ds = DashboardState(tmp_path)
        assert "20240101_120000_abc123" in ds.batches


# ─────────────────────────────────────────────
# HTTP endpoint tests
# ─────────────────────────────────────────────


@pytest.fixture
def app(tmp_path):
    return create_app(tmp_path)


@pytest.mark.asyncio
async def test_handle_download_rejects_above_max(app):
    """POST /api/download with >500 valid processos must return 422."""
    processos = [f"{i:07d}-01.2024.8.08.0001" for i in range(501)]
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/download", json={"processos": processos})
        assert resp.status == 422
        body = await resp.json()
        assert "500" in body["error"]


@pytest.mark.asyncio
async def test_handle_download_accepts_at_max(app):
    """POST /api/download with exactly 500 valid processos must not return 422."""
    processos = [f"{i:07d}-01.2024.8.08.0001" for i in range(500)]

    async def fake_submit(ps, include_anexos=True, gdrive_map=None):
        from dashboard_api import BatchJob
        import datetime

        return BatchJob(
            id="fake",
            processos=ps,
            status="queued",
            created_at=datetime.datetime.now().isoformat(),
        )

    with patch("dashboard_api.state") as mock_state:
        mock_state.current_batch_id = None
        mock_state.batches = {}
        mock_state.submit_batch = fake_submit

        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/download", json={"processos": processos})
            # Should not be 422
            assert resp.status != 422


@pytest.mark.asyncio
async def test_handle_download_rejects_invalid_format(app):
    """POST /api/download with invalid CNJ format returns 400."""
    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/api/download", json={"processos": ["not-a-processo"]}
        )
        assert resp.status == 400


@pytest.mark.asyncio
async def test_handle_download_rejects_empty(app):
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/download", json={"processos": []})
        assert resp.status == 400


@pytest.mark.asyncio
async def test_handle_progress_when_idle(app):
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/progress")
        assert resp.status == 200
        body = await resp.json()
        assert body["status"] == "idle"


@pytest.mark.asyncio
async def test_handle_history_returns_list(app):
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/history")
        assert resp.status == 200
        body = await resp.json()
        assert isinstance(body, list)


@pytest.mark.asyncio
async def test_handle_status_returns_worker_status(app):
    """GET /api/status must include worker_status field."""
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/status")
        assert resp.status == 200
        body = await resp.json()
        assert "worker_status" in body


@pytest.mark.asyncio
async def test_graceful_shutdown_cancels_task(tmp_path):
    """`_on_cleanup` must cancel a running batch task."""
    from dashboard_api import _on_cleanup, DashboardState
    import dashboard_api

    ds = DashboardState(tmp_path)
    cancelled = []

    async def long_running():
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    task = asyncio.create_task(long_running())
    ds._task = task
    dashboard_api.state = ds

    await _on_cleanup(MagicMock())
    assert task.cancelled() or cancelled
