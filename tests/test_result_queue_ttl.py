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


class TestTTLInvariantEnforcedAtRuntime:
    """The invariant must hold for the values production actually runs with.

    The asserts above only ever see the *defaults*, because that is what the
    suite imports. Both sides of the inequality are independently
    env-overridable, so a deploy can violate it while the whole suite stays
    green — which is precisely the shape of failure this change exists to
    prevent. The guard therefore has to live in the app, at import time.
    """

    @staticmethod
    def _reload_config(monkeypatch, **env):
        import importlib

        for key, value in env.items():
            monkeypatch.setenv(key, value)
        return importlib.reload(config)

    def test_ttl_below_batch_ceiling_is_rejected(self, monkeypatch):
        with pytest.raises(ValueError, match="REDIS_RESULT_QUEUE_TTL_SECS"):
            self._reload_config(
                monkeypatch,
                BATCH_MAX_DURATION_SECS="3600",
                REDIS_RESULT_QUEUE_TTL_SECS="60",
            )

    def test_raising_the_batch_ceiling_alone_is_rejected(self, monkeypatch):
        """The realistic operator mistake: lengthen batches, forget the TTL."""
        with pytest.raises(ValueError, match="REDIS_RESULT_QUEUE_TTL_SECS"):
            self._reload_config(
                monkeypatch,
                BATCH_MAX_DURATION_SECS="86400",
                REDIS_RESULT_QUEUE_TTL_SECS="5400",
            )

    def test_consistent_override_is_accepted(self, monkeypatch):
        cfg = self._reload_config(
            monkeypatch,
            BATCH_MAX_DURATION_SECS="86400",
            REDIS_RESULT_QUEUE_TTL_SECS="90000",
        )
        assert cfg.REDIS_RESULT_QUEUE_TTL_SECS == 90000

    def test_derived_default_still_satisfies_the_invariant(self, monkeypatch):
        """Raising only the ceiling is fine when the TTL is left derived."""
        cfg = self._reload_config(monkeypatch, BATCH_MAX_DURATION_SECS="86400")
        assert cfg.REDIS_RESULT_QUEUE_TTL_SECS > 86400


@pytest.fixture(autouse=True)
def _restore_config():
    """Undo any monkeypatched reload so later tests see the real config."""
    yield
    import importlib

    importlib.reload(config)


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


class TestTTLScope:
    """Only the dashboard's per-batch queues are ours to expire.

    `_publish_result` falls back to the un-suffixed `kratos:pje:results`
    (`worker.py`), which is the **n8n control plane's** queue — drained by an
    out-of-repo consumer on its own cadence. Attaching an expiry to it silently
    rewrites a durability contract we do not own: an n8n workflow paused longer
    than the TTL would return to results that had aged out rather than failed.

    Searching this repo for a consumer finds none, but "no in-repo consumer" is
    not "no consumer" — `worker.py` documents the queue as feeding n8n.
    """

    def test_per_batch_queue_is_in_scope(self):
        import worker

        assert worker.owns_queue_lifecycle("kratos:pje:results:20260718_abc123")

    def test_shared_control_plane_queue_is_out_of_scope(self):
        import worker

        assert not worker.owns_queue_lifecycle("kratos:pje:results")

    def test_unrelated_queue_is_out_of_scope(self):
        import worker

        assert not worker.owns_queue_lifecycle("kratos:pje:jobs")


class TestTTLSurvivesAnOutage:
    """The TTL has to outlive a crash, not just a batch.

    `resume_active_batch` re-enters `_run_batch(enqueue_jobs=False)`, and
    `_enqueue_batch` then skips both the queue delete and the job re-publish
    (`dashboard_api.py`). Resume therefore *depends on the undrained reply queue
    still being there*. Nothing re-arms the TTL while the dashboard is down —
    the window decays from the worker's last write, so an outage longer than the
    TTL loses every result the dashboard had not yet drained, and nothing
    re-queues the work.

    Before reply queues expired at all, that resume drained cleanly. Sizing the
    TTL to the batch ceiling alone would trade an unbounded leak for silent loss
    across any overnight incident.
    """

    MIN_OUTAGE_SURVIVAL_SECS = 12 * 3600

    def test_ttl_survives_an_overnight_outage(self):
        assert config.REDIS_RESULT_QUEUE_TTL_SECS >= self.MIN_OUTAGE_SURVIVAL_SECS, (
            f"reply-queue TTL ({config.REDIS_RESULT_QUEUE_TTL_SECS}s) must outlive a "
            f"realistic outage (>= {self.MIN_OUTAGE_SURVIVAL_SECS}s). Resume does not "
            f"re-enqueue: if the queue expired while the dashboard was down, the "
            f"undrained results are gone and every processo is marked failed with its "
            f"files already on disk."
        )


@pytest.mark.asyncio
async def test_both_worker_publish_paths_set_a_ttl():
    """`_publish_result` and `_publish_progress` must both route through the helper.

    Source-level so a newly added publish site is caught too.
    """
    import ast
    import pathlib

    path = pathlib.Path(__file__).resolve().parent.parent / "worker.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))

    # Any list-write onto a redis CLIENT is suspect, not just `rpush` — an
    # earlier version of this test matched `rpush` alone, which `lpush` walked
    # straight past. The sanctioned writes are the ones queued on the pipeline
    # inside rpush_with_ttl; those have `expire` alongside them.
    sanctioned = _pipeline_body_linenos(tree, "rpush_with_ttl")
    LIST_WRITES = {"rpush", "lpush", "rpushx", "lpushx"}

    offenders = [
        (node.lineno, node.func.attr)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in LIST_WRITES
        and node.lineno not in sanctioned
        # DEAD_LETTER_QUEUE is a separate, deliberately durable sink — not a
        # reply queue, and explicitly out of scope for expiry.
        and not _writes_to_dead_letter(node)
    ]
    assert not offenders, (
        f"worker.py writes to a list straight on a redis client at {offenders}. "
        f"Reply-queue writes must go through rpush_with_ttl() so the key always "
        f"carries an expiry; a bare push recreates a key with no expiry."
    )


def _pipeline_body_linenos(tree, func_name: str) -> set[int]:
    """Line numbers of calls inside the named function — the sanctioned writes."""
    import ast as _ast

    for node in _ast.walk(tree):
        if isinstance(node, _ast.AsyncFunctionDef | _ast.FunctionDef) and (
            node.name == func_name
        ):
            return {n.lineno for n in _ast.walk(node) if isinstance(n, _ast.Call)}
    raise AssertionError(f"{func_name} not found in worker.py")


def _writes_to_dead_letter(node) -> bool:
    import ast as _ast

    first = node.args[0] if node.args else None
    return isinstance(first, _ast.Name) and first.id == "DEAD_LETTER_QUEUE"
