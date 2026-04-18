"""Generic exponential-backoff retry helper for async callables.

Previously there were 3 hand-rolled retry loops with identical structure:

- ``worker.PJeSessionWorker.init`` — 5 attempts on Redis connection errors
- ``dashboard_api._rpush_with_retry`` — 3 attempts on Redis errors
- ``worker._try_official_api`` — 3 attempts on 5xx or exception

The first two share identical semantics (catch specific exception types,
exponential backoff + jitter, re-raise on exhaustion). The third is NOT a
fit — it retries on HTTP 5xx status codes (not exceptions) and returns
``None`` on exhaustion (not re-raise). Keep that one specialised.

This module covers the first two. Usage::

    from async_retry import AsyncRetry

    redis_retry = AsyncRetry(
        attempts=5,
        backoff_cap_secs=30,
        retry_on=(redis.ConnectionError, redis.TimeoutError, OSError),
        log_event="pje.redis.init_retry",
        logger=log,
    )
    await redis_retry.run(
        lambda: _ping_and_init_redis(),
        # extra kwargs are forwarded to the log event
    )

The ``coro_factory`` is a zero-arg callable returning a fresh coroutine on
each attempt — required because awaited coroutines cannot be reused.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any, Awaitable, Callable


class AsyncRetry:
    """Execute an async callable with exponential-backoff + jitter retry.

    Attributes:
        attempts: Maximum total attempts, including the first. Must be ``>= 1``.
        backoff_cap_secs: Upper bound on per-iteration sleep. Formula:
            ``min(2**attempt + random.uniform(0, 1), backoff_cap_secs)``.
        retry_on: Tuple of exception types that trigger a retry. Any other
            exception propagates immediately (important for ``CancelledError``).
        log_event: Structured-log event name emitted on each retry attempt.
            Omit/empty-string to skip logging.
        logger: structlog (or stdlib) logger for retry events. Must expose a
            ``.warning(event, **fields)`` method. ``None`` silences retries.
    """

    def __init__(
        self,
        *,
        attempts: int,
        backoff_cap_secs: float,
        retry_on: tuple[type[BaseException], ...],
        log_event: str = "retry",
        logger: Any = None,
    ) -> None:
        if attempts < 1:
            raise ValueError(f"attempts must be >= 1, got {attempts}")
        self.attempts = attempts
        self.backoff_cap_secs = backoff_cap_secs
        self.retry_on = retry_on
        self.log_event = log_event
        self.logger = logger

    async def run(
        self,
        coro_factory: Callable[[], Awaitable[Any]],
        **log_extra: Any,
    ) -> Any:
        """Execute ``coro_factory()`` with retry, returning its result.

        ``coro_factory`` MUST be a zero-arg callable returning a new coroutine
        on each invocation. Passing an already-awaited coroutine will raise
        ``RuntimeError`` on retry.

        ``log_extra`` keyword arguments are forwarded verbatim to the structured
        log event (e.g. ``processo="12345"`` becomes a log field).

        Raises the last caught exception on final-attempt exhaustion, so callers
        can propagate it upstream with the same type as the hand-rolled version.
        """
        last_exc: BaseException | None = None
        for attempt in range(self.attempts):
            try:
                return await coro_factory()
            except self.retry_on as exc:
                last_exc = exc
                if attempt == self.attempts - 1:
                    break
                delay = min(2**attempt + random.uniform(0, 1), self.backoff_cap_secs)
                if self.logger is not None and self.log_event:
                    self.logger.warning(
                        self.log_event,
                        attempt=attempt + 1,
                        delay_s=round(delay, 1),
                        error=str(exc),
                        **log_extra,
                    )
                await asyncio.sleep(delay)
        assert last_exc is not None, "retry_on never caught; how did we get here?"
        raise last_exc
