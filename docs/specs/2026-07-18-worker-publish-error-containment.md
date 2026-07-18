# SPEC — Containment of publish-path Redis errors in the worker

**Status:** draft, awaiting user validation
**Author:** sessão 2026-07-18
**Target:** `worker.py` (`_publish_result`, `_publish_progress`, `_publish_dead_letter`, `consume_queue`)
**Research:** `research-03-responseerror.md` (sessão scratchpad), plus the live-prod verification below.

---

## Goal

Stop a Redis `ResponseError` on any publish path from killing the worker and destroying
the in-flight job's result.

Concretely, after this change:

1. A `ResponseError` while publishing **never** propagates out of `consume_queue`.
2. The downloaded result is **durably recorded** (local log) instead of vanishing.
3. Every newly-caught error is **observable** — a metric increment plus an `error`-level
   log carrying the exception class and text. Nothing is swallowed quietly.

### Non-goals (explicitly out of scope)

| Out of scope | Why |
|---|---|
| At-least-once job delivery | `blpop` (`worker.py:1633`) removes the job atomically with no ack, processing-list, or `RPOPLPUSH`. A crash between BLPOP and publish loses the job permanently. This is a **pre-existing, larger** design change (reliable-queue pattern + idempotent reprocessing + orphan recovery). This spec makes the *result* durable; it does **not** make the *job* re-runnable. Any claim otherwise is overclaiming. |
| Widening the `consume_queue` circuit breaker to publish errors | The breaker (`REDIS_CIRCUIT_THRESHOLD`) is deliberately consumer-scoped, wrapping only the BLPOP. Feeding publish failures into it risks `/health` flapping on a single bad job. Metrics are the right signal here. |
| Unifying the three divergent Redis-error tuples repo-wide | Real inconsistency (worker publish paths, `dashboard_api._rpush_with_retry`, the breaker each use a different tuple), but a separate cleanup. Touching it here widens the diff past what the fix needs. |
| The `redis` `mem_limit: 128m` vs `--maxmemory 96mb` headroom concern | A cgroup OOM-kill surfaces as `ConnectionError` (already caught), not `ResponseError`. Separate operational item. |

---

## Why this is reachable — the part the research got wrong

The research report concluded `ResponseError` was **not reachable** on this deployment:
`OOM` is ruled out by `--maxmemory-policy allkeys-lru` (Redis evicts rather than erroring,
and prod sits at 1.42MB of 96MB), and `WRONGTYPE` is ruled out because every Redis command
in the codebase is `rpush`/`lpush`/`blpop`/`expire`/`delete` — no code path can write a
non-list type to a list key.

Both sub-conclusions are correct. The conclusion drawn from them is not, because the
analysis omitted **`MISCONF`**.

Verified live in the production container (read-only):

```
save                        3600 1  300 100  60 10000     ← RDB snapshots enabled
stop-writes-on-bgsave-error yes                            ← default; fatal combination
dir                         /data
appendonly                  no
```

With `stop-writes-on-bgsave-error yes`, a **failed background save makes Redis reject every
write** with `-MISCONF Errors writing to the RDB snapshot file. Commands that may modify the
data set are disabled`.

Proven against the pinned redis-py 8.0.0 inside the worker container:

```
EXCEPTION_CLASSES codes: ASK CLUSTERDOWN CROSSSLOT ERR EXECABORT LOADING MASTERDOWN
                         MOVED NOAUTH NOPERM NOSCRIPT OOM READONLY TRYAGAIN WRONGPASS

MISCONF    -> ResponseError        caught by worker tuple? False
OOM        -> OutOfMemoryError     caught by worker tuple? False
WRONGTYPE  -> ResponseError        caught by worker tuple? False
```

`MISCONF` has no entry in `EXCEPTION_CLASSES`, so it falls through to a generic
`ResponseError` — outside `(redis.ConnectionError, redis.TimeoutError, OSError)`.

**Two independent triggers exist in this deployment:**

| Trigger | Mechanism | Why it is plausible here |
|---|---|---|
| Disk exhaustion | RDB save to `dir /data` fails | The app's **core function is downloading files to disk**. The worker already ships a `DISK_LOW_THRESHOLD_MB` health check because disk pressure is anticipated. |
| Memory during `fork()` | `bgsave` forks; COW under `mem_limit: 128m` can fail the save | Only ~32MB of headroom above `maxmemory 96mb`. |

### The failure is self-amplifying — a job shredder

1. `bgsave` fails → every write returns `MISCONF`.
2. `_publish_result` raises; the `_log_job_result` fallback lives **inside** the non-matching
   `except` (`worker.py:1508-1512`) and is **skipped**.
3. The exception escapes `consume_queue`; `main()` wraps it in `try/finally` with **no
   `except`** (`worker.py:1917-1930`), so the process exits non-zero.
4. `restart: unless-stopped` (`docker-compose.yml:98`) restarts the container.
5. The new worker BLPOPs the **next** job, downloads it, and dies the same way.

One job destroyed per crash cycle, files accumulating on disk with no record — and since the
likely trigger is a full disk, **downloading more files makes the cause worse**.

### Scope correction: the surface is wider than first reported

| Path | Except tuple | Call sites | Enclosing try? |
|---|---|---|---|
| `_publish_result` | `(ConnectionError, TimeoutError, OSError)` | 1 (`worker.py:1690`) | No |
| `_publish_progress` | same | **~20**, throughout `download_process` | No |
| `_publish_dead_letter` | same | 2 | No — and it fires while already handling a malformed payload |

`_publish_progress` is the **widest** surface, not `_publish_result`. Only `_publish_result`
has the durable local-log fallback; the other two log-and-continue.

---

## Design

Two deliberately redundant layers.

**Layer 1 — correctness (keeps the result).** In `_publish_result`, add a separate
`except redis.ResponseError` that does **not** retry: increment
`worker_publish_failures_total{kind="result"}`, log at `error` with `error_class`, call
`_log_job_result(...)`, return. Retrying is pointless for the permanent members of this
class and burns ~7s of backoff per job before the fallback. Mirror the no-retry + metric +
`error` log shape (without the local-log fallback, which they do not have) in
`_publish_progress` and `_publish_dead_letter`.

**Layer 2 — containment (keeps the consumer alive).** Wrap the per-job body of the
`consume_queue` loop in `try/except Exception`, logging with traceback plus a metric, then
`continue`. This is the backstop for the classes layer 1 deliberately does **not** catch
(`DataError`, `WatchError`, or a future non-Redis bug in `download_process`) — because
"someone enumerated every exception class correctly" is precisely the assumption that
failed here.

**Rejected: widening to `redis.RedisError`.** It would swallow `DataError` (our own encoding
bug), `NoPermissionError` (an ops problem needing a human), and `WRONGTYPE` (a design bug)
*into the retry loop*. This repo's recorded production incidents (B2 frozen gauge, B5
silently-ignored PG version) are both silent-degradation failures; reproducing that pattern
to fix a crash is a bad trade.

**`OutOfMemoryError` nuance, decided explicitly:** it subclasses `ResponseError` but is
arguably transient. It is unreachable here (§ above), so it is treated as non-retriable
along with the rest. Recorded as a decision, not an oversight.

---

## Tasks

Executed with **TDD** and **frequent commits** — one commit per task, tests written before
implementation, suite green before moving on. Per `superpowers:writing-plans`, each task is
independently verifiable; execution follows `superpowers:subagent-driven-development` with
the branch passed explicitly to any subagent.

### Task 1 — Prove the corpus rejects the broken code

Before any production edit, add the integration test that **fails on current `master`**:
drive `consume_queue` with two queued jobs where the first publish raises
`ResponseError("MISCONF ...")`; assert the **second job is still processed** and the loop
exits only via `shutdown_event`.

Harness: model on `tests/test_worker_consume_queue_session_expired.py:45-85`
(`asyncio.wait_for(worker.consume_queue(shutdown), timeout=2)`). Mock the pipeline using the
idiom in `tests/test_result_queue_ttl.py` (`pipe.rpush`/`pipe.expire` as `MagicMock`,
`execute` as `AsyncMock`) — a bare `rpush` mock misses the actual throwing frame.

**Gate:** the test MUST fail against `master` before Task 2 begins. A test that passes on
broken code proves nothing.

### Task 2 — Layer 1 in `_publish_result`

Add the non-retriable `except redis.ResponseError` branch. Unit tests assert:
`_log_job_result` awaited exactly once; the metric incremented; and — the load-bearing
assertion — **`asyncio.sleep` never awaited**, proving no retry budget was burned.
Extend `tests/test_worker.py::TestPublishResult::test_publish_retries_on_failure` and
`::test_publish_falls_back_to_local_log`.

### Task 3 — Layer 1 in `_publish_progress` and `_publish_dead_letter`

Same shape, no local-log fallback (they have none). This is the widest surface (~20 call
sites) and must not be skipped.

### Task 4 — Layer 2 containment in `consume_queue`

Per-job `try/except Exception` + traceback log + metric + `continue`.

Must preserve the intentional loop exits: the `break` paths for `session_expired`
(`worker.py:1699-1702`) and `captcha_required` (`worker.py:1704-1707`) are status-driven
`break` statements, not exceptions, and must remain reachable.
`tests/test_worker_consume_queue_session_expired.py` must stay green.

Add an explicit comment that the handler must **not** be widened to `BaseException` —
`asyncio.CancelledError` derives from `BaseException` and must keep propagating.

### Task 5 — Regression guard for over-catching

Assert `asyncio.CancelledError` still propagates out of `consume_queue`, i.e. graceful
shutdown is not swallowed by Layer 2. Without this test, Layer 2 can silently break
shutdown.

### Task 6 — Verification and documentation

Full suite + `ruff check .` + `ruff format --check .`. Confirm the Task 1 test now passes.
Update `TODO.md`. Record in the spec whether the `OutOfMemoryError` decision still holds.

---

## Risks the implementation must not get wrong

| Risk | Mitigation |
|---|---|
| Catching too broadly reproduces the B2/B5 silent-degradation pattern | Every new catch increments a metric **and** logs at `error` with the exception class. No `warning`-level swallows. |
| Layer 2 masks genuine logic bugs as a stream of "job failed, moving on" | Log with `exc_info`/traceback and a dedicated metric so the rate is visible on `/metrics`. |
| Retrying a permanent error delays the durable fallback | Layer 1 explicitly skips the backoff for `ResponseError`. |
| A careless refactor moves the `break` paths inside the try | Non-regression test (`test_worker_consume_queue_session_expired.py`) is a required gate. |
| Widening Layer 2 to `BaseException` breaks graceful shutdown | Task 5 guards it; inline comment explains why. |
| The fix is mistaken for fixing job loss | Non-goals table states plainly that the job is still lost; only the result becomes durable. |

---

## USER VALIDATION GATE

Do not begin implementation until Felipe confirms:

1. **Scope.** Is fixing all three publish paths (not just `_publish_result`) in scope for
   this change, given `_publish_progress` has ~20 unwrapped call sites?
2. **Layer 2 breadth.** Is a per-job `except Exception` in `consume_queue` acceptable, or is
   that too broad for this codebase's taste — preferring only the enumerated Redis classes?
3. **At-least-once delivery.** Confirm it stays out of scope. This change leaves the job
   itself lost on crash; only the result becomes durable.
4. **`MISCONF` root cause.** Should this spec also address the *cause* — e.g. setting
   `stop-writes-on-bgsave-error no`, or disabling RDB persistence for what is a pure
   control-plane cache — or is that a separate operational decision? Redis here holds only
   transient queues; durability of the RDB may not be wanted at all.

---

## Referências / References

- `research-03-responseerror.md` — exception taxonomy, blast radius, options analysis
  (session scratchpad; its §B reachability verdict is **superseded** by the `MISCONF`
  finding above).
- `.premortems/PREMORTEM-2026-07-18T21-20-00Z-addendum.md` — where this bug was first
  surfaced (listed as current bug C1).
- `worker.py:1482-1605` (publish paths), `worker.py:1607-1707` (`consume_queue`),
  `worker.py:1917-1930` (`main`).
- `docker-compose.yml:16-35` (redis config), `:98` (worker `restart:` policy).
- Skills governing execution: `superpowers:writing-plans`,
  `superpowers:subagent-driven-development`, `superpowers:test-driven-development`,
  and the `plan-quality-gate` review before execution.
