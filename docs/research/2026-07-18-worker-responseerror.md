# Research 03 — `redis.ResponseError` escaping `consume_queue`

Repo: `/home/fbmoulin/projetos-26-2/pje-download` (branch `master`)
Prod: Hostinger VPS `91.108.125.85`, `/opt/pje-download`, docker compose
All section-A facts below were read from the **running prod worker container**
(`redis.__version__` printed as `8.0.0`), not from the local venv and not from memory.

**Headline verdict up front:** the bug as described is **real and correctly diagnosed**
(the tuple does not cover `ResponseError`; the call site has no enclosing try; escape kills
the consumer). But on **this** deployment neither of the two plausible triggers is reachable:
OOM is priced out by `allkeys-lru` + 1.5% memory utilisation, and `WRONGTYPE` is unreachable
because the codebase issues no non-list command against any list key. This is therefore a
**latent robustness gap, not an active fire** — which should scope the spec toward
*structural* containment (one bad job must not kill the consumer) rather than an urgent
hotfix. See §B and §E.

**One correction to the brief's premise:** `_publish_result` is **not** the only escape
site. `_publish_progress` is called twice from *inside* `download_process`'s own `except`
handler (`worker.py:812`, `:823`), where an exception cannot be caught by that handler and
propagates identically — a crash *while reporting a job failure*. Fixing `_publish_result`
alone leaves that path live. See §C8b; it is the decisive argument for §E option (iii).

---

## A. Exception taxonomy — redis-py 8.0.0 (live from prod container)

### A1. Class hierarchy

Command run:
```
ssh -i ~/.ssh/pje_deploy deploy@91.108.125.85 \
  'cd /opt/pje-download && docker compose exec -T worker python -c "..."'
```
Output (`redis version: 8.0.0`), each line `Name <- [first 4 MRO entries]`:

```
AskError                             <- ['ResponseError', 'RedisError', 'Exception', 'BaseException']
AuthenticationError                  <- ['ConnectionError', 'RedisError', 'Exception', 'BaseException']
AuthenticationWrongNumberOfArgsError <- ['ResponseError', 'RedisError', 'Exception', 'BaseException']
AuthorizationError                   <- ['ConnectionError', 'RedisError', 'Exception', 'BaseException']
BusyLoadingError                     <- ['ConnectionError', 'RedisError', 'Exception', 'BaseException']
ChildDeadlockedError                 <- ['Exception', 'BaseException', 'object']
ClusterCrossSlotError                <- ['ResponseError', 'RedisError', 'Exception', 'BaseException']
ClusterDownError                     <- ['ClusterError', 'ResponseError', 'RedisError', 'Exception']
ClusterError                         <- ['RedisError', 'Exception', 'BaseException', 'object']
ConnectionError                      <- ['RedisError', 'Exception', 'BaseException', 'object']
CrossSlotTransactionError            <- ['RedisClusterException', 'Exception', 'BaseException', 'object']
DataError                            <- ['RedisError', 'Exception', 'BaseException', 'object']
ExecAbortError                       <- ['ResponseError', 'RedisError', 'Exception', 'BaseException']
ExternalAuthProviderError            <- ['ConnectionError', 'RedisError', 'Exception', 'BaseException']
IncorrectPolicyType                  <- ['Exception', 'BaseException', 'object']
InvalidPipelineStack                 <- ['RedisClusterException', 'Exception', 'BaseException', 'object']
InvalidResponse                      <- ['RedisError', 'Exception', 'BaseException', 'object']
LockError                            <- ['RedisError', 'ValueError', 'Exception', 'BaseException']
LockNotOwnedError                    <- ['LockError', 'RedisError', 'ValueError', 'Exception']
MasterDownError                      <- ['ClusterDownError', 'ClusterError', 'ResponseError', 'RedisError']
MaxConnectionsError                  <- ['ConnectionError', 'RedisError', 'Exception', 'BaseException']
ModuleError                          <- ['ResponseError', 'RedisError', 'Exception', 'BaseException']
MovedError                           <- ['AskError', 'ResponseError', 'RedisError', 'Exception']
NoPermissionError                    <- ['ResponseError', 'RedisError', 'Exception', 'BaseException']
NoScriptError                        <- ['ResponseError', 'RedisError', 'Exception', 'BaseException']
OutOfMemoryError                     <- ['ResponseError', 'RedisError', 'Exception', 'BaseException']
PubSubError                          <- ['RedisError', 'Exception', 'BaseException', 'object']
ReadOnlyError                        <- ['ResponseError', 'RedisError', 'Exception', 'BaseException']
RedisClusterException                <- ['Exception', 'BaseException', 'object']
RedisError                           <- ['Exception', 'BaseException', 'object']
ResponseError                        <- ['RedisError', 'Exception', 'BaseException', 'object']
SlotNotCoveredError                  <- ['RedisClusterException', 'Exception', 'BaseException', 'object']
TimeoutError                         <- ['RedisError', 'Exception', 'BaseException', 'object']
TryAgainError                        <- ['ResponseError', 'RedisError', 'Exception', 'BaseException']
WatchError                           <- ['RedisError', 'Exception', 'BaseException', 'object']
```

Three structural facts a spec must internalise:

1. **`OutOfMemoryError` DOES exist in 8.0.0** and is a subclass of `ResponseError`
   (it is *not* a subclass of `ConnectionError`). Answers the open question in the brief.
2. **`AuthenticationError`, `AuthorizationError`, `BusyLoadingError`, `MaxConnectionsError`
   subclass `ConnectionError`** — so they are *already caught* by the current tuple, and
   therefore *already retried up to 3× with backoff*. `AuthenticationError` being retried is
   a pre-existing (minor) wart: a bad password will burn the full retry budget before
   falling back to local log. Worth a note in the spec, not necessarily a fix.
3. **`WatchError` and `DataError` subclass `RedisError` directly**, bypassing both
   `ConnectionError` and `ResponseError`. Any fix phrased as "catch `ResponseError` too"
   still leaves these two uncaught. This matters for option (ii) in §E.

### A2. What `pipeline.execute()` / `rpush` can raise here

`rpush_with_ttl` (`worker.py:103-125`) has two branches: a bare `rpush` for
non-owned queues (`worker.py:118`) and a `transaction=True` pipeline of
`RPUSH` + `EXPIRE` (`worker.py:121-125`).

| Exception | Reachable from this call? | Notes |
|---|---|---|
| `ResponseError` (base) | Yes, in principle | Any `-ERR` reply. Umbrella for the rows below. |
| `OutOfMemoryError` | Yes in principle; **not on this deployment** (§B5) | `-OOM command not allowed when used memory > 'maxmemory'`. Only raised when the write cannot be satisfied *and* eviction cannot free space. |
| `ExecAbortError` | Yes — pipeline path only | `EXEC` aborted because a queued command errored at queue-time. Requires a malformed command; not reachable with fixed `RPUSH`/`EXPIRE` argument shapes. |
| `WatchError` | **No** | Requires `WATCH`; this code never calls `WATCH`. `transaction=True` alone does not `WATCH`. |
| `DataError` | Yes — client-side | Raised *before* the wire, on un-encodable argument types. Payloads here are `str` from `result_to_json`/`progress_to_json`, so effectively unreachable. Note it is **not** a `ResponseError`. |
| `NoScriptError` | **No** | Requires `EVALSHA`; never used. |
| `AuthenticationError` | Yes | But subclasses `ConnectionError` → already caught. |
| `ReadOnlyError` | **No** | Replica-write error. Single standalone Redis, no replication (`docker-compose.yml:16-35`). |
| `WRONGTYPE` (surfaces as plain `ResponseError`) | **No** on this codebase — see §B7 | The one that would make this bug live. It is not. |

### A3. Transient vs permanent classification

| Exception | Class | Retry helps? |
|---|---|---|
| `ConnectionError`, `TimeoutError`, `BusyLoadingError`, `MaxConnectionsError` | TRANSIENT | Yes — this is what the current tuple is for. |
| `OutOfMemoryError` | **TRANSIENT-ish** | Arguably yes — memory can free up (TTL expiry, eviction, dashboard `delete`). But bounded: 3 attempts over ~<10s is unlikely to outlast a real memory crisis. Retrying is *harmless* but rarely curative. |
| `TryAgainError`, `ClusterDownError`, `MovedError`/`AskError` | TRANSIENT (cluster) | N/A — not clustered here. |
| `WRONGTYPE` / generic `ResponseError` | **PERMANENT** | No. Retrying a type mismatch or a syntax error never succeeds; it only delays the local-log fallback by the full backoff budget. |
| `ExecAbortError` | PERMANENT | No. Deterministic command-shape error. |
| `DataError` | PERMANENT | No. Client-side encoding bug. |
| `NoPermissionError` | PERMANENT | No. ACL misconfiguration. |
| `AuthenticationError` | PERMANENT (despite being a `ConnectionError`) | No — but currently retried anyway. |
| `ReadOnlyError` | TRANSIENT (post-failover) | N/A here. |

The asymmetry is the crux: `ResponseError` is a *category* spanning both transient
(`OutOfMemoryError`) and permanent (`WRONGTYPE`, `ExecAbortError`) faults. A spec that
treats "`ResponseError` = non-retriable" is right for the common cases and slightly wrong
for OOM; a spec that treats it as retriable is wrong for the permanent ones. §E addresses this.

### A4. redis-py's own internal retry — live values

```python
c = redis.asyncio.from_url(os.environ["REDIS_URL"], decode_responses=True)
c.get_retry()                                            -> None
c.connection_pool.connection_kwargs.get("retry_on_error") -> None
```

Also confirmed with a bare `from_url("redis://localhost:6379")`: `retry` is `None`,
`_retries`/`_supported_errors`/`_backoff` all `None`, and `connection_kwargs` contains only
`['host','port']`.

**Conclusion: there is no library-level retry in play on this deployment.** Every retry the
system performs is the hand-rolled loop in `_publish_result` or `AsyncRetry`. In particular
redis-py is **not** silently retrying `ResponseError` behind the app's back — when the server
returns `-ERR`, it propagates on the first attempt. The spec cannot lean on library retry.

Client construction, both sides, identical shape:
- `worker.py:227-231` — `redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=REDIS_SOCKET_TIMEOUT_SECS)`
- `dashboard_api.py:206-210` — same three arguments.

Neither passes `retry`, `retry_on_error`, or `retry_on_timeout`.

---

## B. Reachability on THIS deployment

### B5. maxmemory / eviction policy

`docker-compose.yml:20-28`:
```yaml
  redis:
    image: redis:7.4-alpine
    mem_limit: 128m
    command: >
      redis-server
      --requirepass ${REDIS_PASSWORD:-pje_redis_2026}
      --maxmemory 96mb
      --maxmemory-policy allkeys-lru
```

**Mechanism, precisely.** Redis returns `-OOM` on a write only when `used_memory` would
exceed `maxmemory` *and* the eviction policy cannot free enough space. Under
`allkeys-lru` **every key in the keyspace is an eviction candidate** — unlike
`noeviction` (returns `-OOM` immediately) or the `volatile-*` policies (can only evict keys
carrying a TTL, and return `-OOM` when no such key exists). So the two residual `-OOM`
paths under `allkeys-lru` are:

1. A single value larger than `maxmemory` — impossible here; payloads are small JSON
   result/progress messages.
2. The keyspace is exhausted of evictable keys and the write still doesn't fit — requires
   the DB to be essentially empty while a huge write is in flight. Not reachable at this scale.

There is also a **container-level** hazard that is *not* an `-OOM` `ResponseError`:
`mem_limit: 128m` vs `--maxmemory 96mb` leaves only 32MB of headroom for Redis overhead
(fragmentation, client buffers, COW during RDB save). If the cgroup OOM-killer fires, the
worker sees a `ConnectionError` — already in the caught tuple — not a `ResponseError`.
Worth flagging as a separate observation, out of scope for this bug.

### B6. Live prod memory (read-only)

Read via the app's own `REDIS_URL` inside the worker container (the `.env`-grep route was
blocked by the permission classifier; this route needed no secret handling):

```
used_memory            = 1493112
used_memory_human      = 1.42M
used_memory_peak_human = 1.48M
maxmemory              = 100663296
maxmemory_human        = 96.00M
maxmemory_policy       = allkeys-lru
dbsize                 = 5
```

**1.42MB of 96MB — roughly 1.5% utilisation, peak 1.48M.** Five keys total. There are
~65× headroom and an LRU policy that evicts rather than erroring.

**Verdict on OOM: not reachable on this deployment.** Not "unlikely" — the policy makes
`-OOM` require a degenerate condition that this workload cannot produce.

### B7. Is `WRONGTYPE` reachable?

`WRONGTYPE` arises when a command for one type hits a key holding another. It surfaces as a
plain `ResponseError`.

Grep run **repo-wide over all `*.py`** (`grep -rnE ... --include=*.py .`), not just the two
obvious modules. Two independent sweeps:

1. **Who even holds a Redis client:** `import redis` / `from_url` / `redis.asyncio` /
   `.Redis(` appears in non-test code at exactly **two** files —
   `worker.py:31,227` and `dashboard_api.py:37,206`. `audit_sync.py`,
   `batch_downloader.py`, `mni_client.py`, `metrics.py`, `gdrive_downloader.py`,
   `pje_session.py`, `file_utils.py`, `protocol.py`, `audit.py`, `config.py` and
   `tools/verify_spec.py` **never touch Redis** (`audit_sync.py` is Postgres/asyncpg).
   So the surface really is confined to the two modules.
2. **Every Redis data-structure command**, repo-wide, non-test:

```
worker.py:118          await redis_client.rpush(queue_name, payload)
worker.py:122          pipe.rpush(queue_name, payload)
worker.py:123          pipe.expire(queue_name, REDIS_RESULT_QUEUE_TTL_SECS)
worker.py:1597         await self.redis.lpush(DEAD_LETTER_QUEUE, ...)
worker.py:1633         result = await self.redis.blpop("kratos:pje:jobs", timeout=...)
dashboard_api.py:180   client.rpush(key, *values)
dashboard_api.py:658   await redis_client.delete(reply_queue)
dashboard_api.py:718   item = await redis_client.blpop(...)
dashboard_api.py:899   await self._redis.delete(reply_queue)
```
The repo-wide sweep returns many additional hits, **all of them non-Redis** and verified as
such: Python `list.append(...)` (`mni_client.py`, `gdrive_downloader.py`, `pje_session.py`,
`file_utils.py`, `config.py`, `tools/verify_spec.py`, and throughout), Prometheus gauge
`.set(...)` (`metrics`-prefixed: `dashboard_api.py:304,424,836,893`,
`batch_downloader.py:706`), and `asyncio.Event.set()` (`worker.py:1912`,
`dashboard_api.py:1357`, `audit_sync.py:389`). None is a Redis call.

**Every Redis command in this codebase is `rpush` / `lpush` / `blpop` / `expire` / `delete`.**
There is no `SET`, `HSET`, `SADD`, `ZADD`, `SETEX`, or `INCR` anywhere. `EXPIRE` and `DELETE`
are type-agnostic. No code path can write a non-list type to a key used as a LIST.

**Verdict: `WRONGTYPE` is not reachable from this codebase.** Caveats worth one line in the
spec: (a) this holds only while that invariant holds — a future feature adding a `SET` on a
colliding key name reopens it; (b) an operator typing `SET kratos:pje:results:x foo` into
`redis-cli` could inject it manually; (c) n8n is an out-of-repo writer to
`kratos:pje:jobs` — but it is a *producer* using RPUSH by contract, and the worker only
BLPOPs there.

---

## C. Current code — exact blast radius

### C8. The three publish paths

| Path | Location | Except tuple | Enclosing try at call site? | On escape |
|---|---|---|---|---|
| `_publish_result` | `worker.py:1482-1520`; except at **`worker.py:1499`** | `(redis.ConnectionError, redis.TimeoutError, OSError)` | **NO** — called at `worker.py:1690-1693`, directly in the `while` body of `consume_queue` | Propagates out of `consume_queue` → out of `main()` → process exits. **This is the reported bug.** |
| `_publish_progress` | `worker.py:1522-1575`; except at **`worker.py:1566`** | same tuple | **PARTLY — see below** | Mostly contained; **fatal from exactly 2 call sites**. Details below. |
| `_publish_dead_letter` | `worker.py:1577-1605`; except at **`worker.py:1599`** | same tuple | **NO** — called at `worker.py:1664` and `worker.py:1672`, in the `while` body | Same fatal exit — and note this fires while handling an *already malformed* payload. |

All three tuples are identical and all three omit `ResponseError`. Note `_publish_progress`
and `_publish_dead_letter` merely log-and-continue on a caught error (they do **not**
fall back to local log) — only `_publish_result` has the durable fallback.

#### C8b. `_publish_progress` in detail — the escape surface is NARROW but nastier than it looks

`download_process` wraps its whole body in `try:` (**`worker.py:713`**) …
`except Exception as e:` (**`worker.py:799`**). This changes the picture materially:

- **Happy-path progress calls (`worker.py:719` through `:783`, plus the calls inside the
  `_phase_*` helpers at `:441`–`:592`, which execute within that same try) are CONTAINED.**
  A `ResponseError` there is caught by the broad `except Exception` at `:799` and converted
  into a `"failed"` (or `"session_expired"`) result. The consumer survives.
  **But it is contained by being mislabelled:** a *Redis publish* failure is reported to the
  operator and to n8n as a *download* failure (`log.error("pje.download.failed", ...)`,
  `worker.py:800`), with the downloaded files still on disk. That is its own
  silent-failure-shaped defect — same family as `CLAUDE.md:117,120` — and the spec should
  name it even if it chooses not to fix it here.
- **The two progress calls inside the `except` handler — `worker.py:812-819` (session_expired)
  and `worker.py:823-830` (failed) — ARE FATAL.** An exception raised inside an `except`
  block is not caught by that same block; it propagates out of `download_process`, into the
  `while` body of `consume_queue`, and out of the process (§C9).
  This is the worst-positioned instance in the file: the job has **already failed**, and a
  `ResponseError` while reporting that failure kills the consumer, losing both the result
  and the failure record. It also chains — `worker.py:812` runs after
  `invalidate_session()`, so the crash happens with the session already torn down.

**Correction to the original brief's framing:** the brief treats `_publish_result` as the
single escape site. There are in fact **three fatal call sites** — `worker.py:1690`
(`_publish_result`), `worker.py:812` and `worker.py:823` (`_publish_progress` inside the
error handler) — plus `worker.py:1664`/`:1672` (`_publish_dead_letter`). It is *not*
"~20 progress call sites", which an earlier read of this file wrongly inferred from the
call-site grep before the `try`/`except` boundaries at `:713`/`:799` were checked.
The practical consequence for the spec: fixing `_publish_result` alone leaves the
error-handler path escaping — which is a strong argument for option (iii) in §E.

`rpush_with_ttl` itself (`worker.py:103-125`) has **no** try/except; it is a pure pass-through
that lets everything propagate to its callers.

### C9. What happens when `consume_queue` raises

Call chain, `worker.py:1917-1930`:
```python
    async with async_playwright() as playwright:
        session_ok = await worker.load_session(playwright)
        if not session_ok:
            log.error("pje.main.session_init_failed", action="aborting")
            return

        try:
            await worker.consume_queue(shutdown_event)
        finally:
            await worker.close()

    log.info("pje.main.shutdown", status="graceful")
```

`try` / **`finally`** — there is **no `except`**. So:

1. The exception propagates out of `consume_queue`.
2. `worker.close()` runs (resources released), then the exception **continues to propagate**.
3. `log.info("pje.main.shutdown", status="graceful")` at `worker.py:1928` is **skipped** —
   so a crash leaves *no* shutdown log line. The absence of that line is the only
   distinguishing signal, which is a poor observability posture.
4. It escapes `main()`, `asyncio.run(main())` (`worker.py:1932`) re-raises, the process
   exits non-zero with a traceback on stderr.
5. `docker-compose.yml:98` — worker has **`restart: unless-stopped`** → the container
   restarts and re-enters `consume_queue`. So the *symptom* is a crash-loop / silent
   restart, not a permanently dead worker. If the trigger is permanent (e.g. `WRONGTYPE`)
   the worker restarts and immediately re-crashes on the next job — a genuine crash-loop.

**Is `_log_job_result` reached?** For an escaping `ResponseError`: **no.** The fallback at
`worker.py:1508-1512` lives *inside* the `except` block that does not match, so it never
executes. The downloaded files sit on disk with **no record in the local log and no message
on the reply queue**. The dashboard, polling the reply queue, sees nothing.

This is precisely the failure shape the repo already worries about elsewhere — cf. the
comment at `dashboard_api.py:200-205`, which explains `socket_timeout` as load-bearing
because otherwise "the batch is marked failed even though its files are on disk."

### C10. Is the job re-delivered after a crash?

**No. The job is lost.** Evidence:

- `worker.py:1633-1635` — `result = await self.redis.blpop("kratos:pje:jobs", timeout=REDIS_BLPOP_TIMEOUT_SECS)`.
  `BLPOP` **atomically removes** the element. Once it returns, the job exists only in the
  worker's local `job_json` variable.
- There is **no** `RPOPLPUSH` / `BLMOVE` / processing-list / in-flight-set / ack anywhere in
  the repo — the exhaustive command grep in §B7 lists every Redis call, and none of them is
  a reliable-queue primitive.
- Consequently a crash between BLPOP (`worker.py:1633`) and a successful publish
  (`worker.py:1690`) loses the job permanently. On restart the worker BLPOPs the *next*
  job; nothing re-delivers the lost one.

**This is a pre-existing at-least-once-delivery gap, independent of the `ResponseError`
bug.** The spec should explicitly scope it OUT (it is a separate, larger design change:
reliable queue pattern + idempotent re-processing + orphan recovery), while naming it as
the reason the escape is harmful rather than merely noisy. Fixing the exception handling
converts "job silently vanishes" into "job's result is durably logged locally" — it does
**not** make the job re-runnable. Any spec that implies otherwise is overclaiming.

---

## D. Prior art available for reuse

### D11. `async_retry.AsyncRetry` (`async_retry.py`)

Signature (`async_retry.py:60-77`):
```python
def __init__(
    self,
    *,
    attempts: int,
    backoff_cap_secs: float,
    retry_on: tuple[type[BaseException], ...],
    log_event: str = "retry",
    logger: Any = None,
) -> None:
```
`run(coro_factory, **log_extra)` (`async_retry.py:79-...`):
- `coro_factory` is a **zero-arg callable returning a fresh coroutine** per attempt.
- Backoff: `min(2**attempt + random.uniform(0, 1), backoff_cap_secs)`.
- `retry_on` semantics: anything not in the tuple **propagates immediately** — the docstring
  calls this out as "important for `CancelledError`".
- On exhaustion it **re-raises the last caught exception** (`async_retry.py` final lines),
  so the caller sees the original type.
- Logs `log_event` at `warning` with `attempt`, `delay_s`, `error`, plus `**log_extra`.

**Reusable here — with one important mismatch.** `_publish_result` does *not* re-raise on
exhaustion; it falls back to `_log_job_result` and returns. So a refactor onto `AsyncRetry`
must wrap `retry.run(...)` in a try that catches the re-raised exception and performs the
fallback. The module docstring (`async_retry.py:1-30`) already names
`worker._publish_result` as one of the three original hand-rolled loops and lists
`dashboard_api._rpush_with_retry` as sharing "identical semantics" — so consolidating
`_publish_result` onto `AsyncRetry` is *explicitly anticipated* prior art, not a new idea.

### D12. `dashboard_api._rpush_with_retry` — a real inconsistency

`dashboard_api.py:164-181`:
```python
    retry = AsyncRetry(
        attempts=_RPUSH_MAX_ATTEMPTS,
        backoff_cap_secs=10,
        retry_on=(redis.ConnectionError, redis.TimeoutError),
        log_event="dashboard.rpush.retry",
        logger=log,
    )
    return await retry.run(lambda: client.rpush(key, *values), key=key)
```

Differences from the worker's tuple:

| | worker `_publish_result` | dashboard `_rpush_with_retry` |
|---|---|---|
| `ConnectionError` | caught | caught |
| `TimeoutError` | caught | caught |
| **`OSError`** | **caught** (`worker.py:1499`) | **NOT caught** |
| `ResponseError` | not caught | not caught |
| On exhaustion | local-log fallback, returns | **re-raises** |

Two things for the spec: (a) the `OSError` divergence is unexplained and looks accidental —
worth unifying; (b) the docstring at `dashboard_api.py:166-167` claims it "Mirrors
`worker.py:_publish_result`'s pattern", which is now **inaccurate** on both the `OSError`
row and the exhaustion behaviour. If the spec changes one side it should fix that comment,
or the next reader inherits a false invariant. A shared, named classification constant
(e.g. `REDIS_TRANSIENT_ERRORS` in `config.py`) used by both sites is the natural unification.

Note also `dashboard_api.py:661-665` calls `_rpush_with_retry` to enqueue jobs onto
`kratos:pje:jobs`; a `ResponseError` there propagates into the dashboard request handler —
a different (HTTP-500-shaped, non-fatal) blast radius than the worker's.

### D13. The circuit breaker in `consume_queue`

`worker.py:1638-1651`:
```python
            except (redis.ConnectionError, redis.TimeoutError) as exc:
                consecutive_errors += 1
                delay = min(2**consecutive_errors + random.uniform(0, 1), 60)
                log.error("pje.queue.redis_error", ...)
                self._last_error = f"redis:{exc}"
                if consecutive_errors >= REDIS_CIRCUIT_THRESHOLD:
                    self._health_status = "redis_unreachable"
                await asyncio.sleep(delay)
                continue
```
Reset on success at `worker.py:1636-1637` (`consecutive_errors = 0`, health back to `consuming`).

Key observations:
- The breaker **only wraps the BLPOP**, not the publish paths. It is a *consumer-side*
  breaker with a third, narrower tuple (no `OSError`, no `ResponseError`) — so the repo now
  has **three** slightly different Redis-error tuples.
- `_health_status = "redis_unreachable"` drives `/health` → 503 → orchestrator/healthcheck
  action (`docker-compose.yml:124-129`).
- A publish-path `ResponseError` **never feeds this breaker**. If the spec adds handling
  that swallows publish errors, those failures become invisible to `/health` unless
  explicitly wired in. Worth a deliberate decision: a persistently failing publish path is
  arguably exactly what the breaker exists to surface. Counter-argument: publish failures
  already have `metrics.worker_publish_failures_total` (`worker.py:1501`, `1567`, `1600`),
  so the metric may be the better signal and the breaker should stay consumer-scoped.
  Recommend: metric + log, do **not** widen the breaker in this change (keeps the diff
  honest and avoids health-flapping on a single bad job).

### D14. Existing tests on the publish paths

`tests/test_worker.py`, `class TestPublishResult` (`tests/test_worker.py:647`):
- `test_publish_succeeds` — `:651`
- `test_publish_can_target_batch_reply_queue` — `:663`
- `test_publish_result_updates_metric` — `:684`
- `test_publish_retries_on_failure` — `:696`  ← **extend this one**; it currently proves retry on a transient error
- `test_publish_falls_back_to_local_log` — `:716` ← **extend this one**; it proves the `_log_job_result` fallback fires

Progress/dead-letter coverage:
- `test_publish_progress_uses_reply_queue` — `tests/test_worker.py:746`
- `test_invalid_json_is_sent_to_dead_letter_queue` — `tests/test_worker.py:553`
- `test_missing_fields_are_sent_to_dead_letter_queue` — `tests/test_worker.py:578`

TTL/pipeline coverage (relevant because `rpush_with_ttl` is the throwing frame):
- `tests/test_result_queue_ttl.py:113` `test_worker_publish_leaves_a_ttl_on_the_reply_queue`
- `tests/test_result_queue_ttl.py:219` `test_both_worker_publish_paths_set_a_ttl`
- `tests/test_result_queue_ttl.py:368-377` documents the pipeline mocking idiom
  (`pipe.rpush`/`pipe.expire` as `MagicMock`, not a bare `rpush`) — **a new test must use
  this idiom or it will assert against the wrong frame.**

`consume_queue`-level tests to model a new crash-containment test on:
`tests/test_worker.py:446, 462, 495, 539, 570, 595, 639, 1664` and
`tests/test_worker_consume_queue_session_expired.py:45-85` (uses
`asyncio.wait_for(worker.consume_queue(shutdown), timeout=2)` — the right harness shape).

**Gap: there is no test anywhere that asserts a publish-path exception does not kill
`consume_queue`.** That absence is why the bug is in `master`.

---

## E. Options analysis (core deliverable)

Framing constraint from this repo's own history: `CLAUDE.md:117,120` records two prior
production bugs (**B2**, **B5**) whose defining characteristic was *silent* degradation —
a frozen gauge and a silently-ignored PG version. This codebase's stated failure mode is
**errors that get swallowed**, not errors that get raised. Any option that widens a catch
must therefore justify what stays observable.

### Option (i) — widen the tuple to `redis.RedisError`

Change `(redis.ConnectionError, redis.TimeoutError, OSError)` → `(redis.RedisError, OSError)`
at `worker.py:1499`, `:1566`, `:1599`.

- **Fixes:** all three publish paths stop escaping, for every Redis-origin error including
  `ResponseError`, `WatchError`, `DataError`.
- **Newly hides:** per §A1 this swallows `DataError` (a *client-side encoding bug* — i.e.
  our own bug, e.g. `result_to_json` returning a non-str), `NoPermissionError` (ACL
  misconfiguration — an ops problem needing a human), and `WRONGTYPE` (a key-naming
  collision — a design bug). Worse, it swallows them **into the retry loop**: three
  exponential-backoff sleeps (~1s + ~2s + ~4s ≈ 7s) burned on an error that can never
  succeed, before the local-log fallback. Multiply by every job in a batch.
- **Verdict: reject as the primary mechanism.** It converts a loud crash into a quiet
  retry-then-log for a class of genuine bugs, which is exactly the B2/B5 pattern. It is
  also the *widest* possible change for a trigger that §B shows is not currently reachable.

### Option (ii) — add `ResponseError`, classified non-retriable

Split the handling: keep `(ConnectionError, TimeoutError, OSError)` as the *retriable* tuple;
add a second `except redis.ResponseError` that skips the backoff and goes **straight** to
`metrics.worker_publish_failures_total` + `log.error` + `_log_job_result`.

- **Fixes:** the exact reported bug; result is durably captured on first failure with no
  wasted retry budget.
- **Newly hides:** `ResponseError` specifically — acceptable *if* the error log is at
  `error` level with the exception text (so `WRONGTYPE`/`ERR` reaches the operator) and the
  metric increments. It does **not** hide `DataError`/`WatchError`, which continue to
  propagate — arguably correct, since both indicate our-side bugs, but it means this option
  **does not by itself guarantee the consumer survives every publish error** (§A1 fact 3).
  That residual is the reason (ii) alone is insufficient.
- **Nuance:** `OutOfMemoryError` is a `ResponseError` but is §A3-transient. Treating it as
  non-retriable is a slight mis-classification. Given §B5/B6 render it unreachable here,
  optimising for it is not worth the branch complexity — but the spec should say so
  explicitly rather than silently conflating the two.

### Option (iii) — per-job try around the `consume_queue` loop body

Wrap the body from `job_from_json` through the publish (roughly `worker.py:1657-1707`) in a
`try/except Exception` that logs, increments a metric, and `continue`s to the next job.

- **Fixes:** the *structural* defect — one bad job can never kill the consumer, regardless
  of which exception class is involved (`ResponseError`, `DataError`, or a future
  non-Redis bug in `download_process`).
- **Newly hides:** potentially a great deal — a bare `except Exception` around the download
  path could mask genuine logic bugs as a stream of "job failed, moving on" lines. Mitigate
  by: logging at `error` with `exc_info`/traceback, a dedicated metric, and **not**
  catching `asyncio.CancelledError` (bare `except Exception` already excludes it in py3.8+,
  since `CancelledError` derives from `BaseException` — worth an explicit comment so a
  future reader doesn't "helpfully" widen it to `BaseException`).
- **Must preserve:** the `break` paths for `session_expired` (`worker.py:1699-1702`) and
  `captcha_required` (`worker.py:1704-1707`) are *intentional* loop exits and must not be
  swallowed by the new handler. Since they are `break` statements on a status value, not
  exceptions, they are unaffected — but a careless refactor that moves them inside the try
  and converts them to exceptions would regress
  `tests/test_worker_consume_queue_session_expired.py`.
- **Alone it is insufficient:** it contains the crash but the in-flight result is still
  lost, because `_publish_result`'s fallback still never fires on a `ResponseError`.

### Option (iv) — RECOMMENDED: (ii) + (iii), in that order of precedence

Two layers, deliberately redundant:

1. **Inner (correctness):** in `_publish_result`, add `except redis.ResponseError` →
   no retry → `metrics.worker_publish_failures_total.labels(kind="result").inc()` +
   `log.error("pje.queue.result_publish_permanent", job_id=..., error=str(exc), error_class=type(exc).__name__)` +
   `await self._log_job_result(...)` → return. Mirror the no-retry-log-metric shape (minus
   the local-log fallback, which they don't have) in `_publish_progress:1566` and
   `_publish_dead_letter:1599`.
2. **Outer (containment):** per-job `try/except Exception` in the `consume_queue` loop body,
   logging with traceback + metric + `continue`. This is the backstop for
   `DataError`/`WatchError`/anything unforeseen — the classes layer 1 deliberately does not
   catch.

**Why layer 2 is not optional (§C8b):** the two fatal `_publish_progress` sites at
`worker.py:812` and `:823` live *inside* `download_process`'s own `except` handler, so no
amount of fixing `_publish_result` reaches them, and no `except` inside `download_process`
can catch them either. Only a handler at the `consume_queue` loop level contains that path.
A spec that ships layer 1 alone would close the reported bug and leave a second, strictly
nastier escape (crash while reporting a failure) still live in `master`.

**Why this combination:** layer 1 keeps the *result durable* (the actual harm in the bug
report is files-on-disk-with-no-record); layer 2 keeps the *consumer alive* without
requiring anyone to have enumerated every exception class correctly — which is the
assumption that failed in the first place. Neither is silent: both increment a metric
already scraped via `/metrics` (`worker.py:_metrics_handler`, exposed per
`tests/test_worker.py:1591-1623`) and both log at `error`.

Explicitly **not** recommended: widening to `RedisError` (option i) as the mechanism.

### How to test each

Harness idiom is established — mock the pipeline as in
`tests/test_result_queue_ttl.py:368-377` (`pipe.rpush`/`pipe.expire` MagicMocks), **not** a
bare `rpush`, or the mock misses the actual throwing frame.

1. **Unit, layer 1:** `pipe.execute` (or `rpush`) side-effect
   `redis.ResponseError("WRONGTYPE Operation against a key holding the wrong kind of value")`
   → assert `_log_job_result` awaited **once**, `worker_publish_failures_total{kind="result"}`
   incremented by 1, and — the load-bearing assertion —
   **`asyncio.sleep` was never awaited** (proves no retry budget burned).
   Extend `tests/test_worker.py:696` / `:716`.
2. **Unit, OOM discrimination (optional):** side-effect `redis.OutOfMemoryError` → assert
   whichever branch the spec chooses. Pins the §A3 nuance so a future reader can see it was
   a decision, not an oversight.
3. **Integration, layer 2:** the critical one. Drive `consume_queue` with a queue of two
   jobs where the first publish raises `ResponseError`; assert **the second job is still
   processed** and the loop exits only on `shutdown_event`. Model on
   `tests/test_worker_consume_queue_session_expired.py:45-85`
   (`asyncio.wait_for(worker.consume_queue(shutdown), timeout=2)`).
4. **Regression guard for over-catching:** assert `asyncio.CancelledError` still propagates
   out of `consume_queue` (i.e. shutdown is not swallowed by the new outer handler). Without
   this, layer 2 can silently break graceful shutdown.
5. **Non-regression:** `tests/test_worker_consume_queue_session_expired.py` must stay green —
   proves the intentional `break` paths survive the refactor.
6. **Prove the corpus rejects the broken code** (repo rule: a passing test on existing code
   proves nothing): confirm test 3 **FAILS** against current `master` before the fix lands.
   This is the gate that would have caught the bug originally.

---

## F. Risks — what the spec must not get wrong

1. **Catching too broadly is itself the bug class this repo keeps hitting.** `CLAUDE.md:117`
   (B2, frozen gauge) and `:120` (B5, silently-ignored PG version) are both silent-degradation
   incidents. An `except redis.RedisError` or a bare `except Exception` with a `warning`-level
   log reproduces that pattern. Every new catch must increment a metric **and** log at
   `error`.
2. **Do not retry non-retriable errors.** Three backoff sleeps (~7s) per job on a permanent
   `WRONGTYPE` delays the local-log fallback and, across a batch, looks like a hang. The
   retriable/non-retriable split is the point of the fix, not an optimisation.
3. **A publish failure must not be reported as a download failure.** Per §C8b, a
   `ResponseError` from a happy-path `_publish_progress` is currently absorbed by
   `download_process`'s `except Exception` (`worker.py:799`) and logged as
   `pje.download.failed` — the wrong subsystem, with files already on disk. The spec should
   decide explicitly whether to distinguish these; at minimum do not make it worse.
4. **The result must not be silently dropped.** The concrete harm is *files on disk with no
   record anywhere*. Any path that catches must reach `_log_job_result` or an equivalent
   durable sink. Note `_publish_progress` and `_publish_dead_letter` have **no** durable
   fallback today (§C8) — the spec should decide whether that is acceptable (probably yes
   for progress, which is advisory; less obviously for dead-letter, which is already the
   last-resort sink) and **say so**, rather than leaving it undecided.
5. **`ResponseError` is not homogeneous.** `OutOfMemoryError` is transient, `WRONGTYPE` is
   permanent, both are `ResponseError`. Whatever the spec picks, it must state the choice
   and its rationale explicitly.
6. **`WatchError` and `DataError` are `RedisError` but NOT `ResponseError`.** A spec written
   as "also catch `ResponseError`" leaves them escaping. This is the specific reason
   option (ii) needs option (iii) behind it.
7. **`AuthenticationError` is a `ConnectionError`** and is therefore already being retried
   3× today. Not caused by this change, but a spec that reasons about the tuple should not
   assert "only transient errors are retried" — that claim is currently false.
8. **At-least-once delivery is a separate, pre-existing gap (§C10).** BLPOP already destroyed
   the job. The fix makes the *result* durable; it does **not** make the *job* replayable.
   The spec must not imply otherwise, and must not silently expand to a reliable-queue
   redesign.
9. **Three divergent error tuples exist** (`worker.py:1499` publish, `worker.py:1638`
   breaker, `dashboard_api.py:177` dashboard) and the `dashboard_api.py:166-167` docstring's
   "mirrors `_publish_result`" claim is already false. If the spec unifies them, update that
   comment; if it doesn't, don't leave a fourth variant behind.
10. **Do not let the fix flap `/health`.** Wiring publish failures into
   `REDIS_CIRCUIT_THRESHOLD` (§D13) would let a single poison job drive the worker to 503
   and trigger container restarts — the opposite of the containment goal.
11. **Preserve the intentional `break`s** at `worker.py:1699-1707` (session_expired,
    captcha_required) and ensure `CancelledError` still propagates, or graceful shutdown
    regresses silently.
12. **Severity framing.** Per §B this is latent, not live. A spec that opens with "production
    is losing jobs to OOM errors" would be **factually wrong on this deployment** and would
    mis-prioritise the work. The honest framing: *a correctly-identified structural gap with
    no currently-reachable trigger, worth fixing because the blast radius is severe and the
    containment is cheap.*

---

## Unknowns / not verified

- **`.env` `REDIS_PASSWORD`** was not read — the `grep`-the-`.env` command was blocked by the
  permission classifier. Worked around by connecting via the app's own `REDIS_URL` from
  inside the worker container, which produced the same `INFO memory` data (§B6) without
  handling the secret. No fact in this report depends on the blocked command.
- **Whether the local venv's redis version matches the pinned 8.0.0** — not checked; moot,
  since every section-A fact was read from the prod container, which self-reported `8.0.0`,
  matching `requirements.txt:3` (`redis[hiredis]==8.0.0`).
- **`_phase_*` helper internals** were read only at the call-site/structure level, enough to
  establish they execute within `download_process`'s `try` (`worker.py:713`). If any helper
  has its *own* `except Exception` that re-raises from a handler, that would add escape
  sites beyond the three identified in §C8b. Not exhaustively verified.
- **n8n's actual write commands** against `kratos:pje:jobs` — out of repo, not inspected.
  §B7's `WRONGTYPE` conclusion covers only code in this repository; an out-of-repo producer
  writing a non-list to that key would reopen the question. Assessed as low risk (the
  queue contract is RPUSH), but it is an assumption, not a verified fact.
- **Whether the container-level `mem_limit: 128m` vs `--maxmemory 96mb` headroom (§B5) has
  ever triggered a cgroup OOM-kill in prod** — container restart history not inspected.
  Out of scope for this bug (it manifests as `ConnectionError`, already caught), but a
  reasonable follow-up.
