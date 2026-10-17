"""The job the worker is running when it dies must come back, a bounded number of times.

Background
----------
`consume_queue` used to take jobs with ``BLPOP``: the job left Redis before any work
started, so a worker stopped mid-job (every deploy sends SIGTERM and, 10 s later,
SIGKILL; OOM and crashes do the same) lost it. The dashboard then waited
``RESULT_WAIT_TIMEOUT_SECS`` (360 s) and failed that processo with "Worker timeout".

Now the worker takes jobs with ``BLMOVE kratos:pje:jobs -> kratos:pje:jobs:processing``
and removes the exact bytes from the processing list (``LREM``) once the job is
finished, dead-lettered or dropped. Whatever is still in the processing list when the
worker boots was interrupted: it goes back to the head of the queue, at most
``JOB_MAX_REDELIVERIES`` times. Past that it is dead-lettered (``redelivery_exhausted``)
and a ``failed`` result is published, so a job that kills the worker cannot loop.

Attempts are counted by the sha256 of the payload, not by ``jobId``: an unparseable
payload has no ``jobId``, and ``deploy.yml`` pushes one on every deploy to exercise the
dead-letter queue (premortem 2026-10-17, finding 1).

Spec: docs/specs/2026-10-16-job-perdido-no-deploy.md (Tasks 3 and 4).
These tests talk to a real Redis; they skip when none is reachable, like
tests/test_result_queue_ttl.py. CI publishes one on 6379.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from tests.test_worker_publish_error_containment import _load_worker_module

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")


def _job(job_id: str = "J-1", **extra) -> str:
    payload = {"jobId": job_id, "numeroProcesso": f"PROC-{job_id}", **extra}
    return json.dumps(payload)


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@pytest_asyncio.fixture
async def env():
    """A worker wired to a real Redis, on key names private to this test."""
    import redis.asyncio as real_redis

    import config

    client = real_redis.from_url(
        REDIS_URL,
        decode_responses=True,
        socket_timeout=config.REDIS_SOCKET_TIMEOUT_SECS,
    )
    try:
        await client.ping()
    except Exception as exc:  # pragma: no cover - env without redis
        await client.aclose()
        pytest.skip(f"redis unreachable at {REDIS_URL}: {exc}")

    w = _load_worker_module()
    prefix = f"pje:test:redelivery:{uuid.uuid4().hex}"
    keys = {
        "JOB_QUEUE": f"{prefix}:jobs",
        "PROCESSING_QUEUE": f"{prefix}:jobs:processing",
        "ATTEMPTS_KEY": f"{prefix}:jobs:attempts",
        "DEAD_LETTER_QUEUE": f"{prefix}:dead-letter",
    }
    reply = f"{prefix}:reply"
    worker = w.PJeSessionWorker()
    worker.redis = client
    worker.mni_client = None
    worker.is_session_expired = MagicMock(return_value=False)
    with patch.multiple(w, **keys), patch.object(w, "log", MagicMock()):
        yield {"w": w, "worker": worker, "r": client, "reply": reply, **keys}
    await client.delete(*keys.values(), reply)
    await client.aclose()


async def _run_one(env, download):
    """Run consume_queue until the queue is empty, with ``download`` as the job body."""
    worker = env["worker"]
    shutdown = asyncio.Event()

    async def body(job):
        try:
            return await download(job)
        finally:
            if not await env["r"].llen(env["JOB_QUEUE"]):
                shutdown.set()

    worker.download_process = body
    await asyncio.wait_for(worker.consume_queue(shutdown), timeout=20)


def _ok(job):
    return {
        "jobId": job["jobId"],
        "numeroProcesso": job["numeroProcesso"],
        "status": "success",
        "arquivosDownloaded": [],
    }


# --- Task 3: consumption --------------------------------------------------------


class TestConsumptionKeepsTheJobUntilItEnds:
    @pytest.mark.asyncio
    async def test_running_job_sits_in_processing_not_in_the_queue(self, env):
        r = env["r"]
        raw = _job(replyQueue=env["reply"])
        await r.rpush(env["JOB_QUEUE"], raw)
        seen = {}

        async def download(job):
            seen["processing"] = await r.lrange(env["PROCESSING_QUEUE"], 0, -1)
            seen["queue"] = await r.lrange(env["JOB_QUEUE"], 0, -1)
            return _ok(job)

        await _run_one(env, download)
        assert seen == {"processing": [raw], "queue": []}

    @pytest.mark.asyncio
    async def test_published_result_empties_processing(self, env):
        r = env["r"]
        await r.rpush(env["JOB_QUEUE"], _job(replyQueue=env["reply"]))

        async def download(job):
            return _ok(job)

        await _run_one(env, download)
        assert await r.llen(env["PROCESSING_QUEUE"]) == 0
        (result,) = await r.lrange(env["reply"], 0, -1)
        assert json.loads(result)["status"] == "success"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "raw", ["{invalid-json", json.dumps({"numeroProcesso": "PROC-X"})]
    )
    async def test_dead_lettered_payload_leaves_processing(self, env, raw):
        r = env["r"]
        worker = env["worker"]
        await r.rpush(env["JOB_QUEUE"], raw)
        shutdown = asyncio.Event()
        real_dead_letter = worker._publish_dead_letter

        async def dead_letter(*args, **kwargs):
            await real_dead_letter(*args, **kwargs)
            shutdown.set()

        worker._publish_dead_letter = dead_letter
        await asyncio.wait_for(worker.consume_queue(shutdown), timeout=20)
        assert await r.llen(env["PROCESSING_QUEUE"]) == 0
        assert await r.llen(env["DEAD_LETTER_QUEUE"]) == 1

    @pytest.mark.asyncio
    async def test_dropped_job_leaves_processing(self, env):
        r = env["r"]
        await r.rpush(env["JOB_QUEUE"], _job(replyQueue=env["reply"]))

        async def download(job):
            raise RuntimeError("boom")

        await _run_one(env, download)
        assert await r.llen(env["PROCESSING_QUEUE"]) == 0

    @pytest.mark.asyncio
    async def test_cancelled_job_stays_in_processing(self, env):
        """SIGKILL cannot be caught; cancellation is the in-process stand-in."""
        r = env["r"]
        raw = _job(replyQueue=env["reply"])
        await r.rpush(env["JOB_QUEUE"], raw)

        async def download(job):
            raise asyncio.CancelledError

        env["worker"].download_process = download
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(
                env["worker"].consume_queue(asyncio.Event()), timeout=20
            )
        assert await r.lrange(env["PROCESSING_QUEUE"], 0, -1) == [raw]

    @pytest.mark.asyncio
    async def test_finished_job_clears_its_attempt_counter(self, env):
        r = env["r"]
        raw = _job(replyQueue=env["reply"])
        await r.rpush(env["JOB_QUEUE"], raw)
        await r.hset(env["ATTEMPTS_KEY"], _digest(raw), 1)

        async def download(job):
            return _ok(job)

        await _run_one(env, download)
        assert not await r.hexists(env["ATTEMPTS_KEY"], _digest(raw))


# --- Task 4: redelivery on boot -------------------------------------------------


class TestRedeliveryOnBoot:
    @pytest.mark.asyncio
    async def test_interrupted_job_goes_back_to_the_head_unchanged(self, env):
        r = env["r"]
        interrupted = _job("J-OLD", replyQueue=env["reply"])
        await r.rpush(env["PROCESSING_QUEUE"], interrupted)
        await r.rpush(env["JOB_QUEUE"], _job("J-NEW"))

        assert await env["worker"]._redeliver_unacked_jobs() == 1

        assert await r.llen(env["PROCESSING_QUEUE"]) == 0
        assert await r.lindex(env["JOB_QUEUE"], 0) == interrupted
        assert await r.hget(env["ATTEMPTS_KEY"], _digest(interrupted)) == "1"
        assert 0 < await r.ttl(env["ATTEMPTS_KEY"])

    @pytest.mark.asyncio
    async def test_empty_processing_is_a_no_op(self, env):
        assert await env["worker"]._redeliver_unacked_jobs() == 0
        assert await env["r"].exists(env["ATTEMPTS_KEY"]) == 0

    @pytest.mark.asyncio
    async def test_redeliveries_are_bounded_and_end_in_a_failed_result(self, env):
        r = env["r"]
        w = env["w"]
        raw = _job("J-POISON", replyQueue=env["reply"], batchId="B-1")
        worker = env["worker"]

        for _ in range(w.JOB_MAX_REDELIVERIES):
            await r.rpush(env["PROCESSING_QUEUE"], raw)  # the worker died on it
            assert await worker._redeliver_unacked_jobs() == 1
            assert await r.lpop(env["JOB_QUEUE"]) == raw

        await r.rpush(env["PROCESSING_QUEUE"], raw)
        assert await worker._redeliver_unacked_jobs() == 0

        assert await r.llen(env["JOB_QUEUE"]) == 0
        assert await r.llen(env["PROCESSING_QUEUE"]) == 0
        assert not await r.hexists(env["ATTEMPTS_KEY"], _digest(raw))
        (entry,) = await r.lrange(env["DEAD_LETTER_QUEUE"], 0, -1)
        entry = json.loads(entry)
        assert (entry["reason"], entry["payload"]) == ("redelivery_exhausted", raw)
        (result,) = await r.lrange(env["reply"], 0, -1)
        result = json.loads(result)
        assert (result["status"], result["numeroProcesso"], result["batchId"]) == (
            "failed",
            "PROC-J-POISON",
            "B-1",
        )

    @pytest.mark.asyncio
    async def test_unparseable_payload_is_bounded_too(self, env):
        """Premortem F1: no jobId to count by, and deploy.yml pushes one every deploy."""
        r = env["r"]
        w = env["w"]
        raw = "{invalid-json"
        worker = env["worker"]

        boots = 0
        while True:
            await r.rpush(env["PROCESSING_QUEUE"], raw)
            boots += 1
            if not await worker._redeliver_unacked_jobs():
                break
            assert await r.lpop(env["JOB_QUEUE"]) == raw
            assert boots <= w.JOB_MAX_REDELIVERIES + 1, "redelivery never stops"

        assert boots == w.JOB_MAX_REDELIVERIES + 1
        assert await r.llen(env["DEAD_LETTER_QUEUE"]) == 1


class TestRedeliveryRunsOnlyAfterTheSessionIsUp:
    """Premortem F2: a boot that fails before consuming must not spend attempts."""

    @staticmethod
    async def _run_main(w, session_ok):
        calls: list[str] = []

        class FakeWorker:
            async def init(self):
                calls.append("init")

            async def start_health_server(self):
                pass

            async def load_session(self, playwright):
                calls.append("load_session")
                return session_ok

            async def _redeliver_unacked_jobs(self):
                calls.append("redeliver")
                return 0

            async def consume_queue(self, shutdown_event):
                calls.append("consume")

            async def close(self):
                pass

        playwright_cm = MagicMock()
        playwright_cm.__aenter__ = AsyncMock(return_value=MagicMock())
        playwright_cm.__aexit__ = AsyncMock(return_value=False)
        with (
            patch.object(w, "PJeSessionWorker", FakeWorker),
            patch.object(w, "async_playwright", MagicMock(return_value=playwright_cm)),
        ):
            await w.main()
        return calls

    @pytest.mark.asyncio
    async def test_failed_session_does_not_redeliver(self):
        w = _load_worker_module()
        calls = await self._run_main(w, session_ok=False)
        assert "redeliver" not in calls

    @pytest.mark.asyncio
    async def test_redelivery_happens_after_session_and_before_consuming(self):
        w = _load_worker_module()
        calls = await self._run_main(w, session_ok=True)
        assert calls == ["init", "load_session", "redeliver", "consume"]
