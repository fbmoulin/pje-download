"""A batch the dashboard gives up on must stop being worked, and a Redis blip must
not end a batch the worker is still serving.

Two defects in `DashboardState._poll_results_loop` (found in the 2026-10-04 audit):

1. Both timeout paths (idle `RESULT_WAIT_TIMEOUT_SECS`, absolute
   `BATCH_MAX_DURATION_SECS`) marked the remaining processos failed but left their
   job payloads in `kratos:pje:jobs`. Only the fatal-status path did the LREM. So the
   worker kept downloading a batch already reported `failed`, and the next batch
   queued behind those stale jobs, tripping its own idle timeout before the worker
   reached it.
2. The reply-queue `blpop` had no error handling. One `ConnectionError`/`TimeoutError`
   escaped to `_run_batch`'s catch-all: batch `failed`, active marker cleared,
   processos still `queued`, while the worker kept producing results.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

import dashboard_api
from tests.test_dashboard_api import FakeRedis

JOBS = "kratos:pje:jobs"
NUMEROS = [
    "5000001-00.2024.8.08.0001",
    "5000002-00.2024.8.08.0001",
    "5000003-00.2024.8.08.0001",
]


def _job(tmp_path, name):
    job = dashboard_api.BatchJob(
        id=name,
        processos=list(NUMEROS),
        status="queued",
        output_dir=str(tmp_path / name),
    )
    return job


def _state(tmp_path, job):
    ds = dashboard_api.DashboardState(tmp_path)
    ds.batches[job.id] = job
    return ds


def _silent_worker_redis():
    """Takes the jobs and never answers: a worker that is stuck, slow or gone."""
    redis_stub = FakeRedis()

    async def rpush(key, *values):
        queue = redis_stub.queues.setdefault(key, [])
        queue.extend(values)
        return len(queue)

    redis_stub.rpush = rpush
    return redis_stub


class TestTimeoutsPurgeTheJobQueue:
    @pytest.mark.asyncio
    async def test_idle_timeout_removes_the_pending_jobs_from_the_queue(self, tmp_path):
        job = _job(tmp_path, "idle")
        ds = _state(tmp_path, job)
        redis_stub = _silent_worker_redis()
        ds.get_redis = AsyncMock(return_value=redis_stub)

        with patch("dashboard_api.RESULT_WAIT_TIMEOUT_SECS", -1):
            await ds._run_batch(job)

        assert job.status == "failed"
        assert "worker timeout" in job.error.lower()
        assert redis_stub.queues[JOBS] == [], (
            "the dashboard gave up on this batch but its jobs are still queued: "
            "the worker will keep downloading them"
        )

    @pytest.mark.asyncio
    async def test_absolute_timeout_removes_the_pending_jobs_from_the_queue(
        self, tmp_path
    ):
        job = _job(tmp_path, "absolute")
        ds = _state(tmp_path, job)
        redis_stub = _silent_worker_redis()
        ds.get_redis = AsyncMock(return_value=redis_stub)

        with patch("dashboard_api.BATCH_MAX_DURATION_SECS", -1):
            await ds._run_batch(job)

        assert job.status == "failed"
        assert "absolute timeout" in job.error.lower()
        assert redis_stub.queues[JOBS] == []

    @pytest.mark.asyncio
    async def test_a_job_the_worker_already_took_is_simply_not_found(self, tmp_path):
        """LREM only reaches jobs still in the queue; the one in flight is not
        recalled. Removing the others must not depend on it being present."""
        job = _job(tmp_path, "inflight")
        ds = _state(tmp_path, job)
        redis_stub = _silent_worker_redis()
        original_rpush = redis_stub.rpush

        async def rpush_then_worker_takes_one(key, *values):
            result = await original_rpush(key, *values)
            redis_stub.queues[JOBS].pop(0)  # the worker's BLPOP got the first one
            return result

        redis_stub.rpush = rpush_then_worker_takes_one
        ds.get_redis = AsyncMock(return_value=redis_stub)

        with patch("dashboard_api.RESULT_WAIT_TIMEOUT_SECS", -1):
            await ds._run_batch(job)

        assert job.status == "failed"
        assert redis_stub.queues[JOBS] == []

    @pytest.mark.asyncio
    async def test_a_redis_error_while_purging_does_not_hide_the_timeout(
        self, tmp_path
    ):
        job = _job(tmp_path, "purge-fails")
        ds = _state(tmp_path, job)
        redis_stub = _silent_worker_redis()
        redis_stub.lrem = AsyncMock(side_effect=RedisConnectionError("gone"))
        ds.get_redis = AsyncMock(return_value=redis_stub)

        with patch("dashboard_api.BATCH_MAX_DURATION_SECS", -1):
            await ds._run_batch(job)

        assert job.status == "failed"
        assert "absolute timeout" in job.error.lower(), (
            f"the cause must stay the timeout, not the LREM failure: {job.error!r}"
        )
        redis_stub.lrem.assert_awaited()

    @pytest.mark.asyncio
    async def test_a_batch_that_completes_never_touches_the_job_queue(self, tmp_path):
        job = _job(tmp_path, "healthy")
        ds = _state(tmp_path, job)
        redis_stub = FakeRedis()  # answers every job with a success
        redis_stub.lrem = AsyncMock(wraps=redis_stub.lrem)
        ds.get_redis = AsyncMock(return_value=redis_stub)

        await ds._run_batch(job)

        assert job.status == "done"
        redis_stub.lrem.assert_not_awaited()


class _FlakyBlpop(FakeRedis):
    """Raises on the first `failures` BLPOPs, then behaves normally."""

    def __init__(self, failures, exc=RedisConnectionError):
        super().__init__()
        self.failures = failures
        self.exc = exc
        self.blpop_calls = 0

    async def blpop(self, key, timeout=0):
        self.blpop_calls += 1
        if self.failures > 0:
            self.failures -= 1
            raise self.exc("connection reset by peer")
        return await super().blpop(key, timeout)


class TestRedisBlipsWhilePolling:
    @pytest.mark.asyncio
    async def test_a_transient_redis_error_does_not_fail_the_batch(self, tmp_path):
        job = _job(tmp_path, "blip")
        ds = _state(tmp_path, job)
        redis_stub = _FlakyBlpop(failures=2)
        ds.get_redis = AsyncMock(return_value=redis_stub)

        with patch("dashboard_api.asyncio.sleep", new=AsyncMock()):
            await ds._run_batch(job)

        assert job.status == "done", (
            f"one reset on the reply-queue BLPOP failed the whole batch: {job.error!r}"
        )
        assert job.progress["summary"]["done"] == 3
        assert redis_stub.blpop_calls > 2

    @pytest.mark.asyncio
    async def test_a_redis_timeout_error_is_also_survived(self, tmp_path):
        from redis.exceptions import TimeoutError as RedisTimeoutError

        job = _job(tmp_path, "blip-timeout")
        ds = _state(tmp_path, job)
        ds.get_redis = AsyncMock(
            return_value=_FlakyBlpop(failures=1, exc=RedisTimeoutError)
        )

        with patch("dashboard_api.asyncio.sleep", new=AsyncMock()):
            await ds._run_batch(job)

        assert job.status == "done"

    @pytest.mark.asyncio
    async def test_a_long_outage_still_ends_as_a_timeout_instead_of_hanging(
        self, tmp_path
    ):
        job = _job(tmp_path, "outage")
        ds = _state(tmp_path, job)
        redis_stub = _silent_worker_redis()
        redis_stub.blpop = AsyncMock(side_effect=RedisConnectionError("down"))
        redis_stub.lrem = AsyncMock(side_effect=RedisConnectionError("down"))
        ds.get_redis = AsyncMock(return_value=redis_stub)
        sleep = AsyncMock()

        with (
            patch("dashboard_api.RESULT_WAIT_TIMEOUT_SECS", 0.05),
            patch("dashboard_api.asyncio.sleep", new=sleep),
        ):
            await asyncio.wait_for(ds._run_batch(job), timeout=10)

        assert job.status == "failed"
        assert "worker timeout" in job.error.lower()
        assert sleep.await_count >= 1, "must back off instead of spinning on Redis"
        assert all(call.args[0] <= 10 for call in sleep.await_args_list), (
            "the backoff must stay capped"
        )

    @pytest.mark.asyncio
    async def test_an_error_that_is_not_a_redis_error_still_propagates(self, tmp_path):
        """Only connection/timeout errors are retried. Swallowing everything would
        turn a real bug into a silent 360 s wait."""
        job = _job(tmp_path, "bug")
        ds = _state(tmp_path, job)
        redis_stub = _silent_worker_redis()
        redis_stub.blpop = AsyncMock(side_effect=ValueError("boom"))
        ds.get_redis = AsyncMock(return_value=redis_stub)

        await ds._run_batch(job)

        assert job.status == "failed"
        assert "boom" in job.error
