"""Tests for dashboard_api — max batch size, progress cache, graceful shutdown."""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from prometheus_client import generate_latest

import dashboard_api
from dashboard_api import (
    MAX_BATCH_SIZE,
    MAX_BATCH_HISTORY,
    BatchJob,
    DashboardState,
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
            "status": "failed",
            "created_at": "2024-01-01T11:00:00+00:00",
            "started_at": "2024-01-01T11:05:00+00:00",
            "completed_at": "2024-01-01T12:00:00+00:00",
            "error": "worker timeout",
            "processos": {"1234567-89.2024.8.08.0001": {"status": "done"}},
        }
        (batch_dir / "_report.json").write_text(json.dumps(report), encoding="utf-8")

        ds = DashboardState(tmp_path)
        assert "20240101_120000_abc123" in ds.batches
        loaded = ds.batches["20240101_120000_abc123"]
        assert loaded.status == "failed"
        assert loaded.error == "worker timeout"
        assert loaded.started_at == "2024-01-01T11:05:00+00:00"

    def test_load_active_batch_restores_current_progress(self, tmp_path):
        batch_dir = tmp_path / "20240101_120000_active"
        batch_dir.mkdir()
        active = {
            "batch_id": "20240101_120000_active",
            "processos": ["1234567-89.2024.8.08.0001"],
            "status": "running",
            "created_at": "2024-01-01T11:00:00+00:00",
            "started_at": "2024-01-01T11:05:00+00:00",
            "output_dir": str(batch_dir),
            "include_anexos": True,
            "gdrive_map": {},
            "error": None,
        }
        progress = {
            "summary": {"total": 1, "done": 0, "failed": 0, "partial": 0, "pending": 1},
            "processos": {
                "1234567-89.2024.8.08.0001": {
                    "status": "running",
                    "phase": "mni_metadata",
                    "phase_detail": "Consultando",
                    "total_docs": 0,
                    "docs_baixados": 0,
                    "tamanho_bytes": 0,
                    "erro": None,
                    "duracao_s": None,
                }
            },
        }
        (tmp_path / "_active_batch.json").write_text(
            json.dumps(active), encoding="utf-8"
        )
        (batch_dir / "_progress.json").write_text(
            json.dumps(progress), encoding="utf-8"
        )

        ds = DashboardState(tmp_path)

        assert ds.current_batch_id == "20240101_120000_active"
        loaded = ds.batches["20240101_120000_active"]
        assert loaded.status == "running"
        assert loaded.progress["summary"]["pending"] == 1
        assert (
            loaded.progress["processos"]["1234567-89.2024.8.08.0001"]["phase"]
            == "mni_metadata"
        )
        metrics_output = generate_latest(dashboard_api.metrics.REGISTRY).decode()
        assert "pje_dashboard_active_batch_recoveries_total" in metrics_output

    def test_progress_event_updates_phase_without_completing_batch(self, tmp_path):
        ds = DashboardState(tmp_path)
        job = BatchJob(
            id="batch-progress",
            processos=["5000001-00.2024.8.08.0001"],
            status="running",
            output_dir=str(tmp_path / "batch-progress"),
            progress=ds._build_initial_progress(
                BatchJob(
                    id="batch-progress",
                    processos=["5000001-00.2024.8.08.0001"],
                    output_dir=str(tmp_path / "batch-progress"),
                )
            ),
        )

        ds._apply_progress_event(
            job,
            {
                "numeroProcesso": "5000001-00.2024.8.08.0001",
                "phase": "mni_metadata",
                "phase_detail": "Consultando metadados",
                "docs_baixados": 0,
                "tamanho_bytes": 0,
            },
        )

        proc = job.progress["processos"]["5000001-00.2024.8.08.0001"]
        assert proc["status"] == "running"
        assert proc["phase"] == "mni_metadata"
        assert job.progress["summary"]["pending"] == 1

    def test_apply_result_maps_partial_success_to_partial(self, tmp_path):
        ds = DashboardState(tmp_path)
        job = BatchJob(
            id="batch-partial",
            processos=["5000001-00.2024.8.08.0001"],
            status="running",
            output_dir=str(tmp_path / "batch-partial"),
            progress=ds._build_initial_progress(
                BatchJob(
                    id="batch-partial",
                    processos=["5000001-00.2024.8.08.0001"],
                    output_dir=str(tmp_path / "batch-partial"),
                )
            ),
        )

        worker_status = ds._apply_result(
            job,
            {
                "numeroProcesso": "5000001-00.2024.8.08.0001",
                "status": "partial_success",
                "arquivosDownloaded": [{"nome": "doc.pdf", "tamanhoBytes": 42}],
                "errorMessage": "faltam anexos",
            },
        )

        proc = job.progress["processos"]["5000001-00.2024.8.08.0001"]
        assert worker_status == "partial_success"
        assert proc["status"] == "partial"
        assert proc["phase"] == "partial"
        assert proc["erro"] == "faltam anexos"
        assert job.progress["summary"]["partial"] == 1


# ─────────────────────────────────────────────
# HTTP endpoint tests
# ─────────────────────────────────────────────


class DummyRequest:
    """Minimal request object for direct handler and middleware tests."""

    def __init__(
        self,
        *,
        method: str = "GET",
        json_data=None,
        headers: dict[str, str] | None = None,
        remote: str | None = "127.0.0.1",
        match_info: dict[str, str] | None = None,
        path: str = "/",
    ):
        self.method = method
        self._json_data = json_data
        self.headers = headers or {}
        self.remote = remote
        self.match_info = match_info or {}
        self.path = path
        self.app = {}

    async def json(self):
        if isinstance(self._json_data, Exception):
            raise self._json_data
        return self._json_data


class FakeHealthResponse:
    """Async context manager that mimics aiohttp's response object."""

    def __init__(self, status: int = 200, payload: dict | None = None):
        self.status = status
        self._payload = payload or {}

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeClientSession:
    """Async context manager for patching aiohttp.ClientSession."""

    def __init__(self, *args, response: FakeHealthResponse | None = None, **kwargs):
        self.response = response or FakeHealthResponse()
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def get(self, url):
        return self.response

    async def close(self):
        self.closed = True


class FakeRedis:
    """Minimal async Redis stub for dashboard queue orchestration tests."""

    def __init__(self):
        self.queues: dict[str, list[str]] = {}
        self.closed = False

    async def ping(self):
        return True

    async def close(self):
        self.closed = True

    async def delete(self, key: str):
        self.queues.pop(key, None)
        return 1

    async def rpush(self, key: str, *values: str):
        queue = self.queues.setdefault(key, [])
        queue.extend(values)
        if key == "kratos:pje:jobs":
            for raw in values:
                payload = json.loads(raw)
                result_queue = payload["replyQueue"]
                queue = self.queues.setdefault(result_queue, [])
                queue.append(
                    json.dumps(
                        {
                            "eventType": "progress",
                            "jobId": payload["jobId"],
                            "batchId": payload["batchId"],
                            "numeroProcesso": payload["numeroProcesso"],
                            "status": "running",
                            "phase": "mni_metadata",
                            "phase_detail": "Consultando metadados",
                            "total_docs": 0,
                            "docs_baixados": 0,
                            "tamanho_bytes": 0,
                        }
                    )
                )
                queue.append(
                    json.dumps(
                        {
                            "jobId": payload["jobId"],
                            "batchId": payload["batchId"],
                            "numeroProcesso": payload["numeroProcesso"],
                            "status": "success",
                            "arquivosDownloaded": [
                                {
                                    "nome": "doc.pdf",
                                    "tamanhoBytes": 42,
                                    "checksum": payload["numeroProcesso"],
                                }
                            ],
                            "errorMessage": None,
                            "downloadedAt": "2026-01-01T00:00:00+00:00",
                        }
                    )
                )
        return len(queue)

    async def blpop(self, key: str, timeout: int = 0):
        queue = self.queues.get(key, [])
        if not queue:
            return None
        return key, queue.pop(0)

    async def lrem(self, key: str, count: int, value: str):
        queue = self.queues.get(key, [])
        removed = 0
        kept = []
        for item in queue:
            if item == value and (count == 0 or removed < count):
                removed += 1
                continue
            kept.append(item)
        self.queues[key] = kept
        return removed


async def _ok_handler(request):
    return web.Response(text="ok")


@pytest.mark.asyncio
async def test_handle_download_rejects_above_max():
    """POST /api/download with >500 valid processos must return 422."""
    processos = [f"{i:07d}-01.2024.8.08.0001" for i in range(501)]
    with patch("dashboard_api.state") as mock_state:
        mock_state.current_batch_id = None
        mock_state.batches = {}
        resp = await dashboard_api.handle_download(
            DummyRequest(method="POST", json_data={"processos": processos})
        )
        assert resp.status == 422
        body = json.loads(resp.body.decode())
        assert "500" in body["error"]


@pytest.mark.asyncio
async def test_handle_download_accepts_at_max():
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
        resp = await dashboard_api.handle_download(
            DummyRequest(method="POST", json_data={"processos": processos})
        )
        assert resp.status == 201


@pytest.mark.asyncio
async def test_handle_download_rejects_invalid_format():
    """POST /api/download with invalid CNJ format returns 400."""
    with patch("dashboard_api.state") as mock_state:
        mock_state.current_batch_id = None
        mock_state.batches = {}
        resp = await dashboard_api.handle_download(
            DummyRequest(method="POST", json_data={"processos": ["not-a-processo"]})
        )
        assert resp.status == 400


@pytest.mark.asyncio
async def test_handle_download_rejects_empty():
    with patch("dashboard_api.state") as mock_state:
        mock_state.current_batch_id = None
        mock_state.batches = {}
        resp = await dashboard_api.handle_download(
            DummyRequest(method="POST", json_data={"processos": []})
        )
        assert resp.status == 400


@pytest.mark.asyncio
async def test_handle_progress_when_idle():
    with patch("dashboard_api.state") as mock_state:
        mock_state.get_current_progress.return_value = None
        resp = await dashboard_api.handle_progress(DummyRequest())
        assert resp.status == 200
        body = json.loads(resp.body.decode())
        assert body["status"] == "idle"


@pytest.mark.asyncio
async def test_handle_history_returns_list():
    with patch("dashboard_api.state") as mock_state:
        mock_state.batches = {}
        resp = await dashboard_api.handle_history(DummyRequest())
        assert resp.status == 200
        body = json.loads(resp.body.decode())
        assert isinstance(body, list)


@pytest.mark.asyncio
async def test_handle_status_returns_worker_status():
    """GET /api/status must include worker summary fields."""
    fake_session = FakeClientSession(
        response=FakeHealthResponse(
            200,
            {
                "status": "healthy",
                "healthy": True,
                "checks": {"redis": "healthy"},
                "fallback_ready": False,
            },
        )
    )
    with (
        patch("dashboard_api.state") as mock_state,
        patch(
            "dashboard_api.aiohttp.ClientSession",
            return_value=fake_session,
        ),
    ):
        mock_state.batches = {}
        mock_state.current_batch_id = None
        mock_state.recovered_active_batch_id = None
        mock_state.output_dir.name = "downloads"
        mock_state.get_current_progress.return_value = None
        mock_state.get_worker_http.return_value = fake_session
        resp = await dashboard_api.handle_status(DummyRequest())
        assert resp.status == 200
        body = json.loads(resp.body.decode())
        assert body["worker_status"] == "healthy"
        assert body["worker"]["healthy"] is True
        assert body["worker"]["checks"]["redis"] == "healthy"
        assert body["worker"]["fallback_ready"] is False


@pytest.mark.asyncio
async def test_handle_healthz_reports_ready(tmp_path):
    ds = DashboardState(tmp_path)
    dashboard_api.state = ds
    ds.get_redis = AsyncMock(return_value=AsyncMock(ping=AsyncMock(return_value=True)))

    resp = await dashboard_api.handle_healthz(DummyRequest())

    assert resp.status == 200
    body = json.loads(resp.body.decode())
    assert body["ready"] is True
    assert body["checks"]["redis"] == "healthy"
    assert body["checks"]["active_batch_resume_pending"] is False


@pytest.mark.asyncio
async def test_handle_healthz_reports_resume_pending(tmp_path):
    batch_dir = tmp_path / "batch-r"
    batch_dir.mkdir()
    (tmp_path / "_active_batch.json").write_text(
        json.dumps(
            {
                "batch_id": "batch-r",
                "processos": ["001"],
                "status": "running",
                "created_at": "2026-01-01T00:00:00+00:00",
                "started_at": "2026-01-01T00:00:10+00:00",
                "output_dir": str(batch_dir),
                "include_anexos": True,
                "gdrive_map": {},
                "error": None,
            }
        ),
        encoding="utf-8",
    )
    (batch_dir / "_progress.json").write_text(
        json.dumps(
            {
                "summary": {
                    "total": 1,
                    "done": 0,
                    "failed": 0,
                    "partial": 0,
                    "pending": 1,
                },
                "processos": {"001": {"status": "running", "phase": "waiting"}},
            }
        ),
        encoding="utf-8",
    )

    ds = DashboardState(tmp_path)
    dashboard_api.state = ds
    ds.get_redis = AsyncMock(return_value=AsyncMock(ping=AsyncMock(return_value=True)))

    resp = await dashboard_api.handle_healthz(DummyRequest())

    assert resp.status == 503
    body = json.loads(resp.body.decode())
    assert body["ready"] is False
    assert body["checks"]["active_batch_recovered"] is True
    assert body["checks"]["active_batch_resume_pending"] is True


@pytest.mark.asyncio
async def test_dashboard_state_reuses_worker_http_session(tmp_path):
    from dashboard_api import DashboardState

    with patch(
        "dashboard_api.aiohttp.ClientSession",
        side_effect=lambda *args, **kwargs: FakeClientSession(*args, **kwargs),
    ) as mock_factory:
        ds = DashboardState(tmp_path)
        sess1 = ds.get_worker_http()
        sess2 = ds.get_worker_http()

        assert sess1 is sess2
        assert mock_factory.call_count == 1

        await ds.close()
        assert sess1.closed is True


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
    async def test_get_not_rate_limited(self):
        """GET requests bypass the rate limiter."""
        resp = await dashboard_api.rate_limit_middleware(
            DummyRequest(method="GET"),
            _ok_handler,
        )
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_post_rate_limit_uses_remote_by_default(self, monkeypatch):
        """X-Forwarded-For is ignored unless trust is enabled."""
        remote_ip = "10.88.88.8"
        spoofed_ip = "10.99.99.1"
        dashboard_api._rate_buckets.pop(remote_ip, None)
        dashboard_api._rate_bucket_last_seen.pop(remote_ip, None)
        dashboard_api._rate_buckets.pop(spoofed_ip, None)
        dashboard_api._rate_bucket_last_seen.pop(spoofed_ip, None)
        monkeypatch.setattr(dashboard_api, "TRUST_X_FORWARDED_FOR", False)

        statuses = []
        for _ in range(12):
            resp = await dashboard_api.rate_limit_middleware(
                DummyRequest(
                    method="POST",
                    headers={"X-Forwarded-For": spoofed_ip},
                    remote=remote_ip,
                ),
                _ok_handler,
            )
            statuses.append(resp.status)

        assert 429 in statuses
        assert spoofed_ip not in dashboard_api._rate_buckets
        assert remote_ip in dashboard_api._rate_buckets

    @pytest.mark.asyncio
    async def test_post_rate_limit_can_trust_forwarded_for_when_enabled(
        self, monkeypatch
    ):
        """Forwarded IPs can be trusted only when explicitly enabled."""
        spoofed_ip = "10.99.99.2"
        dashboard_api._rate_buckets.pop(spoofed_ip, None)
        dashboard_api._rate_bucket_last_seen.pop(spoofed_ip, None)
        monkeypatch.setattr(dashboard_api, "TRUST_X_FORWARDED_FOR", True)

        ip = dashboard_api._get_rate_limit_ip(
            DummyRequest(
                method="POST",
                headers={"X-Forwarded-For": f"{spoofed_ip}, 10.1.1.1"},
                remote="10.88.88.8",
            )
        )
        assert ip == spoofed_ip


# ─────────────────────────────────────────────
# CORS middleware
# ─────────────────────────────────────────────


class TestCorsMiddleware:
    @pytest.mark.asyncio
    async def test_allowed_origin_reflected(self):
        """An origin in _ALLOWED_ORIGINS is echoed back in the response header."""
        resp = await dashboard_api.cors_middleware(
            DummyRequest(headers={"Origin": "http://localhost:8007"}),
            _ok_handler,
        )
        assert (
            resp.headers.get("Access-Control-Allow-Origin") == "http://localhost:8007"
        )

    @pytest.mark.asyncio
    async def test_disallowed_origin_defaults_to_localhost(self):
        """An unknown origin falls back to 'http://localhost'."""
        resp = await dashboard_api.cors_middleware(
            DummyRequest(headers={"Origin": "https://evil.com"}),
            _ok_handler,
        )
        assert resp.headers.get("Access-Control-Allow-Origin") == "http://localhost"

    @pytest.mark.asyncio
    async def test_options_preflight_returns_correct_headers(self):
        """OPTIONS preflight returns 200 with CORS method headers."""
        resp = await dashboard_api.cors_middleware(
            DummyRequest(method="OPTIONS", headers={"Origin": "http://localhost"}),
            _ok_handler,
        )
        assert resp.status == 200
        assert "Access-Control-Allow-Methods" in resp.headers
        assert "Access-Control-Allow-Origin" in resp.headers


# ─────────────────────────────────────────────
# API key middleware
# ─────────────────────────────────────────────


class TestApiKeyMiddleware:
    @pytest.mark.asyncio
    async def test_no_key_configured_passes(self, monkeypatch):
        """When DASHBOARD_API_KEY is empty (dev mode), requests are not blocked."""
        import config

        monkeypatch.setattr(config, "DASHBOARD_API_KEY", "")
        resp = await dashboard_api.api_key_middleware(
            DummyRequest(method="POST"),
            _ok_handler,
        )
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_wrong_key_returns_401(self, monkeypatch):
        """When DASHBOARD_API_KEY is set and wrong key is sent, return 401."""
        import config

        monkeypatch.setattr(config, "DASHBOARD_API_KEY", "correct-key")
        resp = await dashboard_api.api_key_middleware(
            DummyRequest(
                method="POST",
                path="/api/download",
                headers={"X-API-Key": "wrong-key"},
            ),
            _ok_handler,
        )
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_get_api_endpoint_without_key_returns_401(self, monkeypatch):
        """GET /api/history (lists CNJ numbers) MUST require auth when key is set.

        Audit P0.1: previously GET requests skipped the middleware entirely,
        leaking batch history + session status to anyone who could reach :8007.
        """
        import config

        monkeypatch.setattr(config, "DASHBOARD_API_KEY", "correct-key")
        for path in [
            "/api/history",
            "/api/batch/abc",
            "/api/session/status",
            "/api/progress",
            "/api/status",
        ]:
            resp = await dashboard_api.api_key_middleware(
                DummyRequest(method="GET", path=path),
                _ok_handler,
            )
            assert resp.status == 401, f"GET {path} should require auth"

    @pytest.mark.asyncio
    async def test_get_api_endpoint_with_valid_key_passes(self, monkeypatch):
        import config

        monkeypatch.setattr(config, "DASHBOARD_API_KEY", "correct-key")
        resp = await dashboard_api.api_key_middleware(
            DummyRequest(
                method="GET",
                path="/api/history",
                headers={"X-API-Key": "correct-key"},
            ),
            _ok_handler,
        )
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_public_paths_never_require_auth(self, monkeypatch):
        """/, /healthz, /metrics, /static/* are public — orchestrators + browsers
        must reach them without the key.
        """
        import config

        monkeypatch.setattr(config, "DASHBOARD_API_KEY", "correct-key")
        for path in ["/", "/healthz", "/metrics", "/static/css/style.css"]:
            resp = await dashboard_api.api_key_middleware(
                DummyRequest(method="GET", path=path),
                _ok_handler,
            )
            assert resp.status == 200, f"public path {path} was blocked"


# ─────────────────────────────────────────────
# _progress.json torn-read resilience (audit P0.3)
# ─────────────────────────────────────────────


class TestProgressTornRead:
    """get_current_progress and handle_batch_detail both read _progress.json
    in the hot path. The writer uses atomic rename; the file can momentarily
    vanish between exists() and read_text() under concurrent rotation. These
    tests pin the behaviour: torn-read NEVER crashes, and the error is
    visible in logs (so ops can tell the difference between "truly empty
    batch" and "silent IO contention").
    """

    @pytest.mark.asyncio
    async def test_get_current_progress_survives_vanished_file(
        self, tmp_path, monkeypatch
    ):
        import structlog

        ds = DashboardState(tmp_path)
        batch_dir = tmp_path / "torn_batch"
        batch_dir.mkdir()
        progress = batch_dir / "_progress.json"
        progress.write_text('{"summary": {"done": 1}, "processos": {}}')

        job = BatchJob(
            id="torn_batch",
            processos=["5000001-00.2024.8.08.0001"],
            status="running",
            output_dir=str(batch_dir),
        )
        ds.batches["torn_batch"] = job
        ds.current_batch_id = "torn_batch"

        import pathlib

        real_read_text = pathlib.Path.read_text

        def flaky_read(self, *args, **kwargs):
            if self.name == "_progress.json":
                raise FileNotFoundError(self)
            return real_read_text(self, *args, **kwargs)

        monkeypatch.setattr(pathlib.Path, "read_text", flaky_read)

        with structlog.testing.capture_logs() as logs:
            result = ds.get_current_progress()

        assert result is not None
        assert result["batch_id"] == "torn_batch"
        assert any(r.get("event") == "dashboard.progress.read_failed" for r in logs), (
            f"torn-read was silent; got logs: {logs!r}"
        )

    @pytest.mark.asyncio
    async def test_handle_batch_detail_survives_vanished_progress(
        self, tmp_path, monkeypatch
    ):
        import structlog

        batch_dir = tmp_path / "td_batch"
        batch_dir.mkdir()
        progress = batch_dir / "_progress.json"
        progress.write_text('{"summary": {"done": 1}, "processos": {}}')

        job = BatchJob(
            id="td_batch",
            processos=["5000001-00.2024.8.08.0001"],
            status="running",
            output_dir=str(batch_dir),
        )

        import pathlib

        real_read_text = pathlib.Path.read_text

        def flaky_read(self, *args, **kwargs):
            if self.name == "_progress.json":
                raise FileNotFoundError(self)
            return real_read_text(self, *args, **kwargs)

        monkeypatch.setattr(pathlib.Path, "read_text", flaky_read)

        with patch("dashboard_api.state") as mock_state:
            mock_state.batches = {"td_batch": job}
            with structlog.testing.capture_logs() as logs:
                resp = await dashboard_api.handle_batch_detail(
                    DummyRequest(match_info={"id": "td_batch"})
                )
        assert resp.status == 200
        assert any(r.get("event") == "dashboard.progress.read_failed" for r in logs), (
            f"torn-read was silent; got logs: {logs!r}"
        )


# ─────────────────────────────────────────────
# handle_batch_detail
# ─────────────────────────────────────────────


class TestHandleBatchDetail:
    @pytest.mark.asyncio
    async def test_unknown_batch_returns_404(self):
        """GET /api/batch/<nonexistent> returns 404."""
        with patch("dashboard_api.state") as mock_state:
            mock_state.batches = {}
            resp = await dashboard_api.handle_batch_detail(
                DummyRequest(match_info={"id": "nonexistent-batch-id"})
            )
            assert resp.status == 404

    @pytest.mark.asyncio
    async def test_known_batch_returns_200_with_data(self, tmp_path):
        """GET /api/batch/<id> returns 200 with full batch payload."""
        job = BatchJob(
            id="test123",
            processos=["5000001-00.2024.8.08.0001"],
            status="done",
            created_at="2024-01-01T00:00:00",
            output_dir=str(tmp_path),
            progress={"total": 1, "done": 1},
        )
        with patch("dashboard_api.state") as mock_state:
            mock_state.batches = {"test123": job}
            resp = await dashboard_api.handle_batch_detail(
                DummyRequest(match_info={"id": "test123"})
            )
            assert resp.status == 200
            body = json.loads(resp.body.decode())
            assert body["batch_id"] == "test123"
            assert body["status"] == "done"
            assert body["processos"] == ["5000001-00.2024.8.08.0001"]


# ─────────────────────────────────────────────
# handle_session_status
# ─────────────────────────────────────────────


class TestHandleSessionStatus:
    @pytest.mark.asyncio
    async def test_returns_expected_fields(self):
        """GET /api/session/status returns file_exists, login_running, last_login_ok."""
        resp = await dashboard_api.handle_session_status(DummyRequest())
        assert resp.status == 200
        body = json.loads(resp.body.decode())
        assert "file_exists" in body
        assert "login_running" in body
        assert "last_login_ok" in body


# ─────────────────────────────────────────────
# handle_index
# ─────────────────────────────────────────────


class TestHandleIndex:
    @pytest.mark.asyncio
    async def test_returns_200_or_404(self, monkeypatch):
        """GET / returns 200 (HTML served) or 404 (dashboard.html not present)."""

        async def fake_to_thread(func, *args, **kwargs):
            return func(*args, **kwargs)

        monkeypatch.setattr(dashboard_api.asyncio, "to_thread", fake_to_thread)
        resp = await dashboard_api.handle_index(DummyRequest())
        assert resp.status in (200, 404)

    @pytest.mark.asyncio
    async def test_returns_404_when_html_missing(self, monkeypatch):
        """When dashboard.html does not exist, endpoint returns 404."""
        from pathlib import Path

        monkeypatch.setattr(
            Path,
            "exists",
            lambda self: False if "dashboard.html" in str(self) else True,
        )
        resp = await dashboard_api.handle_index(DummyRequest())
        # With patched Path.exists, dashboard.html is not found → 404
        assert resp.status == 404


# ─────────────────────────────────────────────
# handle_metrics
# ─────────────────────────────────────────────


class TestHandleMetrics:
    @pytest.mark.asyncio
    async def test_returns_prometheus_text_format(self):
        """GET /metrics returns 200 with Prometheus exposition format."""
        resp = await dashboard_api.handle_metrics(DummyRequest())
        assert resp.status == 200
        text = resp.body.decode()
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

    @pytest.mark.asyncio
    async def test_resume_active_batch_schedules_consumer(self, tmp_path):
        ds = DashboardState(tmp_path)
        job = BatchJob(
            id="resume-1",
            processos=["5000001-00.2024.8.08.0001"],
            status="running",
            output_dir=str(tmp_path / "resume-1"),
            progress={
                "summary": {
                    "total": 1,
                    "done": 0,
                    "failed": 0,
                    "partial": 0,
                    "pending": 1,
                },
                "processos": {
                    "5000001-00.2024.8.08.0001": {
                        "status": "running",
                        "phase": "mni_metadata",
                        "phase_detail": "Consultando",
                        "total_docs": 0,
                        "docs_baixados": 0,
                        "tamanho_bytes": 0,
                        "erro": None,
                        "duracao_s": None,
                    }
                },
            },
        )
        ds.batches[job.id] = job
        ds.current_batch_id = job.id

        called = asyncio.Event()

        async def noop_run(resumed_job: BatchJob, *, enqueue_jobs: bool = True):
            assert resumed_job is job
            assert enqueue_jobs is False
            called.set()

        ds._run_batch = noop_run
        await ds.resume_active_batch()
        assert ds._task is not None
        await asyncio.wait_for(called.wait(), timeout=2.0)


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


# ─────────────────────────────────────────────
# _run_batch
# ─────────────────────────────────────────────


class TestRunBatch:
    @pytest.mark.asyncio
    async def test_run_batch_completes(self, tmp_path):
        """_run_batch persists batch progress from Redis worker results."""
        ds = DashboardState(tmp_path)
        job = BatchJob(
            id="run_ok",
            processos=["5000001-00.2024.8.08.0001"],
            status="queued",
            output_dir=str(tmp_path / "run_ok"),
        )
        ds.batches["run_ok"] = job

        fake_redis = FakeRedis()
        ds.get_redis = AsyncMock(return_value=fake_redis)

        await ds._run_batch(job)

        assert job.status == "done"
        assert job.finished_at is not None
        assert job.progress["summary"]["done"] == 1
        assert (tmp_path / "run_ok" / "_progress.json").exists()
        assert (tmp_path / "run_ok" / "_report.json").exists()

    @pytest.mark.asyncio
    async def test_run_batch_marks_partial_when_worker_returns_partial_success(
        self, tmp_path
    ):
        ds = DashboardState(tmp_path)
        job = BatchJob(
            id="run_partial",
            processos=["5000001-00.2024.8.08.0001"],
            status="queued",
            output_dir=str(tmp_path / "run_partial"),
        )
        ds.batches["run_partial"] = job

        fake_redis = FakeRedis()

        async def rpush_partial(key: str, *values: str):
            queue = fake_redis.queues.setdefault(key, [])
            queue.extend(values)
            if key == "kratos:pje:jobs":
                for raw in values:
                    payload = json.loads(raw)
                    reply_queue = payload["replyQueue"]
                    fake_redis.queues.setdefault(reply_queue, []).append(
                        json.dumps(
                            {
                                "jobId": payload["jobId"],
                                "batchId": payload["batchId"],
                                "numeroProcesso": payload["numeroProcesso"],
                                "status": "partial_success",
                                "arquivosDownloaded": [
                                    {"nome": "doc.pdf", "tamanhoBytes": 42}
                                ],
                                "errorMessage": "faltam anexos",
                                "downloadedAt": "2026-01-01T00:00:00+00:00",
                            }
                        )
                    )
            return len(queue)

        fake_redis.rpush = rpush_partial
        ds.get_redis = AsyncMock(return_value=fake_redis)

        await ds._run_batch(job)

        assert job.status == "partial"
        assert job.error == "1 incompletos"
        assert job.progress["summary"]["partial"] == 1

    @pytest.mark.asyncio
    async def test_run_batch_handles_exception(self, tmp_path):
        """_run_batch catches Redis/control-plane exceptions and sets failed status."""
        ds = DashboardState(tmp_path)
        job = BatchJob(
            id="run_fail",
            processos=["5000001-00.2024.8.08.0001"],
            status="queued",
            output_dir=str(tmp_path / "run_fail"),
        )
        ds.batches["run_fail"] = job

        async def fail_get_redis():
            raise RuntimeError("connection failed")

        ds.get_redis = fail_get_redis

        await ds._run_batch(job)

        assert job.status == "failed"
        assert job.error == "connection failed"
        assert job.finished_at is not None


class TestRuntimeConfigValidation:
    def test_production_requires_dashboard_api_key(self, monkeypatch, tmp_path):
        monkeypatch.setattr(dashboard_api, "APP_ENV", "production")
        with patch("config.DASHBOARD_API_KEY", ""):
            with pytest.raises(RuntimeError, match="DASHBOARD_API_KEY"):
                dashboard_api.create_app(tmp_path)

    def test_create_app_rotates_audit_logs(self, monkeypatch, tmp_path):
        monkeypatch.setattr(dashboard_api, "APP_ENV", "development")
        monkeypatch.setattr(dashboard_api, "AUDIT_LOG_RETENTION_DAYS", 45)
        with patch("audit.rotate_logs", return_value=2) as mock_rotate:
            app = dashboard_api.create_app(tmp_path)
        assert isinstance(app, web.Application)
        mock_rotate.assert_called_once_with(max_days=45)

    def test_create_app_no_syncer_when_audit_sync_disabled(self, monkeypatch, tmp_path):
        monkeypatch.setattr(dashboard_api, "APP_ENV", "development")
        monkeypatch.setattr(dashboard_api, "AUDIT_SYNC_ENABLED", False)
        with patch("audit.rotate_logs", return_value=0):
            app = dashboard_api.create_app(tmp_path)
        assert app.get(dashboard_api.AUDIT_SYNCER_KEY) is None

    def test_create_app_installs_syncer_when_enabled(self, monkeypatch, tmp_path):
        import audit_sync as audit_sync_mod

        monkeypatch.setattr(dashboard_api, "APP_ENV", "development")
        monkeypatch.setattr(dashboard_api, "AUDIT_SYNC_ENABLED", True)
        monkeypatch.setattr(dashboard_api, "DATABASE_URL", "postgres://u:p@h/db")
        monkeypatch.setattr(dashboard_api, "AUDIT_SYNC_CATCHUP_DAYS", 7)
        monkeypatch.setattr(dashboard_api, "AUDIT_LOG_RETENTION_DAYS", 90)
        with patch("audit.rotate_logs", return_value=0):
            app = dashboard_api.create_app(tmp_path)
        assert isinstance(
            app.get(dashboard_api.AUDIT_SYNCER_KEY), audit_sync_mod.AuditSyncer
        )


class TestAuditSyncLifecycle:
    """Verify on_startup / on_cleanup handle the audit syncer gracefully."""

    @pytest.mark.asyncio
    async def test_on_startup_spawns_run_forever_task(self, monkeypatch, tmp_path):
        import audit_sync

        syncer = audit_sync.create_syncer(
            enabled=True,
            database_url="postgres://u:p@h/db",
            audit_dir=tmp_path,
            interval_secs=10,
            batch_size=100,
            catchup_days=7,
            retention_days=90,
            drain_timeout_secs=1.0,
            app_env="development",
            auto_migrate=False,
        )
        assert syncer is not None
        syncer._tick = AsyncMock()

        app = web.Application()
        app[dashboard_api.AUDIT_SYNCER_KEY] = syncer
        dashboard_api.state = None  # skip resume path

        await dashboard_api._on_startup(app)

        task = app.get(dashboard_api.AUDIT_SYNC_TASK_KEY)
        assert isinstance(task, asyncio.Task)
        assert not task.done()

        # cleanup
        syncer.shutdown.set()
        await asyncio.wait_for(task, timeout=2.0)

    @pytest.mark.asyncio
    async def test_on_cleanup_drains_syncer(self, tmp_path):
        import audit_sync

        syncer = audit_sync.create_syncer(
            enabled=True,
            database_url="postgres://u:p@h/db",
            audit_dir=tmp_path,
            interval_secs=10,
            batch_size=100,
            catchup_days=7,
            retention_days=90,
            drain_timeout_secs=1.0,
            app_env="development",
            auto_migrate=False,
        )
        assert syncer is not None
        syncer._tick = AsyncMock()
        syncer.close = AsyncMock()

        app = web.Application()
        app[dashboard_api.AUDIT_SYNCER_KEY] = syncer
        task = asyncio.create_task(syncer.run_forever())
        app[dashboard_api.AUDIT_SYNC_TASK_KEY] = task
        dashboard_api.state = None

        await dashboard_api._on_cleanup(app)

        assert task.done()
        syncer.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_on_cleanup_cancels_on_drain_timeout(self, tmp_path):
        import audit_sync

        syncer = audit_sync.create_syncer(
            enabled=True,
            database_url="postgres://u:p@h/db",
            audit_dir=tmp_path,
            interval_secs=10,
            batch_size=100,
            catchup_days=7,
            retention_days=90,
            drain_timeout_secs=0.1,
            app_env="development",
            auto_migrate=False,
        )
        assert syncer is not None

        # Task ignores shutdown — simulate a hung run
        async def hang():
            while True:
                await asyncio.sleep(60)

        app = web.Application()
        app[dashboard_api.AUDIT_SYNCER_KEY] = syncer
        task = asyncio.create_task(hang())
        app[dashboard_api.AUDIT_SYNC_TASK_KEY] = task
        dashboard_api.state = None
        syncer.close = AsyncMock()

        # Override drain timeout module constant
        from unittest.mock import patch

        with patch.object(dashboard_api, "AUDIT_SYNC_DRAIN_TIMEOUT_SECS", 0.1):
            await dashboard_api._on_cleanup(app)

        assert task.cancelled() or task.done()
        syncer.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_on_startup_resumes_active_batch(self, monkeypatch, tmp_path):
        dashboard_api.state = DashboardState(tmp_path)
        dashboard_api.state.resume_active_batch = AsyncMock()

        await dashboard_api._on_startup(MagicMock())

        dashboard_api.state.resume_active_batch.assert_awaited_once()


# ─────────────────────────────────────────────
# _on_cleanup
# ─────────────────────────────────────────────


class TestOnCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_cancels_tasks(self, tmp_path):
        """_on_cleanup cancels running batch tasks."""
        from dashboard_api import _on_cleanup

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
# Session login audit
# ─────────────────────────────────────────────


class TestSessionLoginAudit:
    @pytest.mark.asyncio
    async def test_audit_called_on_login(self, tmp_path):
        """handle_session_login triggers audit with session_login event."""
        # We test the _do_login inner function by patching interactive_login
        # and audit, then triggering the task and waiting for it.
        dashboard_api._login_running = False
        dashboard_api._login_task = None
        dashboard_api._login_last_ok = None

        with (
            patch("pje_session.interactive_login", return_value=True),
            patch("audit.log_access") as mock_log_access,
        ):
            resp = await dashboard_api.handle_session_login(DummyRequest(method="POST"))
            assert resp.status == 202

            # Wait for the background task to complete
            task = dashboard_api._login_task
            if task:
                await asyncio.wait_for(task, timeout=5.0)

            mock_log_access.assert_called_once()
            entry = mock_log_access.call_args[0][0]
            assert entry.event_type == "session_login"
            assert entry.fonte == "dashboard"
            assert entry.status == "success"

        # Cleanup
        dashboard_api._login_running = False


class TestCleanupSavesProgress:
    @pytest.mark.asyncio
    async def test_progress_saved_on_shutdown(self, tmp_path):
        """_on_cleanup persists batch progress to disk before cancelling."""
        from dashboard_api import _on_cleanup

        batch_dir = tmp_path / "batch-001"
        batch_dir.mkdir()

        ds = DashboardState(tmp_path)
        ds.current_batch_id = "batch-001"
        ds.batches["batch-001"] = BatchJob(
            id="batch-001",
            processos=["001"],
            status="running",
            output_dir=str(batch_dir),
            progress={"total": 5, "done": 3},
        )
        ds._task = None  # no running task
        dashboard_api.state = ds

        await _on_cleanup(MagicMock())

        progress_file = batch_dir / "_progress.json"
        assert progress_file.exists()
        data = json.loads(progress_file.read_text())
        assert data["done"] == 3
