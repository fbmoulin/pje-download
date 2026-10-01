# Phase 2 Sprint 2 — Playwright Download Telemetry (T2.2A)

**Goal:** Record how long Playwright `expect_download` waits take and how often
they hit the cap, so `PLAYWRIGHT_FULL_DOWNLOAD_TIMEOUT_MS` (300 s) and
`PLAYWRIGHT_INDIVIDUAL_DOWNLOAD_TIMEOUT_MS` (30 s) can be lowered from data
instead of guessed. **No timeout value changes in this sprint.**

**Status:** implemented; awaiting merge. The user approved option A ("proceed
with T2.2A, defer reduction to Sprint 3") at the gate below.

## Process note (SDD)

The `writing-plans` and `plan-quality-gate` skills were not available in the
session that produced this spec, so neither was run. Structure was checked with
`tools/verify_spec.py` (CI gate) instead. Execution was done inline rather than
through `subagent-driven-development`: the change is small and every task
touches `worker.py`/`metrics.py`, so parallel agents would have repeated the
lost-update races seen in Sprint 1. Commits are small and frequent, one per
concern.

## Corrections to the first draft

The first draft of this spec (scratchpad, never committed) was written before
reading the code and was wrong in three ways. The as-built design differs:

| Draft claim | Reality | As built |
|---|---|---|
| Timeouts are used in `batch_downloader.py` | They are used only in `worker.py` (4 download sites + 1 manual-login wait) | Instrumented the 4 download sites in `worker.py` |
| `gdrive_downloader.py` hardcodes 60 s "inconsistent" with 30 s config; fix by using the config value | 60 s is for Drive's confirm-page flow, not a PJe document. Switching to 30 s would **halve** a timeout | New `GDRIVE_PLAYWRIGHT_DOWNLOAD_TIMEOUT_MS`, default 60 000 = old literal. Zero behaviour change, now tunable |
| Counter `playwright_timeout_total` | A counter cannot say how far a cap can be lowered | One histogram, `pje_playwright_download_wait_seconds{operation,outcome}` |

The manual-login wait (`worker.py`, `wait_for_url`, 300 s) also uses
`PLAYWRIGHT_FULL_DOWNLOAD_TIMEOUT_MS` but waits on a human, not a download. It
is deliberately not instrumented, and is a coupling to keep in mind for T2.2B:
lowering the full-download cap would also shorten the time allowed for manual
login.

## Design

- `metrics.track_playwright_download(operation)` — sync context manager. Wrap
  the `expect_download` block **and** `await info.value` (Playwright raises the
  timeout on the latter).
- Outcome: `success`, `timeout` (class name `TimeoutError`, which covers
  Playwright's own class without importing it), or `error`. Exceptions always
  propagate. `CancelledError` and other `BaseException`s are not recorded.
- `operation`: `full_download` (2 sites), `individual` (2 sites), `gdrive` (2 sites).
- Buckets `1…300 s`, topping out at the default full-download cap.

## Tasks

### Task 1 — Metric and helper (TDD)

Failing tests first in `tests/test_playwright_download_metrics.py`: success,
timeout (Playwright-named, `asyncio`, builtin), other error, cancellation not
recorded, exceptions re-raised. Then implement in `metrics.py`. Commit.

### Task 2 — Instrument `worker.py` (TDD)

Tests drive `_try_full_download_button` (timeout) and
`_download_docs_sequential` (timeout, success) against a page stub. Then wrap the
4 sites. Commit.

### Task 3 — `gdrive_downloader.py` (TDD)

Tests: default equals 60 000; `expect_download` receives the configured value;
timeout recorded under `operation="gdrive"`. Existing tests patched
`gdrive_downloader.metrics` wholesale, which makes `MagicMock.__exit__` truthy
and silently swallows exceptions inside the new `with`; they now patch only
`metrics.gdrive_attempts_total`. Commit.

### Task 4 — Grafana panels

Panel 9: success p50/p95 by operation. Panel 10: outcomes over 1 h. Added to
`ops/monitoring/pje/dashboard.json` without reformatting the file. Commit.

## Verification

| Check | Result |
|---|---|
| `pytest tests/ -q` | 609 passed, 2 skipped (Redis-socket tests; no Redis locally) |
| `ruff check .` and `ruff format --check .` with `ruff==0.14.14` (CI pin) | clean |
| `python tools/verify_spec.py docs/specs/*.md` | all pass |
| Dashboard JSON parses, 14 unique panel ids | yes |

Not verified: the real Prometheus/Grafana rendering (no stack available here)
and a real Playwright download. Run `ops/monitoring/verify.sh` before deploy.

## USER VALIDATION GATE

Approved: option A, instrument now, no timeout changes. **Sprint 3 (T2.2B)
requires a fresh gate** and should not start until the panels show at least
1–2 weeks of production data covering both operations.

## Follow-up: T2.2B (not in this sprint)

Decision rule to propose at that gate: set each cap to roughly 2× the observed
p99 of successful waits, and only if timeouts in that window were caused by
genuine hangs. The login-wait coupling is already resolved:
`worker.load_session` uses its own `PLAYWRIGHT_LOGIN_TIMEOUT_MS` (default
300000) since 2026-09-29, so lowering the download caps does not shorten it.

## References

- `docs/specs/2026-09-25-zeep-forbid-external.md` — spec format precedent
- CLAUDE.md, Phase 2 Backlog T2.2
- `metrics.py` module docstring — instrumentation index (updated)
