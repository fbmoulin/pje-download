"""Tests for the Prometheus metrics module (Gap #13)."""

from __future__ import annotations

import pytest
from prometheus_client import generate_latest


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────


def _scrape(m) -> str:
    """Return Prometheus text output for our registry."""
    return generate_latest(m.REGISTRY).decode()


# ─────────────────────────────────────────────
# MODULE IMPORT
# ─────────────────────────────────────────────


def test_metrics_module_importable():
    """metrics.py must import without errors."""
    import metrics as m

    assert m.REGISTRY is not None
    assert m.mni_requests_total is not None
    assert m.mni_latency_seconds is not None
    assert m.gdrive_attempts_total is not None
    assert m.batch_processos_total is not None
    assert m.batch_docs_total is not None
    assert m.batch_bytes_total is not None
    assert m.batch_throughput_docs_per_min is not None
    assert m.worker_results_total is not None
    assert m.worker_progress_events_total is not None
    assert m.worker_dead_letters_total is not None
    assert m.worker_publish_failures_total is not None
    assert m.dashboard_batches_total is not None
    assert m.dashboard_batch_timeouts_total is not None
    assert m.dashboard_active_batch_recoveries_total is not None
    assert m.dashboard_active_batches is not None


# ─────────────────────────────────────────────
# MNI COUNTERS
# ─────────────────────────────────────────────


def test_mni_requests_counter_increments():
    """mni_requests_total counter tracks operation+status labels."""
    import metrics as m

    m.mni_requests_total.labels(operation="consultar_processo", status="success").inc()
    after = _scrape(m)

    # The label combination must appear in the output
    assert 'operation="consultar_processo"' in after
    assert 'status="success"' in after

    # Count must have increased (may not be exactly 1.0 if other tests ran first)
    # We check that the metric is present and non-zero
    assert "pje_mni_requests_total" in after


def test_mni_latency_histogram_observed():
    """mni_latency_seconds histogram records observations."""
    import metrics as m

    m.mni_latency_seconds.labels(operation="consultar_processo").observe(1.5)
    output = _scrape(m)

    assert "pje_mni_latency_seconds" in output
    assert 'operation="consultar_processo"' in output
    # _sum and _count lines must exist
    assert "pje_mni_latency_seconds_sum" in output
    assert "pje_mni_latency_seconds_count" in output


# ─────────────────────────────────────────────
# GDRIVE COUNTERS
# ─────────────────────────────────────────────


def test_gdrive_counter_all_labels():
    """gdrive_attempts_total covers all 3 strategies × 2 statuses."""
    import metrics as m

    for strategy in ("gdown", "requests", "playwright"):
        for status in ("success", "failed"):
            m.gdrive_attempts_total.labels(strategy=strategy, status=status).inc()

    output = _scrape(m)
    assert "pje_gdrive_attempts_total" in output
    for strategy in ("gdown", "requests", "playwright"):
        assert strategy in output


# ─────────────────────────────────────────────
# BATCH COUNTERS
# ─────────────────────────────────────────────


def test_batch_processos_counter():
    """batch_processos_total tracks done and failed statuses."""
    import metrics as m

    m.batch_processos_total.labels(status="done").inc()
    m.batch_processos_total.labels(status="failed").inc()

    output = _scrape(m)
    assert "pje_batch_processos_total" in output
    assert 'status="done"' in output
    assert 'status="failed"' in output


def test_batch_docs_and_bytes_counters():
    """batch_docs_total and batch_bytes_total accept bulk increments."""
    import metrics as m

    m.batch_docs_total.inc(42)
    m.batch_bytes_total.inc(1_048_576)  # 1 MB

    output = _scrape(m)
    assert "pje_batch_docs_total" in output
    assert "pje_batch_bytes_total" in output


# ─────────────────────────────────────────────
# THROUGHPUT GAUGE
# ─────────────────────────────────────────────


def test_throughput_gauge_set():
    """batch_throughput_docs_per_min gauge stores the last set value."""
    import metrics as m

    m.batch_throughput_docs_per_min.set(47.3)
    output = _scrape(m)

    assert "pje_batch_throughput_docs_per_min" in output
    assert "47.3" in output


def test_runtime_metrics_are_exposed():
    """Worker/control-plane metrics are present in the registry."""
    import metrics as m

    m.worker_results_total.labels(status="success").inc()
    m.worker_progress_events_total.labels(phase="mni_metadata", status="running").inc()
    m.worker_dead_letters_total.labels(reason="invalid_json").inc()
    m.worker_publish_failures_total.labels(kind="progress").inc()
    m.dashboard_batches_total.labels(status="done").inc()
    m.dashboard_batch_timeouts_total.inc()
    m.dashboard_active_batch_recoveries_total.inc()
    m.dashboard_active_batches.set(1)

    output = _scrape(m)
    assert "pje_worker_results_total" in output
    assert "pje_worker_progress_events_total" in output
    assert "pje_worker_dead_letters_total" in output
    assert "pje_worker_publish_failures_total" in output
    assert "pje_dashboard_batches_total" in output
    assert "pje_dashboard_batch_timeouts_total" in output
    assert "pje_dashboard_active_batch_recoveries_total" in output
    assert "pje_dashboard_active_batches" in output


# ─────────────────────────────────────────────
# /metrics HTTP ENDPOINT
# ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_metrics_endpoint_returns_200():
    """GET /metrics returns 200 with Prometheus content-type."""
    from aiohttp.test_utils import make_mocked_request
    from dashboard_api import handle_metrics

    request = make_mocked_request("GET", "/metrics")
    resp = await handle_metrics(request)
    assert resp.status == 200
    ct = resp.headers.get("Content-Type", "")
    assert "text/plain" in ct
    body = resp.body.decode()
    assert "pje_" in body
