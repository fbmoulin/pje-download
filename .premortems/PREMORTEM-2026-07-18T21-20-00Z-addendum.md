---
generated: 2026-07-18T21:20:00Z
skill: premortem-code
mode: deep
target: fix/reply-queue-ttl (PR #33, merged as 7b4c24f) — POST-MERGE addendum
scope: branch
stack_detected: [python, redis, docker-compose]
addenda_loaded: [stack-redis-arq.md, stack-docker-k8s.md]
verdict: REWORK
risk_counts:
  high: 2
  medium: 3
  low: 0
dropped_findings_count: 8
---

> **Corrects the prior report.** `PREMORTEM-2026-07-18T21-05-00Z.md` recorded that the
> three adversarial sub-agents never reported and that coverage was therefore
> single-lens. All three delivered afterwards, and they found **two high-severity
> regressions the single-lens pass missed** — both already merged and deployed at the
> time. That earlier verdict of REFINE was wrong on the evidence now available; the
> correct verdict for #33 as merged is **REWORK**. Remediated in PR #34 (`f8d1358`).
>
> The single-lens pass also produced a **false negative that mattered**: it dropped the
> shared-queue finding at the Evidence gate on "0 in-repo consumers", which is not the
> same proposition as "no consumers". See F2.

## Detailed findings

### Finding 1: Crash + resume lost every undrained result once queues could expire

**Category:** 9 — Implicit resource lifecycle (with 7 — Invisible invariants)
**Severity:** high
**Confidence:** confirmed
**Location:** `dashboard_api.py:655-661`, `dashboard_api.py:446`, `config.py:210-222`
**Mitigation verified absent:** `resume_active_batch` re-enters `_run_batch` with
`enqueue_jobs=False` (`dashboard_api.py:446`). Inside `_enqueue_batch`, the guard
`if enqueue_jobs or not job.progress:` is false for a recovered batch with persisted
progress, so the queue is **not** deleted and progress is **not** rebuilt
(`dashboard_api.py:655-658`); `if enqueue_jobs and serialized_payloads:` likewise skips
the re-publish (`dashboard_api.py:660`). Resume therefore depends on the undrained reply
queue surviving. Searching `dashboard_api.py` for `expire`/`ttl`: **0 matches** —
nothing re-arms the window from the dashboard side, and nothing checks the queue still
exists on resume.

#### Failure narrative

The dashboard crash-looped at 22:10 after a bad config push. Nine of twelve processos
had already downloaded, their terminal results sitting undrained in
`kratos:pje:results:b-4471`. The operator fixed it at 07:30. `_load_active_batch`
recovered the batch and `resume_active_batch` re-entered the poll loop — but the queue
had expired at 23:41, ninety minutes after the worker's last write, and Redis dropped
the list with all nine messages. With `enqueue_jobs=False` nothing was re-queued, so
`_poll_results_loop` BLPOPed a key that no longer existed, idled out after
`RESULT_WAIT_TIMEOUT_SECS`, and marked all twelve failed. The PDFs were on disk the
whole time. **Before #33 the key was immortal and this exact resume drained cleanly** —
the change did not eliminate "batch failed but the files are on disk", it moved the
threshold from *never* to *ninety minutes*, comfortably inside an overnight outage. The
suite stayed green because its invariant test measures poll duration, not downtime.

**Hardening:** Give the TTL a floor sized to a realistic outage rather than to the batch
ceiling, so the leak stays bounded without trading it for silent loss.
**(Applied in #34 — `config.py`, 24h floor.)** A stronger fix, not taken: on resume,
detect a missing reply queue with pending processos and re-enqueue rather than idle out.

### Finding 2: The expiry silently rewrote the n8n control plane's durability contract

**Category:** 4 — Assumptions baked into data transformations (scope creep)
**Severity:** high
**Confidence:** confirmed
**Location:** `worker.py:1462`, `worker.py:1668`, `worker.py:85-100`
**Mitigation verified absent:** `rpush_with_ttl` was unconditional, so
`_publish_result`'s default `queue_name="kratos:pje:results"` — the **un-suffixed**
queue — acquired an expiry it never had. `worker.py:1585` documents the job queue as fed
by "o n8n control plane" and `worker.py:1665` comments the publish site "Publicar
resultado para o n8n"; `protocol.py:37` types `replyQueue` as `NotRequired[str | None]`,
so an n8n-enqueued job omitting it lands on exactly that shared queue. No branch in
`rpush_with_ttl` distinguished the two key shapes.

#### Failure narrative

The n8n control plane was paused for a two-hour workflow migration. Results the worker
had published to `kratos:pje:results` — previously immortal, and therefore safe to drain
late — expired at the ninety-minute mark. n8n resumed to an empty queue and no error:
the messages had not failed, they had aged out under a policy nobody chose for them. A
leak fix scoped to the dashboard's own queues had quietly changed the durability
contract of a queue owned by an external system.

**Note on how this was missed.** The single-lens pass *considered* this and dropped it at
the Evidence gate, citing "0 matches for a consumer BLPOPing that key". The search was
repo-wide, and the conclusion drawn — "nothing reads it, so a TTL is inert" — does not
follow from it: absence of an *in-repo* consumer is not absence of a consumer, and the
call site says so in a comment on the adjacent line.

**Hardening:** Apply expiry only to queues this service owns end-to-end.
**(Applied in #34 — `worker.owns_queue_lifecycle`.)**

### Finding 3: Cross-container config drift defeats the import-time guard

**Category:** 7 — Invisible invariants
**Severity:** medium
**Confidence:** confirmed
**Location:** `config.py:217-235`, `worker.py:62`, `dashboard_api.py:67`,
`docker-compose.yml:40`, `docker-compose.yml:93`
**Mitigation verified absent:** the guard added in #33 fires at import inside **one**
interpreter against **one** process's environment. `REDIS_RESULT_QUEUE_TTL_SECS` is
imported only by `worker.py:62`; `BATCH_MAX_DURATION_SECS` is enforced only in
`dashboard_api.py`. Verified: `grep -l` for each name across the two modules returns
`dashboard_api.py` and `worker.py` respectively — disjoint. Setting the ceiling on the
dashboard service alone leaves both containers passing their own local check while the
system-wide relation is violated. `tests/test_result_queue_ttl.py` has the identical
defect: it evaluates both constants in a single interpreter, so cross-container
divergence is not representable in it.

#### Failure narrative

Long batches kept tripping the one-hour ceiling, so `BATCH_MAX_DURATION_SECS=14400` was
added to the **dashboard's** `environment:` block — the natural place, since dashboard
code is what reads it. The worker's block was untouched and kept deriving its own TTL.
Both containers booted clean. A three-hour batch expired its reply queue mid-flight.

**Hardening:** Publish the worker's effective TTL to a well-known key at startup and have
the dashboard assert against it at batch start; an in-process assertion cannot express a
cross-process invariant. **Not applied** — the 24h floor in #34 makes this far harder to
reach but does not close it.

### Finding 4: The AST guard enforced a syntactic proxy, not the invariant

**Category:** 5 — Coincidental correctness
**Severity:** medium
**Confidence:** confirmed
**Location:** `tests/test_result_queue_ttl.py` (as merged in #33)
**Mitigation verified absent:** the comprehension filtered on `node.func.attr == "rpush"`
alone, and whitelisted any receiver merely *named* `pipe`. `_publish_dead_letter` already
writes with `lpush` (`worker.py:1573`) and was invisible to it. Three bypasses: any
`lpush` to a reply queue, a pipeline variable named anything else, or a raw client
variable named `pipe`.

#### Failure narrative

A later `_publish_batch_summary` used `lpush` to prepend a summary. The guard passed — it
only knew the word `rpush` — and the new call created the key with no expiry whenever it
fired first. Keys with no expiry returned under a test explicitly named to prevent them.

**Hardening:** Cover every list-write, resolve sanctioned writes from the *body* of
`rpush_with_ttl` rather than by variable name, exempt the dead-letter sink explicitly.
**(Applied in #34.)**

### Finding 5: A wired-through bad value becomes a crash-loop, not a refusal

**Category:** 8 — Load-bearing defaults
**Severity:** medium
**Confidence:** confirmed
**Location:** `config.py:224-235`, `docker-compose.yml` (`restart: unless-stopped`),
`.github/workflows/deploy.yml`
**Mitigation verified absent:** `docker-compose.yml` has **0 matches** for `env_file:`,
and none of the four timing vars appear in either service's `environment:` block — so the
values are pinned to code defaults and the guard cannot fire in production today. When
someone does wire them through, a bad value becomes an import-time `raise` inside a
container with `restart: unless-stopped`; the deploy's health poll runs a bounded number
of iterations and then fails, by which point the old container is already gone.

#### Failure narrative

The vars were finally wired into both `environment:` blocks with the TTL one digit below
the ceiling. The worker raised at import and restarted forever. The deploy health-poll
timed out and failed the run, but the previous worker had already been replaced, leaving
production with no worker until someone read container logs. A guard meant to prevent
silent data loss had converted a typo into an outage.

**Hardening:** Prefer clamping with a loud `log.error` over `raise` for a value that can
be safely corrected upward; or keep the raise but gate the deploy on the *new* container
being healthy before the old one is removed. **Not applied — needs a human decision.**

## Dropped findings (for transparency)

- **Retry duplication across MULTI/EXEC** — DROPPED at calibration rule 1 (not
  introduced): the pre-change bare RPUSH retried in the identical loop, so at-least-once
  is pre-existing. The consumer is idempotent anyway — `dashboard_api.py:759`
  `if numero not in state.pending: continue`.
- **Key resurrection after the dashboard's `finally` delete** — DROPPED at Evidence:
  mitigation present; worker progress writes route through `rpush_with_ttl`, so a
  resurrected per-batch key carries an expiry.
- **EXPIRE racing a concurrent BLPOP** — DROPPED: no distinct failure; expiry drops the
  list wholesale and the blocked BLPOP returns nil into the existing idle path. That is
  Finding 1's mechanism, not a separate one.
- **Pipeline holds a pooled connection longer / interacts with `socket_timeout`** —
  DROPPED at Evidence: factually wrong; redis-py asyncio acquires the connection at
  `execute()`, not at `__aenter__`.
- **`__aexit__` raises an uncaught type** — DROPPED at Confidence: `reset()` raises
  `ConnectionError`, which is inside the caught tuple.
- **`transaction=True` unnecessary for two commands on one key** — DROPPED: style/perf,
  excluded by the rules.
- **`maxmemory`/`allkeys-lru` evicting reply queues** — DROPPED at calibration rule 1:
  not changed by this diff; eviction applied regardless of TTL.
- **Partial rollout in both directions** — DROPPED at Evidence, verified benign:
  `deploy.yml` restarts `dashboard` and `worker` from one `docker compose up -d --build`
  off a single rsync. Old worker + new dashboard degrades to the pre-fix leak (visible as
  `ttl=-1`, no corruption); new worker + old dashboard arms a TTL on a queue the old
  dashboard still deletes normally. Neither skew has a data-loss path.

## Current bugs (not pre-mortem findings — surfaced separately)

- **`redis.ResponseError` escapes `_publish_result`** (high, pre-existing). The caught
  tuple at `worker.py` is `(ConnectionError, TimeoutError, OSError)`; `ResponseError`
  (OOM under `maxmemory`, WRONGTYPE) is not in it, and the call site sits directly in
  `consume_queue`'s `while` loop with **no enclosing try** — so it escapes
  `consume_queue` and halts consumption entirely, with the downloaded files already on
  disk. Reachable identically before this change.
- **A Redis fault is mislabeled as a PJe failure** (medium, pre-existing). The same
  narrow tuple in `_publish_progress` lets a `ResponseError` propagate into the
  per-document `except Exception`, logging `pje.browser.individual.doc_failed` for a
  document that downloaded fine — and skipping both the counter increment and
  `DOWNLOAD_DELAY_SECS`, so PJe is hammered without pacing for the rest of the loop.
- **`job.get("replyQueue", "kratos:pje:results")`** returns `None`, not the default, when
  the key is present-but-null — which `protocol.py:37` explicitly permits (low,
  pre-existing).
