"""Tests for dashboard_api — max batch size, progress cache, graceful shutdown."""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer

import dashboard_api
from dashboard_api import (
    MAX_BATCH_SIZE,
    MAX_BATCH_HISTORY,
    BatchJob,
    DashboardState,
    create_app,
)


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


# ─────────────────────────────────────────────
# Rate limit middleware
# ─────────────────────────────────────────────


class TestRateLimitMiddleware:
    @pytest.mark.asyncio
    async def test_get_not_rate_limited(self, app):
        """GET requests bypass the rate limiter — 15 requests all succeed."""
        async with TestClient(TestServer(app)) as client:
            for _ in range(15):
                resp = await client.get("/api/progress")
                assert resp.status == 200

    @pytest.mark.asyncio
    async def test_post_rate_limit_exceeded(self, app):
        """After 10 POST requests in window, 429 is returned."""
        # Use a unique X-Forwarded-For IP to isolate this test from others
        async with TestClient(TestServer(app)) as client:
            # Use a unique IP so other tests don't pollute the bucket
            headers = {"X-Forwarded-For": "10.99.99.1"}
            # Clear any leftover state for this IP
            dashboard_api._rate_buckets.pop("10.99.99.1", None)
            dashboard_api._rate_bucket_last_seen.pop("10.99.99.1", None)

            statuses = []
            for _ in range(12):
                resp = await client.post(
                    "/api/download",
                    json={"processos": ["invalid"]},
                    headers=headers,
                )
                statuses.append(resp.status)
            # At least one request beyond limit should get 429
            assert 429 in statuses


# ─────────────────────────────────────────────
# CORS middleware
# ─────────────────────────────────────────────


class TestCorsMiddleware:
    @pytest.mark.asyncio
    async def test_allowed_origin_reflected(self, app):
        """An origin in _ALLOWED_ORIGINS is echoed back in the response header."""
        async with TestClient(TestServer(app)) as client:
            resp = await client.get(
                "/api/progress", headers={"Origin": "http://localhost:8007"}
            )
            assert (
                resp.headers.get("Access-Control-Allow-Origin")
                == "http://localhost:8007"
            )

    @pytest.mark.asyncio
    async def test_disallowed_origin_defaults_to_localhost(self, app):
        """An unknown origin falls back to 'http://localhost'."""
        async with TestClient(TestServer(app)) as client:
            resp = await client.get(
                "/api/progress", headers={"Origin": "https://evil.com"}
            )
            assert resp.headers.get("Access-Control-Allow-Origin") == "http://localhost"

    @pytest.mark.asyncio
    async def test_options_preflight_returns_correct_headers(self, app):
        """OPTIONS preflight returns 200 with CORS method headers."""
        async with TestClient(TestServer(app)) as client:
            resp = await client.options(
                "/api/download", headers={"Origin": "http://localhost"}
            )
            assert resp.status == 200
            assert "Access-Control-Allow-Methods" in resp.headers
            assert "Access-Control-Allow-Origin" in resp.headers


# ─────────────────────────────────────────────
# API key middleware
# ─────────────────────────────────────────────


class TestApiKeyMiddleware:
    @pytest.mark.asyncio
    async def test_no_key_configured_passes(self, app, monkeypatch):
        """When DASHBOARD_API_KEY is empty (dev mode), requests are not blocked."""
        import config

        monkeypatch.setattr(config, "DASHBOARD_API_KEY", "")
        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/api/download", json={"processos": ["not-a-processo"]}
            )
            assert resp.status != 401

    @pytest.mark.asyncio
    async def test_wrong_key_returns_401(self, tmp_path, monkeypatch):
        """When DASHBOARD_API_KEY is set and wrong key is sent, return 401."""
        import config

        monkeypatch.setattr(config, "DASHBOARD_API_KEY", "correct-key")
        _app = create_app(tmp_path)
        async with TestClient(TestServer(_app)) as client:
            resp = await client.post(
                "/api/download",
                json={"processos": ["0000001-01.2024.8.08.0001"]},
                headers={"X-API-Key": "wrong-key"},
            )
            assert resp.status == 401


# ─────────────────────────────────────────────
# handle_batch_detail
# ─────────────────────────────────────────────


class TestHandleBatchDetail:
    @pytest.mark.asyncio
    async def test_unknown_batch_returns_404(self, app):
        """GET /api/batch/<nonexistent> returns 404."""
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/batch/nonexistent-batch-id")
            assert resp.status == 404

    @pytest.mark.asyncio
    async def test_known_batch_returns_200_with_data(self, app, tmp_path):
        """GET /api/batch/<id> returns 200 with full batch payload."""
        job = BatchJob(
            id="test123",
            processos=["5000001-00.2024.8.08.0001"],
            status="done",
            created_at="2024-01-01T00:00:00",
            output_dir=str(tmp_path),
            progress={"total": 1, "done": 1},
        )
        dashboard_api.state.batches["test123"] = job
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/batch/test123")
            assert resp.status == 200
            body = await resp.json()
            assert body["batch_id"] == "test123"
            assert body["status"] == "done"
            assert body["processos"] == ["5000001-00.2024.8.08.0001"]


# ─────────────────────────────────────────────
# handle_session_status
# ─────────────────────────────────────────────


class TestHandleSessionStatus:
    @pytest.mark.asyncio
    async def test_returns_expected_fields(self, app):
        """GET /api/session/status returns file_exists, login_running, last_login_ok."""
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/session/status")
            assert resp.status == 200
            body = await resp.json()
            assert "file_exists" in body
            assert "login_running" in body
            assert "last_login_ok" in body


# ─────────────────────────────────────────────
# handle_index
# ─────────────────────────────────────────────


class TestHandleIndex:
    @pytest.mark.asyncio
    async def test_returns_200_or_404(self, app):
        """GET / returns 200 (HTML served) or 404 (dashboard.html not present)."""
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/")
            assert resp.status in (200, 404)

    @pytest.mark.asyncio
    async def test_returns_404_when_html_missing(self, app, monkeypatch):
        """When dashboard.html does not exist, endpoint returns 404."""
        from pathlib import Path

        monkeypatch.setattr(
            Path,
            "exists",
            lambda self: False if "dashboard.html" in str(self) else True,
        )
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/")
            # With patched Path.exists, dashboard.html is not found → 404
            assert resp.status == 404


# ─────────────────────────────────────────────
# handle_metrics
# ─────────────────────────────────────────────


class TestHandleMetrics:
    @pytest.mark.asyncio
    async def test_returns_prometheus_text_format(self, app):
        """GET /metrics returns 200 with Prometheus exposition format."""
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/metrics")
            assert resp.status == 200
            text = await resp.text()
            # Prometheus format contains HELP or TYPE comment lines
            assert "HELP" in text or "TYPE" in text or "#" in text


# ─────────────────────────────────────────────
# _evict_old_batches
# ─────────────────────────────────────────────


class TestEvictOldBatches:
    def test_evicts_oldest_completed_over_limit(self, tmp_path):
        """When done batches exceed MAX_BATCH_HISTORY, oldest are evicted."""
        ds = DashboardState(tmp_path)
        for i in range(MAX_BATCH_HISTORY + 10):
            job = BatchJob(
                id=f"batch_{i:04d}",
                processos=["x"],
                status="done",
                finished_at=f"2024-01-{(i % 28) + 1:02d}T00:00:00",
            )
            ds.batches[job.id] = job
        ds._evict_old_batches()
        done_count = sum(1 for b in ds.batches.values() if b.status == "done")
        assert done_count <= MAX_BATCH_HISTORY

    def test_does_not_evict_current_batch(self, tmp_path):
        """current_batch_id is protected from eviction even if it is 'done'."""
        ds = DashboardState(tmp_path)
        for i in range(MAX_BATCH_HISTORY + 5):
            job = BatchJob(
                id=f"b{i}",
                processos=["x"],
                status="done",
                finished_at=f"2024-01-{(i % 28) + 1:02d}T00:00:00",
            )
            ds.batches[job.id] = job
        ds.current_batch_id = "b0"
        ds._evict_old_batches()
        assert "b0" in ds.batches

    def test_running_batches_not_evicted(self, tmp_path):
        """Batches with status 'running' or 'queued' are never evicted."""
        ds = DashboardState(tmp_path)
        for i in range(MAX_BATCH_HISTORY + 5):
            job = BatchJob(
                id=f"done_{i}",
                processos=["x"],
                status="done",
                finished_at=f"2024-01-{(i % 28) + 1:02d}T00:00:00",
            )
            ds.batches[job.id] = job
        running = BatchJob(id="running_1", processos=["x"], status="running")
        ds.batches["running_1"] = running
        ds._evict_old_batches()
        assert "running_1" in ds.batches


# ─────────────────────────────────────────────
# submit_batch
# ─────────────────────────────────────────────


class TestSubmitBatch:
    @pytest.mark.asyncio
    async def test_creates_batch_with_correct_fields(self, tmp_path):
        """submit_batch creates a queued BatchJob and sets current_batch_id."""
        ds = DashboardState(tmp_path)

        # Replace _run_batch with a no-op to avoid real download attempts
        async def noop_run(job: BatchJob):
            pass

        ds._run_batch = noop_run

        processos = ["5000001-00.2024.8.08.0001"]
        job = await ds.submit_batch(processos)

        assert job.status == "queued"
        assert job.processos == processos
        assert ds.current_batch_id == job.id
        assert job.id in ds.batches

    @pytest.mark.asyncio
    async def test_creates_background_task(self, tmp_path):
        """submit_batch schedules an asyncio task."""
        ds = DashboardState(tmp_path)

        async def noop_run(job: BatchJob):
            pass

        ds._run_batch = noop_run

        await ds.submit_batch(["5000001-00.2024.8.08.0001"])
        assert ds._task is not None
        # Cancel the task to clean up
        ds._task.cancel()
        try:
            await ds._task
        except (asyncio.CancelledError, Exception):
            pass


# ─────────────────────────────────────────────
# _purge_stale_buckets
# ─────────────────────────────────────────────


class TestPurgeStaleBuckets:
    def test_removes_stale_ips(self):
        """IPs inactive for >300s are removed from both dicts."""
        now = time.monotonic()
        stale_ip = "10.0.0.1"
        active_ip = "10.0.0.2"

        dashboard_api._rate_buckets[stale_ip] = [now - 400]
        dashboard_api._rate_bucket_last_seen[stale_ip] = now - 400
        dashboard_api._rate_buckets[active_ip] = [now]
        dashboard_api._rate_bucket_last_seen[active_ip] = now

        dashboard_api._purge_stale_buckets(now)

        assert stale_ip not in dashboard_api._rate_buckets
        assert stale_ip not in dashboard_api._rate_bucket_last_seen
        assert active_ip in dashboard_api._rate_buckets

        # Cleanup
        dashboard_api._rate_buckets.pop(active_ip, None)
        dashboard_api._rate_bucket_last_seen.pop(active_ip, None)

    def test_keeps_recently_seen_ips(self):
        """IPs seen within the expiry window are retained."""
        now = time.monotonic()
        ip = "10.0.0.3"

        dashboard_api._rate_buckets[ip] = [now - 10]
        dashboard_api._rate_bucket_last_seen[ip] = now - 10

        dashboard_api._purge_stale_buckets(now)

        assert ip in dashboard_api._rate_buckets

        # Cleanup
        dashboard_api._rate_buckets.pop(ip, None)
        dashboard_api._rate_bucket_last_seen.pop(ip, None)
