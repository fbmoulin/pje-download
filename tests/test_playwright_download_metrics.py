"""Telemetry for Playwright download waits (Phase 2 Sprint 2, T2.2A).

The Playwright timeouts (``PLAYWRIGHT_*_DOWNLOAD_TIMEOUT_MS``) cannot be tuned
without knowing how long successful downloads take and how often the cap is
hit. ``pje_playwright_download_wait_seconds{operation,outcome}`` records both.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from prometheus_client import generate_latest

import metrics
from tests.test_worker import _fake_locator, _load_worker_module


def _count(operation: str, outcome: str, module=metrics) -> float:
    """Number of observations recorded for (operation, outcome).

    Pass the module the code under test actually holds (``w.metrics``): another
    test re-imports ``metrics``, so this file's own import can be a stale copy.
    """
    return (
        module.REGISTRY.get_sample_value(
            "pje_playwright_download_wait_seconds_count",
            {"operation": operation, "outcome": outcome},
        )
        or 0.0
    )


class PlaywrightTimeoutError(Exception):
    """Stand-in for ``playwright.async_api.TimeoutError`` (same class name)."""


PlaywrightTimeoutError.__name__ = "TimeoutError"


class TestTrackPlaywrightDownload:
    def test_registered_in_dedicated_registry(self):
        with metrics.track_playwright_download("unit_registry"):
            pass
        body = generate_latest(metrics.REGISTRY).decode()
        assert "pje_playwright_download_wait_seconds" in body

    def test_success_is_recorded(self):
        before = _count("unit_ok", "success")
        with metrics.track_playwright_download("unit_ok"):
            pass
        assert _count("unit_ok", "success") == before + 1

    @pytest.mark.parametrize(
        "exc",
        [PlaywrightTimeoutError("t"), asyncio.TimeoutError(), TimeoutError()],
    )
    def test_timeout_is_recorded_and_reraised(self, exc):
        before = _count("unit_to", "timeout")
        with pytest.raises(type(exc)):
            with metrics.track_playwright_download("unit_to"):
                raise exc
        assert _count("unit_to", "timeout") == before + 1

    def test_other_error_is_recorded_as_error_and_reraised(self):
        before_err = _count("unit_err", "error")
        before_to = _count("unit_err", "timeout")
        with pytest.raises(RuntimeError):
            with metrics.track_playwright_download("unit_err"):
                raise RuntimeError("boom")
        assert _count("unit_err", "error") == before_err + 1
        assert _count("unit_err", "timeout") == before_to

    def test_cancellation_is_not_recorded(self):
        """A cancelled wait is neither a slow download nor a failure."""
        before = sum(_count("unit_cancel", o) for o in ("success", "timeout", "error"))
        with pytest.raises(asyncio.CancelledError):
            with metrics.track_playwright_download("unit_cancel"):
                raise asyncio.CancelledError()
        after = sum(_count("unit_cancel", o) for o in ("success", "timeout", "error"))
        assert after == before


def _download_cm(*, value_exc: Exception | None = None, download=None):
    """``page.expect_download(...)`` stand-in: ``async with`` yields an object
    whose awaited ``.value`` raises ``value_exc`` (Playwright raises the
    timeout there, not on ``__aenter__``)."""

    async def _value():
        if value_exc is not None:
            raise value_exc
        return download

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=MagicMock(value=_value()))
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


class TestWorkerFullDownloadInstrumented:
    @pytest.mark.asyncio
    async def test_full_download_timeout_is_counted(self, tmp_path, monkeypatch):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        worker.page = AsyncMock()
        worker.page.locator = MagicMock(return_value=_fake_locator(count=1))
        worker.page.expect_download = MagicMock(
            return_value=_download_cm(value_exc=PlaywrightTimeoutError("cap"))
        )
        worker._detect_captcha = AsyncMock(return_value=False)
        monkeypatch.setattr(w.asyncio, "sleep", AsyncMock())

        before = _count("full_download", "timeout", w.metrics)
        result = await worker._try_full_download_button(
            "5000001-00.2024.8.08.0001", tmp_path
        )

        assert result is None
        assert _count("full_download", "timeout", w.metrics) == before + 1


class TestWorkerIndividualDownloadInstrumented:
    @pytest.mark.asyncio
    async def test_sequential_timeout_is_counted(self, tmp_path):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        worker.page = AsyncMock()
        worker.page.expect_download = MagicMock(
            return_value=_download_cm(value_exc=PlaywrightTimeoutError("cap"))
        )
        worker._detect_captcha = AsyncMock(return_value=False)
        link = MagicMock()
        link.click = AsyncMock()

        before = _count("individual", "timeout", w.metrics)
        files = await worker._download_docs_sequential(
            "5000001-00.2024.8.08.0001", [link], tmp_path
        )

        assert files == []
        assert _count("individual", "timeout", w.metrics) == before + 1

    @pytest.mark.asyncio
    async def test_sequential_success_is_counted(self, tmp_path):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        worker.page = AsyncMock()

        download = MagicMock()
        download.suggested_filename = "doc.pdf"

        async def _save_as(path):
            open(path, "wb").write(b"%PDF-1.4 x")

        download.save_as = _save_as
        worker.page.expect_download = MagicMock(
            return_value=_download_cm(download=download)
        )
        worker._detect_captcha = AsyncMock(return_value=False)
        link = MagicMock()
        link.click = AsyncMock()

        before = _count("individual", "success", w.metrics)
        files = await worker._download_docs_sequential(
            "5000001-00.2024.8.08.0001", [link], tmp_path
        )

        assert len(files) == 1
        assert _count("individual", "success", w.metrics) == before + 1
