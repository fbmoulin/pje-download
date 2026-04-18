# 2026-04-18 Audit Remediation Plan

## Context

Post-P0.4 Grafana monitoring stack shipment (PR #11, 2026-04-18), a 3-lens
parallel audit was run against the main pje-download scripts:

- **Code-quality lens** (`Explore` subagent) on `worker.py` + `dashboard_api.py`
  → 15 findings, structural/maintenance issues (large methods, duplicated
  patterns, misleading naming).
- **Adversarial lens** (`adversarial-critic`) on `audit_sync.py` +
  `batch_downloader.py` → 8 findings, of which 5 are real production bugs
  with high confidence (data-integrity / progress-state / metric-accuracy).
- **Architecture lens** (`Explore`) on all main modules → 10 findings,
  coupling and layering smells, mostly deferrable.

Surprise finding: 4 real bugs in code that had just shipped in Sprint 7
(`audit_sync.py`) and Sprint 11 (`batch_downloader.py` Playwright paths).
Low production load had masked them — they were the reason to ship this
remediation cycle, not cosmetics.

Full agent reports are in the session transcript (search "Code Quality &
Refactoring Analysis", "adversarial review", "Import graph"). Detailed
triage is in `~/.claude/plans/a-fizzy-moon.md` (user-level plan file).

## Sprint 1 — 5 Production Bugs

**PR:** [#12](https://github.com/fbmoulin/pje-download/pull/12) — `fix/audit-batch-bugs`
**Commit:** `3b7ed64`
**Tests:** 377 → 388 (+11 targeted regression tests)

| ID | Bug | File:line | Fix |
|----|-----|-----------|-----|
| B1 | Crash-resume re-downloads already-completed processos | `batch_downloader.py:518` | Add `progress.save(force=True)` before early return on Playwright success path |
| B2 | Grafana lag gauge silently freezes on mixed naive/aware datetimes | `audit_sync.py:149,423` | New `_coerce_utc` helper normalises tz-aware UTC everywhere |
| B3 | Successful download marked failed when file metadata missing `tamanhoBytes` | `batch_downloader.py:459,511,557,659` | `sum(int(f.get("tamanhoBytes", 0) or 0) for f in ...)` at all 4 sites |
| B4 | `audit_sync_rows_total{success}` overcounts on crash-recovery | `audit_sync.py:347` | Move increment from `_insert_batch` to `_sync_file` after `_save_cursor` succeeds |
| B5 | PG<15 silently accepts duplicate NULL-keyed audit rows | `audit_sync.py _verify_pg_version` | Query `server_version_num` after pool creation; on <150000: log ERROR, set `_disabled=True`, `shutdown.set()`, raise |

## Sprint 2 — DRY Helpers + Config Constants

**PR:** [#13](https://github.com/fbmoulin/pje-download/pull/13) — `refactor/sprint2-polish`, stacked on #12
**Commit:** `3788b39`
**Tests:** 388 (no change — pure refactor)

| ID | Refactor | Sites | Outcome |
|----|----------|-------|---------|
| Q1 | Extract `file_utils.total_bytes(files)` | 17 copies | Central defensive helper (subsumes B3 by construction) |
| Q2 | Dedupe `_merge_downloaded_files` | 2 verbatim copies → 1 | Import from `file_utils.merge_file_lists`, compat aliases kept |
| Q3 | Extract `dashboard_api._safe_load_json(path)` | 3 sites | Consolidates JSON-load-with-except pattern in `_load_history` / `_load_active_batch` |
| Q4 | Move 7 magic numbers to `config.py` | 9 call sites | Env-configurable: `PLAYWRIGHT_FULL_DOWNLOAD_TIMEOUT_MS`, `PLAYWRIGHT_INDIVIDUAL_DOWNLOAD_TIMEOUT_MS`, `REDIS_BLPOP_TIMEOUT_SECS`, `REDIS_CIRCUIT_THRESHOLD`, `MNI_HEALTH_CACHE_TTL_SECS`, `RESULT_WAIT_TIMEOUT_SECS`, `RESULT_POLL_BLPOP_TIMEOUT_SECS` |
| Q5 | `FileMetadata` dataclass | (deferred to Sprint 3B) | Touches ~20 dict literals; bigger scope, isolated PR later |

**Gotcha caught en route:** Python function-scope shadow bug. `batch_downloader.download_batch()` had a local variable `total_bytes = sum(...)` at line 698 that shadowed the newly-imported helper, causing `UnboundLocalError` at the 4 earlier call sites. Fix: renamed local to `batch_total_bytes` with defensive comment. This ALSO affected `dashboard_api.py`'s `_run_batch` and `handle_history` which have the same local-var name, but no conflict there because those functions don't also call the helper.

## Sprint 3A — _run_batch split + AsyncRetry

**PR:** [#14](https://github.com/fbmoulin/pje-download/pull/14) — `refactor/sprint3-runbatch-retry`, stacked on #13
**Commit:** `f2091e0`
**Tests:** 388 → 398 (+10 AsyncRetry unit tests; 63 dashboard integration tests verify R2 behavior parity)

### R3 — `async_retry.AsyncRetry` class

Consolidates 2 of 3 hand-rolled exponential-backoff loops:

- `worker.PJeSessionWorker.init` (Redis ping + retry, 5 attempts, 30s cap)
- `dashboard_api._rpush_with_retry` (3 attempts on Redis errors, 10s cap)

The third site (`worker._try_official_api`) is intentionally kept — it retries on HTTP 5xx status codes (not exceptions) and returns `None` on exhaustion (not re-raise), a distinct contract.

Design notes:
- `coro_factory` is a zero-arg callable returning a FRESH coroutine per attempt (Python's `RuntimeError: cannot reuse awaited coroutine`).
- `log_extra` kwargs forward to the structured log event so call sites retain `processo=` / `key=` fields without coupling them to the helper.
- `CancelledError` is not defensively blocked; users should never put it in `retry_on`, and it propagates correctly when absent.

### R2 — Split `DashboardState._run_batch` (170L → 30L + 3 phase methods)

| Method | ~Lines | Purpose |
|--------|--------|---------|
| `_enqueue_batch(job, redis, …)` | 50 | Publish payloads, reset progress, delete reply queue, RPUSH work, compute `BatchPollState` |
| `_poll_results_loop(job, redis, state)` | 60 | Drain reply queue; dispatch progress vs terminal events; handle fatal worker status + idle timeout |
| `_finalize_batch(job)` | 60 | Compute final status (done/partial/failed ladder), persist report, emit Prometheus metrics, evict old batches |
| `_run_batch` (orchestrator) | 30 | Thin try/except wrapper around the 3 phases |

Plus:
- `BatchPollState` dataclass — mutable poll-phase state (pending, last_result_at, serialized_payloads, reply_queue, timed_out, fatal_error).
- `_FATAL_WORKER_STATUSES = frozenset({"session_expired", "captcha_required"})` — named constant for the fatal-abort check.

**Preserved invariants** (Grafana-load-bearing):
1. Side-effect order: `job.status=running` → `persist_progress` → `delete reply_queue` → `persist_active_batch` → `RPUSH`.
2. Metric sequence at finalise: `dashboard_batches_total` → `batch_docs_total` → `batch_bytes_total` → `batch_processos_total` → `dashboard_active_batches.set(0)`.
3. Status ladder: `failed` on total washout (done=0, partial=0, failed>0), `partial` on mixed, `done` on all-success.
4. Fatal abort path: LREM remaining payloads from work queue, then `_fail_remaining_processes`, then break.
5. Idle timeout: `dashboard_batch_timeouts_total.inc()` before structured log.

All 63 dashboard integration tests pass unchanged — behavior parity verified.

## Sprint 3B — R1: Split `download_process` (DONE)

**Status:** SHIPPED 2026-04-18. PR #15 (`refactor/sprint3b-download-process-split`), commit `41626b5`.
**Actual:** ~2h focused work, +9 phase-isolation tests (399→408)

Shipped:
- `DownloadContext` dataclass — transient state carrier across phases.
- `_resolve_output_dir` — path validation + mkdir extracted from orchestrator setup.
- `_make_progress_cb` — snapshot-based incremental progress closure.
- `_phase_gdrive` (Phase 0) — returns `dict|None`.
- `_phase_mni` (Phase 1) — returns `dict|None`.
- `_phase_api_fallback` (Phase 2) — returns `bool`.
- `_phase_browser_fallback` (Phase 3) — returns `dict|None`.
- Orchestrator reduced from 438L to ~80L.
- 9 tests in `tests/test_download_phases.py` — each phase testable without Playwright/MNI/GDrive stacks.

## Sprint 5A — 3 Critical Reliability Bugs (DONE)

**Status:** SHIPPED 2026-04-18. Branch `refactor/sprint3b-download-process-split`, commit `1ffb544`.
**Tests:** 408 → 411 (+3 targeted regression tests)

Surfaced by 4-lens parallel audit (architecture, reliability, test-coverage, APIs/integrations).

| ID | Bug | File:line | Fix |
|----|-----|-----------|-----|
| C1 | Circuit breaker health status never recovered after Redis reconnected — worker stayed 503 forever | `worker.py:1580` | After `consecutive_errors = 0`, reset `_health_status = "consuming"` if was `"redis_unreachable"` |
| C2 | Unhandled `json.JSONDecodeError` in `_poll_results_loop` crashed entire batch loop — batch hung forever | `dashboard_api.py:711` | `try/except JSONDecodeError` around `json.loads`, log warning, `continue` |
| C3 | `serialized_payloads[pending_numero]` direct dict access → `KeyError` when key absent in crash-resume scenario | `dashboard_api.py:735` | `.get()` + `if payload:` guard skips missing entries safely |

## Sprint 5B — 6 Reliability + Monitoring Fixes (DONE)

**Status:** SHIPPED 2026-04-18. Branch `refactor/sprint3-runbatch-retry`, commit pending.
**Tests:** 411 → 416 (+5 targeted regression tests)

| ID | Fix | Files | Action |
|----|-----|-------|--------|
| H2 | Absolute batch timeout — poll loop had no wall-clock ceiling | `dashboard_api.py`, `config.py` | `BATCH_MAX_DURATION_SECS` env-var (default 3600s); check fires at top of each loop tick |
| H3 | MNI SOAP timeout retry — single timeout marked processo failed | `mni_client.py` | `AsyncRetry(attempts=3, backoff_cap_secs=10)` wraps `wait_for(to_thread(...))` |
| H4 | GDrive 429 silent skip — rate-limited files omitted silently | `gdrive_downloader.py` | 429 check → `asyncio.sleep(Retry-After)` → one retry; second `if ≠200` handles retry failure |
| H5 | Payload size cap — no Content-Length guard on POST /api/download | `dashboard_api.py` | `_MAX_DOWNLOAD_PAYLOAD_BYTES = 10 MB`; 413 before json() call |
| M7 | Disk threshold hardcoded at 100 MB — far too low for production | `worker.py`, `config.py` | `DISK_LOW_THRESHOLD_MB` env-var (default 2000 MB) replaces literal `100` |
| M8 | Alert fatigue — `PjeAuditSyncBatchesFailing` fires on any single failure | `ops/monitoring/pje/alert-rules.yml` | `> 0` → `> 3` (3 failures in 10 min window before paging) |

## Sprint 4 — Architectural (DEFERRED, schedule-when-touched)

Low urgency. Schedule only when the affected code is being modified for another reason:

- **A1** — Typed Redis queue protocol. Create `protocol.py` with `JobMessage` / `ResultMessage` / `DeadLetterEntry` typed dicts. Dashboard and worker use them for serialise/deserialise. Enables schema versioning and earlier detection of protocol drift. Defer until a new field needs to be added to the message shape.
- **A2** — `dashboard_api.py` module-level globals (`state`, `_rate_buckets`, `_login_task`, etc.) → request-scoped `AppContext` dataclass stored in `app["_context"]`. Enables parallel test execution and cleaner isolation. Defer until parallel-test-execution is a pain point.
- **Worker.py splitting** — Break `worker.py` (1860 lines) into `worker_consumer.py` (queue loop), `worker_session.py` (Playwright lifecycle), `worker_health.py` (`/health`+`/metrics` server). Big rewrite, low ROI at current 1-user scale. Revisit if worker grows >2500 lines.

## Cumulative Outcome

| Metric | Before audit (2026-04-18) | After Sprint 5B | Delta |
|--------|---------------------------|-----------------|-------|
| Test count | 377 | 416 | +39 |
| Duplicated helpers | 2 copies of `_merge_downloaded_files`, 17 copies of `sum(tamanhoBytes)`, 2 retry loops | 0 duplicates | Consolidated |
| Inline magic numbers | 9 timeouts/thresholds | 0 | Now env-configurable in `config.py` |
| 438-line mega-method | `download_process` | Split + orchestrator | Closed (Sprint 3B) |
| 170-line god-method | `_run_batch` | Split 3 phases + orchestrator | Closed (Sprint 3A) |
| Production bugs | 5 latent in audit_sync + batch_downloader | 0 | Closed (B1-B5) |
| Reliability gaps | 3 crash paths (C1-C3) + 6 hardening items (H2-H5,M7,M8) | 0 | Closed (Sprint 5A+5B) |
| Config constants | ~33 public | ~42 public | +9 env-configurable runtime knobs |
| Alert fatigue | `PjeAuditSyncBatchesFailing > 0` (fires on first transient) | `> 3` (3 in 10min) | Closed (M8) |

## Verification

All changes verified via:

1. `pytest tests/ -q` on each branch — test count deltas match plan.
2. `ruff check && ruff format --check` on all modified files — clean.
3. Behaviour parity for R2: 63 existing dashboard integration tests pass unchanged.
4. Each B-test asserts the BEFORE-FIX failure mode explicitly, so a silent revert would fail loudly with a pointed message.

Post-merge verification (manual, deferred to deploy time):
- B2 sanity: append naive-ts line to test JSON-L → `pje_audit_sync_lag_seconds` gauge updates on next tick (pre-fix: frozen).
- B4 sanity: `pje_audit_sync_rows_total{success}` growth rate matches `COUNT(*)` growth in Railway audit_entries (pre-fix: overcount on restart).
- R2 sanity: end-to-end batch run produces identical `_progress.json` / `_report.json` and identical Prometheus metric trajectories as pre-split.

## References

- Main project instructions: [`../../CLAUDE.md`](../../CLAUDE.md)
- Reader's guide to the repo: [`../../README.md`](../../README.md)
- Grafana monitoring stack (orthogonal): [`../specs/2026-04-18-grafana-dashboard-design.md`](../specs/2026-04-18-grafana-dashboard-design.md)
- User-level orchestration plan (agent-session state): `~/.claude/plans/a-fizzy-moon.md`
