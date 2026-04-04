# pje-download — Gap Analysis Session (2026-04-03)

## Status: COMPLETE — merged to master (8bb1673) + Gap #13 implemented

## What Was Done

Executed all 13 gaps from `docs/plans/2026-04-03-gap-analysis.md`.
Branch `feat/gap-analysis` created, all fixes implemented, 61-test suite written, merged.
Gap #13 (Prometheus metrics) implemented in follow-up session (2026-04-03).

## Fixes Applied

### P0
- **tests/** — 61-test pytest suite: `conftest.py`, `test_batch_downloader.py`, `test_gdrive_downloader.py`, `test_dashboard_api.py`, `test_worker.py`

### P1
- **gdrive_downloader.py:144/178/208** — `timeout=30` on all `session.get()` calls
- **worker.py:invalidate_session()** — added `self._release_session_lock()` at end
- **dashboard_api.py:handle_download** — 422 for >500 processos (`MAX_BATCH_SIZE = 500`)
- **worker.py:init()** — explicit `log.error("pje.mni.credentials_missing")` before creating MNIClient
- **gdrive_downloader.py:201** — confirm token fallback to `"t"` when not found in response body

### P2
- **gdrive_downloader.py:159** — JS file ID regex narrowed from `{25,60}` to `{28,44}`
- **batch_downloader.py:download_batch** — sequential loop → `asyncio.Semaphore(CONCURRENT_DOWNLOADS)` with `asyncio.gather()`
- **gdrive_downloader.py:is_processo_antigo** — added year < 2013 secondary rule via `re.search(r"-\d{2}\.(\d{4})\.", numero)`
- **dashboard_api.py:create_app** — `app.on_cleanup.append(_on_cleanup)` cancels running batch
- **dashboard_api.py:DashboardState** — 1s TTL memory cache in `get_current_progress()` (`_progress_cache`, `_progress_cache_time`)
- **dashboard_api.py:handle_status** — queries `http://localhost:8006/health` with 2s timeout, returns `worker_status` field

### P3
- **batch_downloader.py:load_processos_from_file** — `encoding="utf-8"` → `"utf-8-sig"` (Excel BOM fix)

### P3 — Gap #13: Prometheus Metrics (implemented 2026-04-03)
- **metrics.py** (NEW) — dedicated `CollectorRegistry()`, 7 metrics: `pje_mni_requests_total`, `pje_mni_latency_seconds`, `pje_gdrive_attempts_total`, `pje_batch_processos_total`, `pje_batch_docs_total`, `pje_batch_bytes_total`, `pje_batch_throughput_docs_per_min`
- **mni_client.py** — instrumented `consultar_processo()` (all return paths: success/mni_error/timeout/not_found/auth_failed/error) and `download_documentos()` with latency histogram + requests counter
- **gdrive_downloader.py** — instrumented `_try_gdown()`, `_try_requests_parse()`, `_try_playwright_download()` with strategy×status counter
- **batch_downloader.py** — increments `batch_processos_total`/`batch_docs_total`/`batch_bytes_total` at all 4 done/failed paths in `_download_one()`; sets `batch_throughput_docs_per_min` gauge at end of `download_batch()`
- **dashboard_api.py** — added `GET /metrics` endpoint using `generate_latest(m.REGISTRY)` with `headers={"Content-Type": CONTENT_TYPE_LATEST}` (NOT `content_type=` kwarg — aiohttp rejects charset in content_type)
- **requirements.txt** — added `prometheus_client>=0.21.0`
- **tests/test_metrics.py** (NEW) — 8 tests, all passing
- Total: 61 + 8 = **69 tests passing**

## Key Test Patterns Discovered

- **Lazy import mocking**: All heavy modules (MNIClient, download_gdrive_folder) are imported lazily inside functions — must patch source module (`mni_client.MNIClient`, `gdrive_downloader.download_gdrive_folder`), NOT `batch_downloader.MNIClient`
- **worker.py module-level side effect**: `DOWNLOAD_BASE_DIR.mkdir()` runs on import → must set `DOWNLOAD_BASE_DIR=/tmp/pje-test-downloads` in conftest BEFORE any worker import
- **importlib.reload(w)** required in worker tests to pick up env var changes after monkeypatch
- **aiohttp + prometheus**: use `headers={"Content-Type": CONTENT_TYPE_LATEST}` NOT `content_type=CONTENT_TYPE_LATEST` (aiohttp rejects charset in content_type kwarg)
- **Dedicated CollectorRegistry**: use `CollectorRegistry()` in metrics.py — avoids `ValueError: Duplicated timeseries` when module is re-imported in tests

## Architecture Notes

- `CONCURRENT_DOWNLOADS=3` default in `config.py` — used via `os.getenv()` lazily in `download_batch()`
- progress cache TTL=1s chosen because disk writes are debounced at 500ms (BatchProgress.save) and dashboard polls at 1.5s
- Worker health endpoint: `:8006` (HEALTH_PORT in config.py)
- Metrics endpoint: `GET /metrics` on dashboard port `:8007`

## DoD Verified
- 61 ≥ 40 tests ✓
- POST /api/download 501 processos → 422 ✓
- asyncio.Semaphore(3) concurrent batch ✓
- GET /metrics → 200 + Prometheus text format ✓
- 69 total tests passing ✓
