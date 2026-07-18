"""Reply queues must expire on their own — the dashboard's cleanup is in-process.

Background
----------
`kratos:pje:results:<batch_id>` reply queues are created implicitly by the
worker's RPUSH and deleted by the dashboard in the `finally` of `_run_batch`
(`dashboard_api.py`). That `finally` runs *in-process*, so it cannot survive the
process dying: on a container restart / redeploy while a batch is still in
`_poll_results_loop`, the key is never deleted and — with no TTL — it lives
forever.

Observed in production 2026-07-18: four orphaned queues (`ttl=-1`) holding 48
undrained messages, stranded when the redis-py 8.0.0 BLPOP bug wedged batches
until the containers were redeployed.

The invariant these tests protect: **every reply-queue write leaves a TTL
behind, and that TTL outlives the longest batch** — so an abandoned queue
self-cleans while a live batch never loses results to expiry.

The second half of that invariant is the dangerous one. A TTL shorter than the
batch ceiling would expire a queue *mid-flight* and silently drop results the
dashboard had not drained yet — reintroducing exactly the "batch failed but the
files are on disk" symptom this is meant to prevent.
"""

import os

import pytest

import config


class TestTTLInvariant:
    """No Redis required — guards the timeout chain."""

    def test_ttl_outlives_the_longest_batch(self):
        assert config.REDIS_RESULT_QUEUE_TTL_SECS > config.BATCH_MAX_DURATION_SECS, (
            f"reply-queue TTL ({config.REDIS_RESULT_QUEUE_TTL_SECS}s) must exceed "
            f"BATCH_MAX_DURATION_SECS ({config.BATCH_MAX_DURATION_SECS}s), or a "
            f"long batch's queue expires mid-flight and undrained results are lost."
        )

    def test_ttl_outlives_the_idle_wait(self):
        assert config.REDIS_RESULT_QUEUE_TTL_SECS > config.RESULT_WAIT_TIMEOUT_SECS, (
            f"reply-queue TTL ({config.REDIS_RESULT_QUEUE_TTL_SECS}s) must exceed "
            f"RESULT_WAIT_TIMEOUT_SECS ({config.RESULT_WAIT_TIMEOUT_SECS}s)."
        )

    def test_ttl_is_bounded(self):
        """A TTL is required — an unbounded key is the leak itself."""
        assert config.REDIS_RESULT_QUEUE_TTL_SECS > 0


@pytest.mark.asyncio
async def test_worker_publish_leaves_a_ttl_on_the_reply_queue():
    """Real-socket proof: after a worker push, the key is NOT immortal.

    A mock cannot catch this — the failure mode is a missing `EXPIRE`, and a
    mock happily reports whatever was called. Only a live server knows whether
    the key would actually expire.
    """
    redis_asyncio = pytest.importorskip("redis.asyncio")

    url = os.getenv("REDIS_URL", "redis://localhost:6379")
    client = redis_asyncio.from_url(
        url,
        decode_responses=True,
        socket_timeout=config.REDIS_SOCKET_TIMEOUT_SECS,
    )
    try:
        try:
            await client.ping()
        except Exception as exc:  # pragma: no cover - env without redis
            pytest.skip(f"redis unreachable at {url}: {exc}")

        queue = "kratos:pje:results:pytest-ttl-probe"
        await client.delete(queue)

        import worker

        await worker.rpush_with_ttl(client, queue, '{"status": "running"}')

        ttl = await client.ttl(queue)
        assert ttl > 0, (
            f"reply queue {queue} has ttl={ttl} (-1 = immortal). Every write "
            f"must leave an expiry or the key leaks forever when the dashboard "
            f"process dies before its finally-block cleanup."
        )
        assert ttl <= config.REDIS_RESULT_QUEUE_TTL_SECS

        # A second write must REFRESH the window, not let it decay — otherwise a
        # long batch's queue would expire while results are still arriving.
        await client.expire(queue, 5)
        await worker.rpush_with_ttl(client, queue, '{"status": "running"}')
        refreshed = await client.ttl(queue)
        assert refreshed > 5, f"second write did not refresh the TTL ({refreshed}s)"

        await client.delete(queue)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_both_worker_publish_paths_set_a_ttl():
    """`_publish_result` and `_publish_progress` must both route through the helper.

    Source-level so a newly added publish site is caught too.
    """
    import ast
    import pathlib

    path = pathlib.Path(__file__).resolve().parent.parent / "worker.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))

    # `pipe.rpush` inside rpush_with_ttl is the sanctioned one; what must never
    # appear is an RPUSH straight onto a client (`self.redis.rpush`, etc.), which
    # writes a message with no expiry attached.
    bare_rpush = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "rpush"
        and not (isinstance(node.func.value, ast.Name) and node.func.value.id == "pipe")
    ]
    assert not bare_rpush, (
        f"worker.py RPUSHes straight onto a redis client at line(s) {bare_rpush}. "
        f"Reply-queue writes must go through rpush_with_ttl() so the key always "
        f"carries an expiry; a bare rpush recreates an immortal key."
    )
