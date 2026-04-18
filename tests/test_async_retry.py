"""Tests for async_retry.AsyncRetry — the generic backoff helper."""

from __future__ import annotations

import asyncio

import pytest

from async_retry import AsyncRetry


async def _no_sleep(_delay):
    """No-op async sleep to speed up retry tests without recursing into the
    same ``asyncio.sleep`` that was patched."""
    return None


class _RetryableError(Exception):
    """Marker for tests so we don't accidentally catch Python built-ins."""


class _NonRetryableError(Exception):
    """Should propagate without retry."""


class _FakeLogger:
    """Captures .warning() calls for assertion; drop-in for structlog."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def warning(self, event: str, **fields) -> None:
        self.calls.append((event, fields))


class TestAsyncRetrySuccess:
    @pytest.mark.asyncio
    async def test_returns_value_on_first_attempt_without_retry(self):
        retry = AsyncRetry(
            attempts=5,
            backoff_cap_secs=1,
            retry_on=(_RetryableError,),
        )
        call_count = 0

        async def op():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = await retry.run(op)

        assert result == "ok"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_returns_value_after_retries(self, monkeypatch):
        # Keep the test fast by stubbing out asyncio.sleep
        import async_retry as _ar

        monkeypatch.setattr(_ar.asyncio, "sleep", _no_sleep)

        retry = AsyncRetry(
            attempts=5,
            backoff_cap_secs=1,
            retry_on=(_RetryableError,),
        )
        call_count = 0

        async def op():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise _RetryableError(f"attempt {call_count}")
            return "recovered"

        result = await retry.run(op)

        assert result == "recovered"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_coro_factory_creates_fresh_coro_each_time(self, monkeypatch):
        """A retried operation must work — this regresses on using raw coroutine."""
        import async_retry as _ar

        monkeypatch.setattr(_ar.asyncio, "sleep", _no_sleep)

        retry = AsyncRetry(
            attempts=3,
            backoff_cap_secs=1,
            retry_on=(_RetryableError,),
        )
        call_count = 0

        async def op():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _RetryableError("first")
            return "done"

        # Passing a zero-arg lambda creates a fresh coroutine each attempt.
        result = await retry.run(op)
        assert result == "done"
        assert call_count == 2


class TestAsyncRetryExhaustion:
    @pytest.mark.asyncio
    async def test_reraises_last_exception_when_all_attempts_exhausted(
        self, monkeypatch
    ):
        import async_retry as _ar

        monkeypatch.setattr(_ar.asyncio, "sleep", _no_sleep)

        retry = AsyncRetry(
            attempts=3,
            backoff_cap_secs=1,
            retry_on=(_RetryableError,),
        )
        call_count = 0

        async def always_fails():
            nonlocal call_count
            call_count += 1
            raise _RetryableError(f"attempt {call_count}")

        with pytest.raises(_RetryableError, match="attempt 3"):
            await retry.run(always_fails)

        assert call_count == 3

    @pytest.mark.asyncio
    async def test_non_retryable_error_propagates_immediately(self):
        retry = AsyncRetry(
            attempts=5,
            backoff_cap_secs=1,
            retry_on=(_RetryableError,),
        )
        call_count = 0

        async def op():
            nonlocal call_count
            call_count += 1
            raise _NonRetryableError("nope")

        with pytest.raises(_NonRetryableError, match="nope"):
            await retry.run(op)

        # Only the first attempt was made — no retries on unmatched exception
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_cancelled_error_always_propagates(self):
        """CancelledError must NOT be swallowed even if included in retry_on
        (asyncio shutdown must work). Users should never put it in retry_on,
        but we don't defensively block it — the concern is that structured
        cancellation still works in the happy case."""
        retry = AsyncRetry(
            attempts=5,
            backoff_cap_secs=1,
            retry_on=(
                _RetryableError,
            ),  # intentionally does NOT include CancelledError
        )

        async def op():
            raise asyncio.CancelledError("shutdown")

        with pytest.raises(asyncio.CancelledError):
            await retry.run(op)


class TestAsyncRetryLogging:
    @pytest.mark.asyncio
    async def test_logs_each_retry_attempt_with_fields(self, monkeypatch):
        import async_retry as _ar

        monkeypatch.setattr(_ar.asyncio, "sleep", _no_sleep)

        logger = _FakeLogger()
        retry = AsyncRetry(
            attempts=4,
            backoff_cap_secs=1,
            retry_on=(_RetryableError,),
            log_event="test.retry",
            logger=logger,
        )
        call_count = 0

        async def op():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise _RetryableError("still failing")
            return "ok"

        await retry.run(op, key="mykey", processo="12345")

        # 2 retries = 2 log entries (the successful 3rd attempt doesn't log)
        assert len(logger.calls) == 2
        event, fields = logger.calls[0]
        assert event == "test.retry"
        assert fields["attempt"] == 1
        assert fields["key"] == "mykey"
        assert fields["processo"] == "12345"
        assert fields["error"] == "still failing"
        assert "delay_s" in fields

    @pytest.mark.asyncio
    async def test_no_logging_when_logger_none(self, monkeypatch):
        """logger=None must not raise when iteration would otherwise log."""
        import async_retry as _ar

        monkeypatch.setattr(_ar.asyncio, "sleep", _no_sleep)

        retry = AsyncRetry(
            attempts=3,
            backoff_cap_secs=1,
            retry_on=(_RetryableError,),
            log_event="ignored",
            logger=None,
        )

        async def op():
            raise _RetryableError("x")

        with pytest.raises(_RetryableError):
            await retry.run(op)


class TestAsyncRetryValidation:
    def test_rejects_zero_attempts(self):
        with pytest.raises(ValueError, match="attempts must be"):
            AsyncRetry(
                attempts=0,
                backoff_cap_secs=1,
                retry_on=(Exception,),
            )

    def test_accepts_single_attempt(self):
        # attempts=1 is valid (no retry — single shot with exception propagation).
        retry = AsyncRetry(
            attempts=1,
            backoff_cap_secs=1,
            retry_on=(Exception,),
        )
        assert retry.attempts == 1
