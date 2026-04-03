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

batch_downloader.py
    _download_one()        -> batch_processos_total (status="done"/"failed")
                          -> batch_docs_total / batch_bytes_total (on done)
    download_batch()       -> batch_throughput_docs_per_min (set at completion)

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
