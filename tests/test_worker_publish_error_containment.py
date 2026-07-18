"""A publish-path Redis error must not kill the consumer or destroy the result.

Background
----------
`_publish_result`, `_publish_progress` and `_publish_dead_letter` all catch only
`(redis.ConnectionError, redis.TimeoutError, OSError)`. A `redis.ResponseError`
is outside that tuple, and none of their call sites has an enclosing try — they
sit directly in `consume_queue`'s `while` body. So the exception escapes
`consume_queue`, escapes `main()` (which wraps it in `try/finally` with **no
`except`**), and the process exits non-zero.

Why that is not merely noisy: `blpop` removed the job atomically and there is no
ack or processing list, so the job is gone. And `_log_job_result` — the durable
local-log fallback — lives *inside* the non-matching `except`, so it never runs.
The downloaded files sit on disk with no record anywhere.

With `restart: unless-stopped`, the container comes back, takes the *next* job,
and dies the same way: one job destroyed per crash cycle.

Reachability on this deployment is via **MISCONF**, not OOM or WRONGTYPE. Redis
runs RDB snapshots with `stop-writes-on-bgsave-error yes`, so a failed background
save makes every write return `-MISCONF ...`. Verified against the pinned
redis-py 8.0.0: `MISCONF` has no `EXCEPTION_CLASSES` entry and falls through to a
generic `ResponseError`. The likely trigger is a full disk — and this app's core
function is downloading files to disk.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

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

    # Re-assert the real exception classes on the mocked module after reload —
    # same workaround as tests/test_worker.py::_patch_redis_exceptions. Without
    # it, `except (redis.ConnectionError, ...)` sees MagicMocks and raises
    # TypeError instead of exercising the branch under test.
    w.redis.ConnectionError = _real_redis.ConnectionError
    w.redis.TimeoutError = _real_redis.TimeoutError
    w.redis.ResponseError = _real_redis.ResponseError
    return w


MISCONF = (
    "MISCONF Errors writing to the RDB snapshot file. Commands that may modify "
    "the data set are disabled"
)


def _two_job_queue(worker, shutdown):
    """Feed two jobs then block, so the test can prove job 2 was still reached."""
    seen: list[str] = []

    async def blpop(*args, **kwargs):
        if len(seen) >= 2:
            shutdown.set()
            return None
        job_id = f"J-{len(seen) + 1}"
        return (
            "kratos:pje:jobs",
            json.dumps(
                {
                    "jobId": job_id,
                    "numeroProcesso": f"100000{len(seen) + 1}-00.2024.8.08.0001",
                }
            ),
        )

    async def download(job):
        seen.append(job["jobId"])
        return {
            "jobId": job["jobId"],
            "numeroProcesso": job["numeroProcesso"],
            "status": "success",
            "arquivosDownloaded": [{"nome": "doc.pdf"}],
        }

    worker.redis.blpop = blpop
    worker.download_process = download
    worker.is_session_expired = MagicMock(return_value=False)
    return seen


class TestConsumerSurvivesPublishError:
    """The consumer must outlive a publish failure — this is the reported bug."""

    @pytest.mark.asyncio
    async def test_response_error_does_not_kill_the_consumer(self):
        """Job 2 must still be processed after job 1's publish raises MISCONF.

        Fails on current master: the ResponseError escapes consume_queue.
        """
        import redis as _real_redis

        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        worker.redis = AsyncMock()
        worker.mni_client = None
        shutdown = asyncio.Event()
        seen = _two_job_queue(worker, shutdown)

        calls = {"n": 0}

        async def publish(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise _real_redis.ResponseError(MISCONF)

        worker._publish_result = publish

        with patch.object(w, "log", MagicMock()):
            await asyncio.wait_for(worker.consume_queue(shutdown), timeout=5)

        assert seen == ["J-1", "J-2"], (
            f"consumer did not survive a publish-path ResponseError — processed {seen}. "
            f"On master the exception escapes consume_queue, the process exits, and the "
            f"in-flight job is destroyed (blpop already removed it)."
        )

    @pytest.mark.asyncio
    async def test_progress_error_does_not_kill_the_consumer(self):
        """Same for _publish_progress — ~20 call sites, the widest surface."""
        import redis as _real_redis

        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        worker.redis = AsyncMock()
        worker.mni_client = None
        shutdown = asyncio.Event()
        seen: list[str] = []

        async def blpop(*args, **kwargs):
            if len(seen) >= 2:
                shutdown.set()
                return None
            return (
                "kratos:pje:jobs",
                json.dumps(
                    {
                        "jobId": f"J-{len(seen) + 1}",
                        "numeroProcesso": "1000001-00.2024.8.08.0001",
                    }
                ),
            )

        async def download(job):
            seen.append(job["jobId"])
            # download_process emits progress; on master this raise escapes.
            if len(seen) == 1:
                raise _real_redis.ResponseError(MISCONF)
            return {
                "jobId": job["jobId"],
                "numeroProcesso": job["numeroProcesso"],
                "status": "success",
                "arquivosDownloaded": [],
            }

        worker.redis.blpop = blpop
        worker.download_process = download
        worker.is_session_expired = MagicMock(return_value=False)
        worker._publish_result = AsyncMock()

        with patch.object(w, "log", MagicMock()):
            await asyncio.wait_for(worker.consume_queue(shutdown), timeout=5)

        assert seen == ["J-1", "J-2"], (
            f"consumer did not survive an exception raised mid-download — processed {seen}."
        )


class TestResultIsDurableOnPermanentError:
    """A permanent publish error must still preserve the result locally."""

    @pytest.mark.asyncio
    async def test_response_error_falls_back_to_local_log_without_retrying(self):
        """No retry budget burned, and _log_job_result still runs.

        Retrying a MISCONF/WRONGTYPE never succeeds; it only delays the durable
        fallback by the full backoff (~7s per job).
        """
        import redis as _real_redis

        w = _load_worker_module()
        worker = w.PJeSessionWorker()

        pipe = MagicMock()
        pipe.rpush = MagicMock(return_value=pipe)
        pipe.expire = MagicMock(return_value=pipe)
        pipe.execute = AsyncMock(side_effect=_real_redis.ResponseError(MISCONF))
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=pipe)
        cm.__aexit__ = AsyncMock(return_value=False)
        mock_r = AsyncMock()
        mock_r.pipeline = MagicMock(return_value=cm)
        worker.redis = mock_r

        local_log = AsyncMock()
        sleep_mock = AsyncMock()

        with (
            patch.object(w, "log", MagicMock()),
            patch("worker.asyncio.sleep", sleep_mock),
            patch.object(worker, "_log_job_result", local_log),
        ):
            await worker._publish_result(
                {
                    "jobId": "J1",
                    "numeroProcesso": "5000001-00.2024.8.08.0001",
                    "status": "success",
                    "arquivosDownloaded": [{"nome": "doc.pdf"}],
                },
                queue_name="kratos:pje:results:batch-1",
            )

        local_log.assert_awaited_once()
        sleep_mock.assert_not_awaited()


class TestShutdownStillPropagates:
    """Regression guard: containment must not swallow graceful shutdown.

    `asyncio.CancelledError` derives from `BaseException`, so a bare
    `except Exception` already excludes it — this test exists so a future reader
    who "helpfully" widens the handler to `BaseException` breaks a test rather
    than production shutdown.
    """

    @pytest.mark.asyncio
    async def test_cancelled_error_is_not_swallowed(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        worker.redis = AsyncMock()
        worker.mni_client = None
        shutdown = asyncio.Event()

        async def blpop(*args, **kwargs):
            raise asyncio.CancelledError()

        worker.redis.blpop = blpop
        worker.is_session_expired = MagicMock(return_value=False)

        with patch.object(w, "log", MagicMock()):
            with pytest.raises(asyncio.CancelledError):
                await worker.consume_queue(shutdown)
