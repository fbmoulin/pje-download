"""Results must survive a dashboard restart and a short Redis outage.

Two gaps found in the 2026-10-04 audit, both on the path "worker finished a
processo, dashboard has not drained the result yet":

1. `DashboardState._run_batch` deleted the reply queue in its `finally` even when
   the task was *cancelled*. `_on_cleanup` cancels it on every dashboard shutdown,
   i.e. every deploy that lands mid-batch. But `resume_active_batch` re-enters
   `_run_batch(enqueue_jobs=False)` and depends on that queue still holding the
   undrained results (see `test_result_queue_ttl.py::TestTTLSurvivesAnOutage`).
   The cleanup threw away exactly what the resume design keeps alive.
2. `worker._publish_result` retried a Redis ConnectionError/Timeout only ~3 times
   (about 3.5 s). A longer blip left the result only in the worker's local log, so
   the files were on disk while the dashboard reported the processo as failed.
"""

from __future__ import annotations

import asyncio
import importlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

import config
import dashboard_api
from tests.test_dashboard_api import FakeRedis
from tests.test_worker import _load_worker_module, _patch_redis_exceptions
from tests.test_worker import _redis_with_pipeline

NUMEROS = ["5000001-00.2024.8.08.0001", "5000002-00.2024.8.08.0001"]


def _job(tmp_path, name):
    return dashboard_api.BatchJob(
        id=name,
        processos=list(NUMEROS),
        status="queued",
        output_dir=str(tmp_path / name),
    )


def _state(tmp_path, job, redis_stub):
    ds = dashboard_api.DashboardState(tmp_path)
    ds.batches[job.id] = job
    ds._redis = redis_stub
    ds.get_redis = AsyncMock(return_value=redis_stub)
    return ds


class _ParkedBlpop(FakeRedis):
    """A worker that already answered one processo; the next BLPOP waits forever."""

    def __init__(self):
        super().__init__()
        self.parked = asyncio.Event()

    async def rpush(self, key, *values):
        queue = self.queues.setdefault(key, [])
        queue.extend(values)
        return len(queue)

    async def blpop(self, key, timeout=0):
        self.parked.set()
        await asyncio.sleep(3600)


class TestCancelledBatchKeepsItsReplyQueue:
    @pytest.mark.asyncio
    async def test_shutdown_cancel_leaves_undrained_results_for_the_resume(
        self, tmp_path
    ):
        job = _job(tmp_path, "cancelled")
        redis_stub = _ParkedBlpop()
        ds = _state(tmp_path, job, redis_stub)
        reply_queue = ds._result_queue(job.id)
        redis_stub.queues[reply_queue] = ['{"jobId": "x", "status": "success"}']

        job.progress = ds._build_initial_progress(job)  # a resumed batch has one
        # the real scenario: a resumed batch (enqueue_jobs=False) keeps the queue
        task = asyncio.create_task(ds._run_batch(job, enqueue_jobs=False))
        await asyncio.wait_for(redis_stub.parked.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert redis_stub.queues.get(reply_queue), (
            "the dashboard was cancelled (deploy/shutdown) and deleted the reply "
            "queue: resume_active_batch would find it empty and lose the results"
        )

    @pytest.mark.asyncio
    async def test_a_batch_that_completes_still_cleans_up(self, tmp_path):
        job = _job(tmp_path, "done")
        redis_stub = FakeRedis()
        ds = _state(tmp_path, job, redis_stub)

        await ds._run_batch(job)

        assert job.status == "done"
        assert ds._result_queue(job.id) not in redis_stub.queues

    @pytest.mark.asyncio
    async def test_a_batch_that_fails_still_cleans_up(self, tmp_path):
        job = _job(tmp_path, "boom")
        redis_stub = FakeRedis()
        redis_stub.blpop = AsyncMock(side_effect=ValueError("boom"))
        ds = _state(tmp_path, job, redis_stub)
        reply_queue = ds._result_queue(job.id)
        redis_stub.queues[reply_queue] = ["leftover"]

        await ds._run_batch(job)

        assert job.status == "failed"
        assert reply_queue not in redis_stub.queues, (
            "only cancellation keeps the queue; a batch the dashboard ended itself "
            "is final and its queue must go"
        )


def _publisher(w, execute_side_effect):
    _patch_redis_exceptions(w)
    worker = w.PJeSessionWorker()
    worker.redis = _redis_with_pipeline(execute_side_effect=execute_side_effect)
    worker._log_job_result = AsyncMock()
    return worker


class TestPublishResultRidesOutAShortOutage:
    @pytest.mark.asyncio
    async def test_default_window_survives_several_consecutive_failures(self):
        w = _load_worker_module()
        outage = [RedisConnectionError("down")] * 5 + [None]
        worker = _publisher(w, outage)

        with (
            patch.object(w, "log", MagicMock()),
            patch.object(w.asyncio, "sleep", new=AsyncMock()),
        ):
            await worker._publish_result(
                {"jobId": "J1", "numeroProcesso": "n", "status": "success"},
                queue_name="kratos:pje:results:batch-1",
            )

        assert worker.redis._pipe.execute.await_count == 6
        worker._log_job_result.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_still_falls_back_to_the_local_log_after_the_window(self):
        w = _load_worker_module()
        worker = _publisher(w, RedisConnectionError("down"))

        with (
            patch.object(w, "log", MagicMock()),
            patch.object(w.asyncio, "sleep", new=AsyncMock()),
        ):
            await worker._publish_result(
                {"jobId": "J1", "numeroProcesso": "n", "status": "success"},
                queue_name="kratos:pje:results:batch-1",
            )

        assert (
            worker.redis._pipe.execute.await_count == config.RESULT_PUBLISH_MAX_ATTEMPTS
        )
        worker._log_job_result.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_total_backoff_covers_a_redis_restart_but_stays_bounded(self):
        w = _load_worker_module()
        worker = _publisher(w, RedisConnectionError("down"))
        sleep = AsyncMock()

        with (
            patch.object(w, "log", MagicMock()),
            patch.object(w.asyncio, "sleep", new=sleep),
        ):
            await worker._publish_result(
                {"jobId": "J1", "status": "success"},
                queue_name="kratos:pje:results:batch-1",
            )

        waited = sum(call.args[0] for call in sleep.await_args_list)
        assert waited >= 30, (
            f"only {waited:.0f}s of retries: a Redis restart takes longer"
        )
        assert waited <= 120, f"{waited:.0f}s blocks the worker's next job too long"
        assert all(call.args[0] <= 10 for call in sleep.await_args_list)

    @pytest.mark.asyncio
    async def test_an_explicit_max_retries_still_wins(self):
        w = _load_worker_module()
        worker = _publisher(w, RedisConnectionError("down"))

        with (
            patch.object(w, "log", MagicMock()),
            patch.object(w.asyncio, "sleep", new=AsyncMock()),
        ):
            await worker._publish_result(
                {"jobId": "J1"},
                max_retries=2,
                queue_name="kratos:pje:results:batch-1",
            )

        assert worker.redis._pipe.execute.await_count == 2


class TestPublishAttemptsConfig:
    def test_default_is_eight(self):
        assert config.RESULT_PUBLISH_MAX_ATTEMPTS == 8

    def test_is_env_configurable_and_at_least_one(self, monkeypatch):
        try:
            monkeypatch.setenv("RESULT_PUBLISH_MAX_ATTEMPTS", "3")
            assert importlib.reload(config).RESULT_PUBLISH_MAX_ATTEMPTS == 3
            monkeypatch.setenv("RESULT_PUBLISH_MAX_ATTEMPTS", "0")
            assert importlib.reload(config).RESULT_PUBLISH_MAX_ATTEMPTS == 1
        finally:
            monkeypatch.undo()
            importlib.reload(config)
