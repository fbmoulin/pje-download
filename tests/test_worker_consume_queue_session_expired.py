"""Focused coverage for worker queue shutdown on fatal session status."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _load_worker_module():
    """Import worker with heavy dependencies mocked out."""
    import importlib
    import os

    import redis as _real_redis

    os.environ.setdefault("DOWNLOAD_BASE_DIR", "/tmp/pje-test-downloads")
    os.environ.setdefault("SESSION_STATE_PATH", "/tmp/pje-test-session.json")

    mock_redis_module = MagicMock()
    mock_redis_module.from_url = MagicMock(return_value=AsyncMock())
    mock_redis_module.ConnectionError = _real_redis.ConnectionError
    mock_redis_module.TimeoutError = _real_redis.TimeoutError
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


@pytest.mark.asyncio
async def test_consume_queue_exits_after_session_expired_without_mni():
    """consume_queue must stop after session_expired when no MNI fallback exists."""
    w = _load_worker_module()
    worker = w.PJeSessionWorker()
    shutdown = asyncio.Event()
    mock_redis = AsyncMock()
    calls = 0

    async def blpop_one_job(*args, **kwargs):
        nonlocal calls
        calls += 1
        return (
            "kratos:pje:jobs",
            json.dumps(
                {
                    "jobId": "J-expire",
                    "numeroProcesso": "1234567-00.2024.8.08.0001",
                }
            ),
        )

    mock_redis.blpop = blpop_one_job
    worker.redis = mock_redis
    worker.mni_client = None
    # The branch under test is the post-job session_expired result. Prevent the
    # pre-BLPOP guard from short-circuiting when no persisted PJe session exists
    # in the test environment.
    worker.is_session_expired = MagicMock(return_value=False)
    worker.download_process = AsyncMock(
        return_value={
            "jobId": "J-expire",
            "numeroProcesso": "1234567-00.2024.8.08.0001",
            "status": "session_expired",
            "arquivosDownloaded": [],
            "errorMessage": "session expired",
        }
    )
    worker._publish_result = AsyncMock()

    with patch.object(w, "log", MagicMock()):
        await asyncio.wait_for(worker.consume_queue(shutdown), timeout=2)

    assert calls == 1
    worker._publish_result.assert_awaited_once()
