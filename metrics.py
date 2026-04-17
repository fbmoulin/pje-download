"""Prometheus metrics registry for pje-download.

All metrics use a dedicated CollectorRegistry (not the default global) so
that tests can import this module without triggering duplicate-registration
errors when the module is re-imported across test sessions.

Exposed at GET /metrics (text/plain Prometheus format) by dashboard_api.py.

Instrumentation points
----------------------
mni_client.py
    consultar_processo()   -> mni_requests_total + mni_latency_seconds
                             Labels: operation="consultar_processo"
                             Status: success | mni_error | timeout |
                                     not_found | auth_failed | error
    download_documentos()  -> mni_requests_total + mni_latency_seconds
                             Labels: operation="download_documentos"
                             Status: success | error

gdrive_downloader.py
    _try_gdown()           -> gdrive_attempts_total (strategy="gdown")
    _try_requests_parse()  -> gdrive_attempts_total (strategy="requests")
    _try_playwright_*()    -> gdrive_attempts_total (strategy="playwright")
    All functions:           status="success" (files returned) or "failed" (exception)

batch_downloader.py / dashboard_api.py
    CLI and dashboard control plane both update batch_processos_total,
    batch_docs_total and batch_bytes_total so Prometheus reflects the
    real production path as well as offline runs.

worker.py
    _publish_result()      -> worker_results_total(status=...)
    _publish_progress()    -> worker_progress_events_total(phase=..., status=...)
    _publish_dead_letter() -> worker_dead_letters_total(reason=...)
                           -> worker_publish_failures_total(kind=...)

dashboard_api.py
    _load_active_batch()   -> dashboard_active_batch_recoveries_total
    submit/_run_batch()    -> dashboard_active_batches
                           -> dashboard_batches_total(status=...)
                           -> dashboard_batch_timeouts_total

To add instrumentation to a new module
---------------------------------------
    import metrics

    t0 = time.monotonic()
    try:
        ...
        metrics.mni_requests_total.labels(operation="my_op", status="success").inc()
    except Exception:
        metrics.mni_requests_total.labels(operation="my_op", status="error").inc()
        raise
    finally:
        metrics.mni_latency_seconds.labels(operation="my_op").observe(
            time.monotonic() - t0
        )
"""

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

REGISTRY = CollectorRegistry()

# ── MNI SOAP ─────────────────────────────────────────────────────────────────

mni_requests_total = Counter(
    "pje_mni_requests_total",
    "Total MNI SOAP requests by operation and outcome",
    ["operation", "status"],
    registry=REGISTRY,
)
# operation: consultar_processo | download_documentos
# status:    success | mni_error | timeout | not_found | auth_failed | error

mni_latency_seconds = Histogram(
    "pje_mni_latency_seconds",
    "MNI SOAP request latency in seconds",
    ["operation"],
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0],
    registry=REGISTRY,
)

# ── Google Drive ──────────────────────────────────────────────────────────────

gdrive_attempts_total = Counter(
    "pje_gdrive_attempts_total",
    "GDrive download attempts by strategy and outcome",
    ["strategy", "status"],
    registry=REGISTRY,
)
# strategy: gdown | requests | playwright
# status:   success | failed

# ── Batch downloader ─────────────────────────────────────────────────────────

batch_processos_total = Counter(
    "pje_batch_processos_total",
    "Total processes handled by the batch downloader",
    ["status"],
    registry=REGISTRY,
)
# status: done | failed

batch_docs_total = Counter(
    "pje_batch_docs_total",
    "Total documents downloaded across all batches",
    registry=REGISTRY,
)

batch_bytes_total = Counter(
    "pje_batch_bytes_total",
    "Total bytes downloaded across all batches",
    registry=REGISTRY,
)

batch_throughput_docs_per_min = Gauge(
    "pje_batch_throughput_docs_per_min",
    "Documents per minute achieved in the most recent completed batch",
    registry=REGISTRY,
)

# ── Worker/control plane runtime ─────────────────────────────────────────────

worker_results_total = Counter(
    "pje_worker_results_total",
    "Total worker result messages published by terminal status",
    ["status"],
    registry=REGISTRY,
)

worker_progress_events_total = Counter(
    "pje_worker_progress_events_total",
    "Total worker progress events published by phase and status",
    ["phase", "status"],
    registry=REGISTRY,
)

worker_dead_letters_total = Counter(
    "pje_worker_dead_letters_total",
    "Total malformed queue payloads sent to dead-letter storage by reason",
    ["reason"],
    registry=REGISTRY,
)

worker_publish_failures_total = Counter(
    "pje_worker_publish_failures_total",
    "Total Redis publish failures in the worker by message kind",
    ["kind"],
    registry=REGISTRY,
)

dashboard_batches_total = Counter(
    "pje_dashboard_batches_total",
    "Total dashboard batches completed by final status",
    ["status"],
    registry=REGISTRY,
)

dashboard_batch_timeouts_total = Counter(
    "pje_dashboard_batch_timeouts_total",
    "Total dashboard batches that hit worker result timeout",
    registry=REGISTRY,
)

dashboard_active_batch_recoveries_total = Counter(
    "pje_dashboard_active_batch_recoveries_total",
    "Total active batches recovered from disk on dashboard startup",
    registry=REGISTRY,
)

dashboard_active_batches = Gauge(
    "pje_dashboard_active_batches",
    "Number of dashboard batches currently active in the control plane",
    registry=REGISTRY,
)

# ── Audit sync (CNJ 615/2025 Phase 2 — Railway Postgres) ─────────────────────

audit_sync_rows_total = Counter(
    "pje_audit_sync_rows_total",
    "Audit rows written to Railway Postgres by outcome",
    ["status"],  # success | conflict | failed
    registry=REGISTRY,
)

audit_sync_batches_total = Counter(
    "pje_audit_sync_batches_total",
    "Audit sync batches by outcome",
    ["status"],  # success | retry | failed
    registry=REGISTRY,
)

audit_sync_lag_seconds = Gauge(
    "pje_audit_sync_lag_seconds",
    "Event-time lag between newest local JSON-L entry and newest synced row",
    registry=REGISTRY,
)

audit_sync_latency_seconds = Histogram(
    "pje_audit_sync_latency_seconds",
    "Tick latency (read+insert+cursor-advance) in seconds",
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
    registry=REGISTRY,
)

audit_sync_malformed_lines_total = Counter(
    "pje_audit_sync_malformed_lines_total",
    "Malformed (but \\n-terminated) JSON-L lines skipped during sync",
    registry=REGISTRY,
)

audit_sync_files_vanished_total = Counter(
    "pje_audit_sync_files_vanished_total",
    "Cursor referenced an audit file that no longer exists (rotated/deleted)",
    registry=REGISTRY,
)
