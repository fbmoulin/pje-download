"""Regression tests for the redis-py 8.0.0 socket_timeout race.

Background
----------
redis-py 8.0.0 (pulled in by the Dependabot bump in #24, commit ``4da8899``)
changed ``AbstractConnection.__init__``'s ``socket_timeout`` default from
``None`` to ``5``. Both BLPOP call sites in this repo poll with a 5s timeout,
which exactly matched the new default.

A blocking command whose timeout is >= the connection's ``socket_timeout``
ALWAYS loses the race: the socket read deadline fires before (or as) the
server's ``nil`` reply arrives, so ``read_response`` raises
``TimeoutError("Timeout reading from <host>:<port>")`` instead of returning
``None``. Measured in production (redis-py 8.0.0, hiredis 3.4.0):

    BLPOP(timeout=3) -> None            in 3.017s   # 3 < 5, server wins
    BLPOP(timeout=5) -> TimeoutError    in 5.006s   # 5 >= 5, deadline wins
    BLPOP(timeout=8) -> TimeoutError    in 5.008s   # deadline always wins

Consequence: every empty-queue poll raised, the worker's circuit breaker
tripped to ``redis_unreachable`` (/health 503), and the dashboard marked
batches ``failed`` even though the downloaded files were on disk.

The invariant these tests protect: **the connection's socket read deadline
must clear the longest blocking command issued on that connection.**
"""

import os

import pytest

import config


BLOCKING_TIMEOUTS = {
    "REDIS_BLPOP_TIMEOUT_SECS": config.REDIS_BLPOP_TIMEOUT_SECS,
    "RESULT_POLL_BLPOP_TIMEOUT_SECS": config.RESULT_POLL_BLPOP_TIMEOUT_SECS,
}


class TestConfigInvariant:
    """Cheap, deterministic guard — no Redis required.

    This is the test that would have caught the Dependabot bump in #24.
    """

    def test_socket_timeout_clears_every_blocking_timeout(self):
        for name, blocking in BLOCKING_TIMEOUTS.items():
            assert config.REDIS_SOCKET_TIMEOUT_SECS > blocking, (
                f"REDIS_SOCKET_TIMEOUT_SECS ({config.REDIS_SOCKET_TIMEOUT_SECS}) "
                f"must exceed {name} ({blocking}), or that BLPOP always raises "
                f"TimeoutError instead of returning None on an empty queue."
            )

    def test_socket_timeout_is_bounded(self):
        """A deadline is still required — never fall back to None.

        ``socket_timeout=None`` would fix the race but reintroduce the
        pre-8.0 failure mode: a genuinely dead TCP connection hangs the read
        forever, so the worker's circuit breaker never trips.
        """
        assert config.REDIS_SOCKET_TIMEOUT_SECS is not None
        assert config.REDIS_SOCKET_TIMEOUT_SECS > 0


class TestClientsPinSocketTimeout:
    """Both clients must set socket_timeout explicitly.

    Relying on the library default is what broke: the default changed under
    us via an automated dependency bump.
    """

    @pytest.mark.parametrize("filename", ["worker.py", "dashboard_api.py"])
    def test_client_sets_socket_timeout(self, filename):
        _assert_source_pins_socket_timeout(filename)


def _assert_source_pins_socket_timeout(filename: str) -> None:
    """Every ``from_url`` call in the module must pass socket_timeout.

    Source-level assertion (rather than mocking) so that adding a *new*
    client anywhere in the module is also caught.
    """
    import ast
    import pathlib

    path = pathlib.Path(__file__).resolve().parent.parent / filename
    tree = ast.parse(path.read_text(encoding="utf-8"))

    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "from_url"
    ]
    assert calls, f"expected at least one redis.from_url call in {filename}"

    for call in calls:
        kwargs = {kw.arg for kw in call.keywords}
        assert "socket_timeout" in kwargs, (
            f"{filename}:{call.lineno} calls redis.from_url without an explicit "
            f"socket_timeout. redis-py's default is load-bearing here and has "
            f"already changed once (8.0.0: None -> 5)."
        )


@pytest.mark.asyncio
async def test_blpop_on_empty_queue_returns_none_not_timeout():
    """Real-socket reproduction — a mock cannot surface this bug.

    Fails on the pre-fix configuration with
    ``TimeoutError: Timeout reading from <host>:<port>``.
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

        queue = "pje:test:definitely-empty-queue"
        await client.delete(queue)

        # The exact production shape: blocking timeout == the old default.
        result = await client.blpop(queue, timeout=config.REDIS_BLPOP_TIMEOUT_SECS)
        assert result is None, "empty-queue BLPOP must return None, not raise"
    finally:
        await client.aclose()
