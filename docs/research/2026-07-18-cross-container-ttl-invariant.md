# Research 01 — Cross-container TTL invariant (`REDIS_RESULT_QUEUE_TTL_SECS > BATCH_MAX_DURATION_SECS`)

Repo: `/home/fbmoulin/projetos-26-2/pje-download` @ `master` (`05c4bba`). Read-only; no repo file modified.

---

## HEADLINE

**The hole is real but currently UNREACHABLE in production, and it is unreachable by accident, not by design.**

Neither `BATCH_MAX_DURATION_SECS` nor `REDIS_RESULT_QUEUE_TTL_SECS` appears in *either* service's `environment:` block in `docker-compose.yml`, and `deploy.yml`'s `.env` writer does not emit them. There is **no `env_file:`** in the compose file, and `.env` is excluded from the image by `.dockerignore`. Per the repo's own interpolation-≠-injection rule, **both vars are today unsettable inside either container** — both processes fall back to the `config.py` defaults (`3600` / `86400`), so the invariant holds by construction.

It becomes reachable the moment someone uses the feature *as the code invites them to*. `config.py:134-137` explicitly advertises these as env-configurable "so ops can retune without a code deploy". Adding `BATCH_MAX_DURATION_SECS: ${BATCH_MAX_DURATION_SECS:-3600}` to the `dashboard` service — the natural, documented action — is exactly the edit that opens it.

**Why the existing guard cannot catch it:** the guard at `config.py:252` *does* execute in both processes (both fully import `config`). It catches **single-process** misconfiguration. It cannot catch **cross-process divergence**: dashboard env raises `BATCH_MAX_DURATION_SECS`, worker env leaves the TTL at default (or lowers it) — each container's *local* pair passes its own guard, while the *effective* pair (the TTL the worker arms vs. the ceiling the dashboard enforces) is inverted. No process ever sees both halves of the real pair.

**Recommendation: option (ii)** — the dashboard stamps its own derived TTL into the `JobMessage`; the worker arms *that* value for queues it owns. This collapses two sources of truth into one and — uniquely among the options — makes the invariant testable in a single interpreter. Cost is ~6 lines across 3 files.

---

## A. Current reality

### A1. Constant table

| Constant | `config.py` line | Default | Env var | Read by (non-test) |
|---|---|---|---|---|
| `REDIS_BLPOP_TIMEOUT_SECS` | `154` | `5` | same | `worker.py:61,1634` |
| `RESULT_WAIT_TIMEOUT_SECS` | `166` | `360` | same | `dashboard_api.py:66,722,725` |
| `RESULT_POLL_BLPOP_TIMEOUT_SECS` | `167` | `5` | same | `dashboard_api.py:65,719`; `config.py:189` (derivation) |
| `REDIS_SOCKET_TIMEOUT_MARGIN_SECS` | `185` | `10.0` | same | `config.py:194` only |
| `REDIS_MAX_BLOCKING_TIMEOUT_SECS` | `188` | derived `max(5,5)=5` | — (derived) | `config.py:194` only |
| `REDIS_SOCKET_TIMEOUT_SECS` | `191` | derived `15.0` | same | `dashboard_api.py:64,209`; `worker.py:63,230` (**both**) |
| `BATCH_MAX_DURATION_SECS` | `199` | `3600` | same | **`dashboard_api.py:67,706,707` ONLY** |
| `REDIS_RESULT_QUEUE_TTL_MARGIN_SECS` | `214` | `1800` | same | `config.py:238` only |
| `REDIS_RESULT_QUEUE_TTL_FLOOR_SECS` | `230` | `86400` (24h) | same | `config.py:239` only |
| `REDIS_RESULT_QUEUE_TTL_SECS` | `233` | derived `max(3600+1800, 86400) = 86400` | same | **`worker.py:62,123` ONLY** |

**The asymmetry is exactly as stated in the task.** `BATCH_MAX_DURATION_SECS` is a dashboard-only read; `REDIS_RESULT_QUEUE_TTL_SECS` is a worker-only read. The two halves of the invariant are consumed by different containers.

Note `REDIS_SOCKET_TIMEOUT_SECS` is the counter-example: it is read by **both** modules, so its guard-by-derivation genuinely holds system-wide. It is the only timing constant with that property.

### A2. The guards

Reply-queue TTL guard — `config.py:252-257`:

```python
if REDIS_RESULT_QUEUE_TTL_SECS <= BATCH_MAX_DURATION_SECS:
    raise ValueError(
        f"REDIS_RESULT_QUEUE_TTL_SECS ({REDIS_RESULT_QUEUE_TTL_SECS}s) must exceed "
        f"BATCH_MAX_DURATION_SECS ({BATCH_MAX_DURATION_SECS}s): a reply queue that "
        f"expires before its batch can finish silently discards undrained results."
    )
```

The preceding comment (`config.py:245-251`) already names the failure mode precisely — including "the test suite would not notice: it imports these with their defaults". It stops one step short: it treats "fail fast at import" as sufficient, without noting that *there are two imports, in two processes, each seeing only half the deployed truth*.

`PJE_BASE_URL` guard — `config.py:79-84`:

```python
_pje_url = os.getenv("PJE_BASE_URL", "https://pje.tjes.jus.br/pje")
if _pje_url != "https://pje.tjes.jus.br/pje" and (
    not _pje_url.startswith("https://") or ".jus.br" not in _pje_url
):
    raise ValueError(f"PJE_BASE_URL must be HTTPS .jus.br URL, got: {_pje_url}")
```

Structurally different and *not* a useful precedent: it validates a **single** value against a **constant** rule, so per-process evaluation is complete. The TTL guard validates a **relation between two values owned by different processes** — the same enforcement mechanism does not carry over.

### A3. `docker-compose.yml`

- **No `env_file:` anywhere in the file.** Confirmed by reading all 143 lines.
- `dashboard` `environment:` — `docker-compose.yml:49-70`. 21 keys. **Contains neither `BATCH_MAX_DURATION_SECS` nor `REDIS_RESULT_QUEUE_TTL_SECS`.** No timing var from the table is present.
- `worker` `environment:` — `docker-compose.yml:101-117`. 15 keys. **Contains neither.** No timing var from the table is present.
- Not one constant from the A1 table is plumbed into either container.

Corroborating: `.dockerignore:7-8` excludes `.env` and `.env.*` from the build context, and the only volume mounted into either service is `downloads_data:/data` (`docker-compose.yml:74,121`) — the app directory is **not** bind-mounted. So `config.load_env()` (`config.py:12-31`), which looks for `PJE_DOWNLOAD_ENV_FILE` or a repo-local `.env`, finds **no `.env` inside either container**. The host's `/opt/pje-download/.env` is consumed only by the Compose CLI for `${VAR}` interpolation — never injected.

**Consequence:** to set these vars today, an operator must edit `docker-compose.yml` itself. That is precisely the single-sided edit that creates the divergence, and it requires touching only one service block to do so.

### A4. `.github/workflows/deploy.yml`

`.env` is written at `deploy.yml:77-112` via `appleboy/ssh-action`, secrets passed through `env:` + `envs:` (lines 79-88), then a `printf` block redirected to `/opt/pje-download/.env` (lines 91-112). The 17 keys written are: `APP_ENV`, `MNI_USERNAME`, `MNI_PASSWORD`, `MNI_TRIBUNAL`, `MNI_TIMEOUT`, `MNI_BATCH_SIZE`, `MNI_ENABLED`, `PJE_BASE_URL`, `DASHBOARD_API_KEY`, `REDIS_PASSWORD`, `REDIS_URL`, `DOWNLOAD_BASE_DIR`, `HEALTH_PORT`, `DASHBOARD_PORT`, `WORKER_HEALTH_HOST`, `TRUST_X_FORWARDED_FOR`, `AUDIT_LOG_RETENTION_DAYS`.

**No timing var is set.** Note also this writer is *authoritative* — `>` truncates — so any hand-edit to the VPS `.env` is erased on the next deploy. A spec must not propose "just set it in the VPS `.env`" as a remedy: it would silently revert.

Restart step — `deploy.yml:114-131`: `docker compose --profile worker up -d --build dashboard worker` (line 128), preceded by `cd /opt/pje-download` and `set -e`. **Both services are rebuilt and restarted from a single command, from one commit.** (Relevant to rollout, §C.)

---

## B. Existing cross-process handshakes

### B5. Worker `/health` — `worker.py:1756-1826`

Response body (`worker.py:1812-1825`): `service`, `status`, `healthy`, `checks{mni, redis, disk, disk_free_mb}`, `mni_enabled`, `session_valid`, `fallback_ready`, `docs_downloaded`, `uptime_minutes`. Status 200 when healthy, 503 otherwise (`worker.py:1811`).

**No config value is exposed today.** It reports liveness/resources only.

**Could it carry the effective TTL?** Yes, trivially — one key in the dict at `worker.py:1812`. It is JSON, additive, unversioned. Registered at `worker.py:1720`. This is the enabling fact for option (i).

### B6. Dashboard → worker HTTP — YES, this already exists

- `DashboardState.get_worker_http()` — `dashboard_api.py:214-215`, "Reuse a single HTTP session for worker health polling."
- `_fetch_worker_health(state)` — `dashboard_api.py:1034-1051`. GETs `http://{WORKER_HEALTH_HOST}:{WORKER_HEALTH_PORT}/health` (line 1038-1039).
- **Endpoint:** `http://worker:8006/health` in prod (`WORKER_HEALTH_HOST: worker` — `docker-compose.yml:62`; also set in `.env` at `deploy.yml:109`).
- **Timeout:** UNKNOWN — no explicit `timeout=` on the `sess.get(...)`; whether `get_worker_http()` sets a session-level `ClientTimeout` was not read. aiohttp's default is 5 min total. A spec relying on this call must pin an explicit timeout.
- **Cadence:** on-demand, not periodic — called from `dashboard_api.py:978` inside a status handler. Not a background poller.
- **On failure:** `except Exception: return {"status": "unreachable", "healthy": False}` (`dashboard_api.py:1050-1051`). Fully swallowed — **fail-soft, never raises, never logs**. Any invariant assertion built on this path would inherit that silence unless deliberately changed.

So a channel exists, but it is a *display* channel with swallow-everything semantics, and it is not on the batch-start path.

### B7. Shared state in Redis — NONE

Searched for settings/heartbeat/registration keys written at startup by either app. The only Redis keys in the system are the queues themselves: `kratos:pje:jobs` (`worker.py:1634`, `dashboard_api.py:663`), `kratos:pje:results` (unsuffixed, n8n control plane), `kratos:pje:results:<batch_id>` (per-batch, `dashboard_api.py:321`), and `kratos:pje:dead-letter`.

**There is no existing shared-state-in-Redis pattern to build on.** Option (i) would introduce the repo's first such key — a genuinely new category of state, with a new staleness surface.

### B8. `protocol.py` — YES, and this is the clean seam

`protocol.py` (123 lines, "**zero side effects** at import time") defines `JobMessage` (`:27-41`) with required `jobId`/`numeroProcesso` and six `NotRequired` optional fields — including `replyQueue` (`:37`). `job_from_json` (`:94-107`) validates *only* that the payload is a dict containing `jobId` and `numeroProcesso`; **all other fields pass through untouched**.

Dashboard build site — `dashboard_api._batch_job_payload`, `dashboard_api.py:485-497`. **This is the single construction point for `JobMessage`.** It already stamps `replyQueue=self._result_queue(job.id)` (line 494). It is called at `dashboard_api.py:863-865` to build `serialized_payloads` — the stamp would happen **before** serialization, in the same expression. Verified: there is no second build site.

Worker consumption — `consume_queue` deserialises via `job_from_json` (`worker.py:1661`), then:
- `_publish_result(result_data, queue_name=job.get("replyQueue", ...))` at `worker.py:1688-1692` — `job` is in scope, so a ttl kwarg threads in directly.
- `_publish_progress(self, job, ...)` (`worker.py:1522`) already **receives the job dict** and reads `job.get("jobId")`, `job.get("batchId")` (`worker.py:1550-1552`) — `job.get("replyQueueTtlSecs")` needs no new plumbing at all.

Both TTL-arming sites (`worker.py:1494` result, `worker.py:1563` progress) funnel through `rpush_with_ttl` (`worker.py:103-125`), whose only use of the constant is `pipe.expire(queue_name, REDIS_RESULT_QUEUE_TTL_SECS)` at line 123 — one parameter away from being caller-supplied.

**Assessment: yes, seriously — this is the cleanest fix.** The wire already carries per-job routing metadata from the same producer; adding a TTL is the same kind of field, at the same site, consumed at the same place.

One design note, load-bearing: prefer a **relative TTL (seconds)** over an **absolute deadline (timestamp)**. `rpush_with_ttl`'s documented contract (`worker.py:108-113`) is that the TTL is *re-armed on every write* so "a live batch never expires, an abandoned one self-cleans". An absolute deadline destroys that property and, worse, would be stale on the resume path (§D3). A relative value preserves the existing semantics exactly.

---

## C. Options

Throughout: **"closes"** = the invariant is enforced by a single process that sees both halves. **"narrows"** = the window shrinks but two independent sources of truth remain.

### (i) Worker publishes effective TTL to a Redis key at startup; dashboard asserts at batch start

- **Closes?** **Narrows only.** Detection, not prevention. The dashboard learns of divergence at batch start; between the worker writing the key and that check, nothing is enforced. Worse, it is a *reporting* mechanism whose accuracy depends on a key staying in sync with the process that wrote it.
- **New failure modes:** (a) **startup ordering** — worker may not have written the key when the first batch starts (see §D1); (b) **staleness** — worker restarts with new config but the old key survives, so the dashboard asserts against a value the worker no longer uses; (c) introduces the repo's first shared-state key (§B7), and Redis here runs `--maxmemory 96mb --maxmemory-policy allkeys-lru` (`docker-compose.yml:25-26`) — **an untagged key is LRU-evictable**, so absent-key handling is not a corner case but a routine event.
- **Rollout:** old worker + new dashboard ⇒ key never written ⇒ every batch hits the absent-key path. Requires that path to be permissive, which blunts the whole point.
- **Test:** can be tested single-interpreter (write key, run assertion), but the test validates the *detector*, not the invariant.

### (ii) Dashboard stamps the TTL into the job message; worker arms that value — **RECOMMENDED**

- **Closes? YES.** The closure argument: the dashboard computes `REDIS_RESULT_QUEUE_TTL_SECS` at import via the same derivation, and its own import-time guard (`config.py:252`) has *already proved* `TTL > BATCH_MAX` **within that process**. If the dashboard stamps its own derived TTL and the worker arms exactly that, then the armed TTL is provably greater than the ceiling enforced by the same process that produced it. The relation becomes a **single-process invariant**. The worker's constant stops being a source of truth for owned queues.
- **Scope:** ~6 lines. `protocol.py` +1 field; `dashboard_api._batch_job_payload:489-497` +1 kwarg; `worker.rpush_with_ttl:103` +1 param; two call sites `worker.py:1494`, `worker.py:1563`.
- **New failure modes:** one, and it must be handled explicitly — **field-absent fallback** (§D4). Wire-format change is additive and non-breaking (below).
- **Rollout:** `job_from_json` (`protocol.py:94-107`) validates only two fields and passes the rest through ⇒ **new dashboard + old worker**: worker ignores the unknown field, arms its own constant — i.e. exactly today's behaviour, no regression. **Old dashboard + new worker**: field absent, worker falls back to its constant — again today's behaviour. Both mixed directions safe. And per `deploy.yml:128`, a single `docker compose --profile worker up -d --build dashboard worker` rebuilds both from one commit, so skew is transient (a job in flight across the restart), never persistent.
- **Test — this is the decisive advantage.** Set the *worker's* `REDIS_RESULT_QUEUE_TTL_SECS` to something absurd (e.g. `1`), feed a job carrying a sane `replyQueueTtlSecs`, assert the `EXPIRE` argument follows the **job**, not the constant. That is a real cross-container-divergence test **inside one interpreter** — because divergence is now representable as "two different numbers in one process", which is exactly what the fix makes irrelevant. Under the current design this test cannot be written at all: the existing suite (`tests/test_result_queue_ttl.py:37,44`) can only assert `config.X > config.Y` with both read from one env, which is precisely the blind spot `config.py:248-249` admits to.

### (iii) Dashboard arms/refreshes the TTL itself

- **Closes? No — and it is strictly worse than (ii).** The worker's own docstring rules this out: `worker.py:105-107` — "The worker is what brings `kratos:pje:results:<batch_id>` into existence, so the expiry has to be set here — **a bare RPUSH recreates a key with no expiry even right after the dashboard deleted it**." The RPUSH+EXPIRE is pipelined `transaction=True` (`worker.py:121-125`) specifically so a write cannot land without its expiry. A dashboard refresher cannot be atomic with a worker write; there is always a window where the key exists with no expiry. The dashboard also deletes the queue at `dashboard_api.py:658` and `:899`, adding more races.
- **Verdict: reject.** It reintroduces the exact leak PR #33/#34 fixed.

### (iv) Single source of truth — one service owns both constants

Two readings:
- **(iv-a) Move `BATCH_MAX_DURATION_SECS` ownership so both live in one service.** Not possible without moving the batch poll loop itself: the ceiling is enforced at `dashboard_api.py:706`, inside the dashboard's poll loop. The constant must be read where the loop runs.
- **(iv-b) Derive the TTL from a value that travels with the batch.** This *is* option (ii). (ii) is the concrete implementation of (iv); they are not competing options.

### (v) Do nothing beyond the 24h floor; document

- **Closes? No, but it is a defensible position** — and honest about the current state. The floor (`config.py:230-232`, 24h) means the effective default TTL is `max(3600+1800, 86400) = 86400`, a **24× margin** over the 3600s ceiling. For divergence to bite, an operator must raise `BATCH_MAX_DURATION_SECS` past 24h *or* explicitly lower the TTL on the worker — both deliberate acts.
- **Cost:** the hole stays latent, and `config.py`'s comment actively invites the edit that opens it. The failure is silent and the symptom (`batch failed, files on disk`) has already burned this project twice — the exact reason the guard exists.
- **Reasonable as an interim** if paired with a comment at `config.py:252` naming the cross-process limitation, so the next reader does not over-trust the guard.

### Recommendation

**Adopt (ii), keep (v)'s floor and the existing guard as defense-in-depth.**

Reasoning: it is the only option that *closes* rather than narrows; it adds no new state, no new key, no startup-ordering dependency, no new network call; it is ~6 lines at seams that already exist for exactly this kind of metadata; it is backward-compatible in both mixed-version directions; and it is the only option under which the invariant becomes testable at all. The `config.py:252` guard should **stay** — post-(ii) it still catches the fallback path and single-process misconfig, and removing it would trade one silent failure for another.

---

## D. Risks a spec must not get wrong

**D1. Startup ordering (kills option (i), N/A for (ii)).** `docker-compose.yml:122-124` gives `worker` only `depends_on: redis (service_healthy)` — there is **no ordering between dashboard and worker**, and `dashboard` likewise depends only on redis (`:75-77`). The dashboard can start, and `resume_active_batch` can fire (`dashboard_api.py:1413`), before the worker has written anything. Any design requiring "worker publishes, dashboard reads" must define absent-key behaviour as a **normal** state, not an error. Option (ii) sidesteps this entirely — the value travels with the message that already carries `replyQueue`.

**D2. Staleness after redeploy (kills option (i)).** A Redis-published TTL key outlives the worker that wrote it. After `up -d --build`, a worker with new config can be serving while the dashboard reads the previous generation's value. Any such key needs a TTL of its own *and* a generation marker — and under `allkeys-lru` with a 96mb cap (`docker-compose.yml:25-26`) it can vanish at any moment regardless.

**D3. Resume path — verify no regression.** `resume_active_batch` (`dashboard_api.py:438`) re-enters `_run_batch(enqueue_jobs=False)`; `_enqueue_batch` then skips **both** the queue delete and the job re-publish (`dashboard_api.py:657-664`). **No new job message is published on resume**, so no fresh `replyQueueTtlSecs` reaches the worker. This does **not** regress under (ii): any worker still processing holds the ttl from its original job message, and a worker that restarted has no in-flight job at all. But it is decisive for the relative-vs-absolute choice — an absolute deadline stamped at original enqueue would be **stale or already past** on a late resume, converting a recoverable batch into an instantly-expiring one. **Use relative seconds.**

**D4. The new silent failure — the central trap.** If the worker falls back to its own constant whenever `replyQueueTtlSecs` is absent, divergence returns **silently**, and now with a false sense of closure. Mitigations the spec must mandate: (a) fall back **only** for queues where `owns_queue_lifecycle()` is false, or where the field is genuinely absent (old dashboard); (b) **log at WARNING** on every fallback for an owned queue — this is the signal that a producer is out of date; (c) **keep the `config.py:252` guard** so the fallback value is still self-consistent; (d) validate the stamped value (positive int, sane bounds) rather than trusting the wire — a malformed field must fall back loudly, never arm `EXPIRE` with garbage.

**D5. Assertion-failure semantics (must be decided explicitly, whichever option).** Three candidate behaviours, and the spec must pick one per site: **refuse to start** (matches `config.py`'s existing fail-fast posture, but a worker/dashboard that won't boot over a timing mismatch is a self-inflicted outage), **fail the batch loudly** (safest for data — the invariant's whole purpose is preventing silent result loss), **log and continue** (matches `_fetch_worker_health`'s existing swallow-everything style at `dashboard_api.py:1050`, and is the wrong default here — it recreates the silence). Recommendation: for (ii), no runtime assertion is needed on the happy path; only D4's fallback WARNING.

**D6. Do not touch the unsuffixed control-plane queue.** `owns_queue_lifecycle` (`worker.py:92-100`) deliberately excludes `kratos:pje:results` — the n8n queue, whose durability contract is out-of-repo (`worker.py:94-99`: "a workflow paused longer than the TTL would come back to results that had aged out rather than failed, with nothing logged"). Stamping must apply **only** to owned `kratos:pje:results:<batch_id>` queues. `rpush_with_ttl:117-119` already early-returns for non-owned queues; that branch must remain untouched.

**D7. Both write sites, not just the result site.** `rpush_with_ttl` is called at **`worker.py:1494`** (result) *and* **`worker.py:1563`** (progress). A fix that threads the ttl only through `_publish_result` leaves progress writes re-arming the old constant — and progress writes are far more frequent, so they would *dominate* the effective TTL (last write wins). Missing this site silently defeats the entire fix. There is also an AST-level guard test (`tests/test_result_queue_ttl.py:233-250`) asserting all reply-queue writes go through `rpush_with_ttl`; a signature change must keep that test meaningful.

**D8. Don't propose fixing this via the VPS `.env`.** `deploy.yml:91-112` truncates and rewrites `/opt/pje-download/.env` on every deploy, and (§A3) the file never reaches the containers anyway. Any remedy must live in `docker-compose.yml`, `deploy.yml`'s printf block, or code.

---

## Explicit UNKNOWNs

- **Timeout on the dashboard→worker `/health` GET** (`dashboard_api.py:1037-1040`): no per-request `timeout=`; whether `get_worker_http()` (`dashboard_api.py:214-215`) configures a session-level `ClientTimeout` was not read. Matters only if a spec builds on that path (option (i)).
- **Live prod values.** Not read from the running VPS. The inference "both containers use defaults" rests on static evidence (no `env_file:`, absent from both `environment:` blocks, `.dockerignore` excludes `.env`, no app-dir bind mount) — strong, but a spec author who can reach the box should confirm with `docker compose exec dashboard python -c "import config; print(config.BATCH_MAX_DURATION_SECS, config.REDIS_RESULT_QUEUE_TTL_SECS)"` and the same on `worker`. Per the repo's own `feedback_compose-env-file-vs-interpolation` rule, **exercise `Settings()` inside both containers** — that is the only proof.
