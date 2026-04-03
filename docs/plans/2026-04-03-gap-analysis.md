# Action Plan: pje-download — Gap Analysis & Optimization

> Created: 2026-04-03 | Status: APPROVED

| # | Gap | File | Effort | Priority | Dependencies |
|---|-----|------|--------|----------|--------------|
| 1 | **No test suite** — 0 tests for async+SOAP+browser code. Any change is blind. | `tests/` | High | P0 | None |
| 2 | **GDrive requests: no timeout** — `session.get()` inside `asyncio.to_thread` can hang forever on slow GDrive responses | `gdrive_downloader.py:118` | Low | P1 | None |
| 3 | **Session lock not released on invalidate** — `_release_session_lock()` never called in `invalidate_session()` → orphaned `.lock` file on restart | `worker.py` | Low | P1 | None |
| 4 | **No max batch size** — `POST /api/download` accepts 10,000+ processos, triggering runaway downloads with no guardrail | `dashboard_api.py` | Low | P1 | None |
| 5 | **MNI credentials not validated in worker `init()`** — worker silently sets `mni_client = None` without telling user why | `worker.py:init` | Low | P1 | None |
| 6 | **GDrive confirm token regex outdated** — Google now uses `&confirm=t` (cookie-based); old regex `confirm=([a-zA-Z0-9_-]+)` fails for large files | `gdrive_downloader.py:167` | Medium | P1 | None |
| 7 | **GDrive false-positive file IDs** — broad JS regex `\["([a-zA-Z0-9_-]{25,60})"` matches non-file-ID strings → wastes requests on wrong URLs | `gdrive_downloader.py:138` | Low | P2 | None |
| 8 | **Sequential batch** — processes downloaded one-at-a-time; `CONCURRENT_DOWNLOADS` is set but unused in `batch_downloader.py`. MNI SOAP supports parallel consultarProcesso | `batch_downloader.py` | Medium | P2 | #1 |
| 9 | **`is_processo_antigo` fragile heuristic** — only checks "starts with 5"; processes starting with 1-4 treated as antigo. Needs year-based cutoff (< 2013 = antigo) as secondary rule | `gdrive_downloader.py` | Low | P2 | None |
| 10 | **Dashboard graceful shutdown** — no `app.on_cleanup` handler; running batch task is orphaned when server stops | `dashboard_api.py` | Low | P2 | None |
| 11 | **Progress polling disk I/O** — `/api/progress` reads `_progress.json` from disk every 1.5s; for long batches this is unnecessary churn | `dashboard_api.py` | Low | P2 | None |
| 12 | **Worker health siloed** — worker health at `:8006` is never queried by dashboard `/api/status`; operator sees partial picture | `dashboard_api.py` | Medium | P2 | None |
| 13 | **No metrics** — no Prometheus/structlog counters for SOAP latency, docs/min, batch success rate, GDrive strategy hit rate | All | High | P3 | #1 |
| 14 | **CSV BOM marker** — `load_processos_from_file` uses `utf-8` not `utf-8-sig`; Excel-exported CSVs fail silently | `batch_downloader.py` | Low | P3 | None |

## Constraints

- Worker requires Redis + optional Playwright; tests must mock both cleanly
- Sequential batch is intentional for server courtesy; parallelism needs `asyncio.Semaphore`, not unlimited gather

## Definition of Done

- `pytest tests/ -q` reports ≥ 40 tests passing (items 1, 8)
- No hanging process in `_try_requests_parse` under simulated slow network (item 2)
- `POST /api/download` with 500 processes returns 422 (item 4)
- `batch_downloader` processes 3 processes concurrently behind `asyncio.Semaphore(3)` (item 8)
