# Research 02 — Import-time `raise` vs clamp for the reply-queue TTL guard

Repo: `/home/fbmoulin/projetos-26-2/pje-download` @ `master` (`05c4bba`).
Scope: `config.py:252-257` guard (`REDIS_RESULT_QUEUE_TTL_SECS <= BATCH_MAX_DURATION_SECS` → `ValueError` at import).
No repo file was modified by this research.

---

## HEADLINE (read this before prioritising the item)

**The guard cannot fire in production today. The crash-loop is a latent, forward-looking
hazard, not a live one.** Three independent facts, each verified:

1. `docker-compose.yml` has **zero** occurrences of `env_file:` (grep: `NO MATCHES`).
2. Neither `REDIS_RESULT_QUEUE_TTL_SECS` nor `BATCH_MAX_DURATION_SECS` appears in the
   `environment:` block of `dashboard`, `worker`, or `redis` (grep over
   `docker-compose.yml`: `NO MATCHES`).
3. `config.load_env()` (`config.py:12-31`) reads `Path(__file__).parent/.env`, i.e.
   `/app/.env` inside the container — but `.dockerignore:7-8` excludes `.env` and
   `.env.*` from the build context, so **no `.env` exists inside either image**.
   (`Dockerfile:22` `COPY *.py dashboard.html ./` for dashboard; `Dockerfile:40`
   `COPY . .` for worker — both filtered by `.dockerignore`.)

Consequence: inside both containers the two values are pinned to code defaults —
`BATCH_MAX_DURATION_SECS = 3600` (`config.py:199`) and
`REDIS_RESULT_QUEUE_TTL_SECS = max(3600+1800, 86400) = 86400` (`config.py:233-243`).
The relation `86400 > 3600` holds unconditionally; the `raise` is unreachable.

This matches premortem Finding 5 (`.premortems/PREMORTEM-2026-07-18T21-20-00Z-addendum.md`),
which reached the same conclusion independently.

**Corollary trap for the spec (this is new, and it bites option (iv)):** the deploy
*rewrites* `/opt/pje-download/.env` wholesale on every run
(`deploy.yml:94-112`, `{ printf ... } > /opt/pje-download/.env`), and that generated file
contains **none** of the four timing vars. `rsync --delete` excludes `.env`
(`deploy.yml:65`), so a hand-edit on the VPS survives the file sync — but is then
clobbered by the very next `Write .env on VPS` step. Any operator tuning of these values
by editing `.env` is therefore **silently non-durable across deploys**, *and* would not
reach the container anyway (no `env_file:`, no var in `environment:`). Two independent
reasons the value never lands.

---

## A. Blast radius

### A1. Restart policies

| Service | `restart:` | Line |
|---|---|---|
| `redis` | `unless-stopped` | `docker-compose.yml:19` |
| `dashboard` | `unless-stopped` | `docker-compose.yml:45` |
| `worker` | `unless-stopped` | `docker-compose.yml:99` |

All three. `worker` additionally sits behind `profiles: [worker]`
(`docker-compose.yml:135-136`), so it only starts with `--profile worker` — which
`deploy.yml:127` does pass.

**Documented Docker behaviour** (verified, not reconstructed):

- `unless-stopped` — docs.docker.com/engine/containers/start-containers-automatically/:
  *"Similar to `always`, except that when the container is stopped (manually or
  otherwise), it isn't restarted even after Docker daemon restarts."* No restart limit is
  specified for `always`/`unless-stopped` (only `on-failure` takes `:max-retries`).
- Backoff — docs.docker.com/reference/cli/docker/container/run/:
  *"An increasing delay (double the previous delay, starting at 100 milliseconds) is
  added before each restart to prevent flooding the server. This means the daemon waits
  for 100 ms, then 200 ms, 400, 800, 1600, and so on until either the `on-failure` limit,
  the maximum delay of 1 minute is hit, or when you `docker stop` or `docker rm -f` the
  container."*
- Reset condition — same page: *"If a container is successfully restarted (the container
  is started and runs for at least 10 seconds), the delay is reset to its default value
  of 100 ms."*

Applied here: an import-time `raise` exits in well under 10 s, so **the backoff never
resets**. The sequence is 0.1s, 0.2, 0.4, 0.8, 1.6, 3.2, 6.4, 12.8, 25.6, 51.2, then
**every 60 s forever**. Roughly 10-11 restart attempts inside the deploy's 120 s health
window, then one per minute indefinitely. Retries are unlimited — a `raise` is a
permanent crash-loop, never a give-up.

Note both containers are affected: `config.py` is imported by `worker.py:60-79` **and**
`dashboard_api.py:67`. A bad value takes down the dashboard and the worker together.

### A2. Deploy sequence (`.github/workflows/deploy.yml`)

1. `deploy.yml:30-32` checkout at the CI-tested SHA.
2. `deploy.yml:34-51` validate secrets (bash `${VAR:?}`, secrets passed via `env:`).
3. `deploy.yml:53-75` `rsync -az --delete` the tree to `/opt/pje-download/`.
4. `deploy.yml:77-112` regenerate `/opt/pje-download/.env` from secrets.
5. `deploy.yml:114-160` `Restart services`, `set -e` (`:125`):
   - `:127` `docker compose --profile worker up -d --build dashboard worker`
   - `:129-137` worker health poll: `for i in $(seq 1 24)` … `sleep 5` → **24 × 5 s = 120 s
     ceiling**, then a bare `grep -Eq` that fails the step if never healthy.
   - `:139-147` dashboard poll: another `seq 1 24` × `sleep 5` → **120 s**, same
     terminating `grep`.
   - `:149-159` Redis queue smoke test (`seq 1 12` × `sleep 5` = 60 s).
6. `deploy.yml:162-177` `Validate MNI credentials` via `docker compose exec dashboard`.

**Does `up -d --build` remove the OLD container before the new one is healthy? Yes.**
Compose's default recreate is stop → remove → create → start; there is no `--wait`, no
rolling update, and `depends_on … condition: service_healthy` (`:81-83`, `:129-131`)
gates only on **redis**, not on the previous revision of the app container. Also note
`container_name:` is pinned (`pje-dashboard`, `pje-worker`), which structurally forbids
running old and new side by side. So at `:127` the old worker/dashboard are already gone;
the health poll is observing the *new* container only.

**What happens if the new container never becomes healthy:** the poll burns 120 s, the
`grep` at `:137` returns non-zero, `set -e` aborts the step, the job fails —
and **production is left with a crash-looping container and no predecessor**. The failure
is visible in GitHub Actions but the outage continues until a human SSHes in.

### A3. Rollback

**None.** `grep -n "rollback\|revert\|previous\|down\b" .github/workflows/deploy.yml` →
`NONE`. There is no `if: failure()` step, no image retention/retag, no `git` checkout of a
prior SHA on the VPS. Recovery from a failed deploy is entirely manual, and `rsync
--delete` has already replaced the source tree on the VPS — so even a manual `docker
compose up -d` re-runs the *bad* code. Real recovery = revert on master → CI → deploy,
i.e. minutes, not seconds.

### A4. Reachability

Answered in the HEADLINE. Precisely:
- `env_file:` in `docker-compose.yml`: **absent** (0 matches).
- `REDIS_RESULT_QUEUE_TTL_SECS` in compose: **absent**. In `.env.example`: **absent**.
- `BATCH_MAX_DURATION_SECS` in compose: **absent**. In `.env.example`: **absent**.
- `.env` inside the image: **absent** (`.dockerignore:7-8`).
- Only consumers: `worker.py:62` imports the TTL; `dashboard_api.py:67` imports the
  ceiling. Disjoint (confirms addendum Finding 3).

**Verification I could not perform (UNKNOWN):** I have no shell on the VPS from here, so I
cannot rule out a hand-edited `docker-compose.yml` on `/opt/pje-download` diverging from
the repo. `rsync --delete` (`deploy.yml:64`) does **not** exclude `docker-compose.yml`, so
any such drift is overwritten on every deploy — which makes divergence very unlikely to
persist, but I am flagging it rather than asserting it.

---

## B. Existing conventions

### B5. Every config-validation pattern in `config.py`

There are **three**, in two distinct idioms:

| # | Location | Pattern | Behaviour |
|---|---|---|---|
| 1 | `config.py:79-84` | **raise at import** | `PJE_BASE_URL` not HTTPS `.jus.br` → `ValueError`. Note the escape hatch: the check is skipped entirely when the value equals the default. |
| 2 | `config.py:252-257` | **raise at import** | the TTL-vs-ceiling guard under discussion. |
| 3 | `config.py:233-243` | **clamp (of the default)** | `REDIS_RESULT_QUEUE_TTL_SECS` default is `max(BATCH_MAX_DURATION_SECS + MARGIN, FLOOR)`. |

Two adjacent derivations are the same clamp idiom without validation:
`REDIS_SOCKET_TIMEOUT_SECS` derived from `max(...) + margin` (`config.py:185-196`) and
`REDIS_MAX_BLOCKING_TIMEOUT_SECS = max(...)` (`:188-190`) — the "raise in lockstep"
pattern documented at `config.py:169-184`.

**This is the single most important observation for the option analysis:** pattern 3 means
**the repo already clamps this exact value — but only on the default path.** `max()` at
`:237-240` silently corrects the derived value upward. The `raise` at `:252` exists solely
to catch the case where an operator sets `REDIS_RESULT_QUEUE_TTL_SECS` *explicitly* and
inverts the relation. So the codebase's own answer to "is this value safely correctable
upward?" is already **yes** — it just applies that answer inconsistently, clamping the
default and aborting on the override.

No `warn`-only or `default-and-log` validation pattern exists anywhere in `config.py`.
There is no logging in `config.py` at all (no `import structlog`, no `import logging`).

### B6. Stated policy in docs / CLAUDE.md

- **No repo-level policy document on config validation exists.** `grep` over `docs/` for
  `env_file|interpolation|fail-fast|config valid` hits only three unrelated files
  (`docs/reports/2026-04-04-audit-final.md`,
  `docs/superpowers/plans/2026-04-04-p0p1-hardening.md`,
  `docs/superpowers/plans/2026-04-18-grafana-dashboard.md`).
- `CLAUDE.md:51` states a related principle for a different subsystem: *"MNI credentials
  are validated before any SOAP call — keep fail-fast check in `download_batch()`"* —
  note that this is fail-fast **at the call site**, not at import.
- `CLAUDE.md:152` documents the deploy's `Validate required secrets` step as the
  fail-fast gate for **secrets**, i.e. the repo's existing answer for must-abort values is
  a *deploy-time* gate, not an import-time raise.
- `TODO.md:21` records the rationale for the guard: the invariant previously lived only in
  tests, and tests import config with defaults, so a deploy could invert it with a green
  suite. `TODO.md:27` records item (b) — crash-loop vs refusal — as **explicitly open,
  pending human decision**.
- **The prior incident about `${VAR:?}` guards in compose is in the user's GLOBAL
  memory, not in this repo.** Recorded there as: *"Env-var guards belong in app Settings,
  not compose `${VAR:?}`"* — because a compose-level guard aborts every `docker compose`
  invocation after a `git pull` (KCP 2026-06-27 incident), and CI dummy-envs mask the
  class. Also relevant and from the same source: *"Compose without `env_file:`: a var in
  the host `.env` that is not in the service's `environment:` NEVER reaches the container
  (interpolation ≠ injection; no warning)."* That second rule is exactly what A4 confirms
  empirically here. I could not find either statement inside this repo — treat them as
  cross-project convention, not repo-local policy.

### B7. Startup preflight

**None exists.** `grep -rn "preflight\|validate_config"` over `*.py` → **0 matches**
(outside `tests/`). Entry points are `dashboard_api.py:1646 main()`,
`worker.py:1890 async def main()`, `batch_downloader.py:771 main()`; none performs config
validation. `tools/verify_spec.py:83` is a Markdown-spec linter, unrelated.

CI (`ci.yml`) does **not** import config with prod-like env: the `test` job sets only
`MNI_USERNAME`/`MNI_PASSWORD`/`MNI_TRIBUNAL`/`REDIS_URL`/`DOWNLOAD_BASE_DIR`
(`ci.yml:46-51`) — the timing vars are unset, so pytest exercises defaults, which is
precisely the blind spot `TODO.md:21` describes.

The only prod-env-touching validation is `deploy.yml:162-177`, which imports
`MNIClient` inside the running dashboard container — a good template for option (iv),
see below.

### B8. What an operator would actually SEE

**A bare traceback, with no structured log line, from both containers.**

- `worker.py`: `from config import (...)` is at `worker.py:60-79`, module scope.
  `structlog.configure(...)` runs at `worker.py:1891-1899`, *inside* `main()`. The import
  raise therefore fires long before logging is configured.
- `dashboard_api.py`: same shape — `from config import (... BATCH_MAX_DURATION_SECS ...)`
  at `:67`, `structlog.configure(...)` at `:1658` inside `main()`.

`docker compose logs worker` would show, repeated once per backoff interval:

```
Traceback (most recent call last):
  File "/app/worker.py", line 60, in <module>
    from config import (
  File "/app/config.py", line 252, in <module>
    raise ValueError(
ValueError: REDIS_RESULT_QUEUE_TTL_SECS (60s) must exceed BATCH_MAX_DURATION_SECS (3600s): ...
```

Credit where due: the message text (`config.py:253-257`) is genuinely good — it names both
vars, both values, and the consequence. The problem is not legibility, it is that nothing
*emits* it anywhere an operator is watching (no structlog, so no JSON, so no log
aggregation / alerting), and that it repeats forever rather than stopping.

Contrast with the app's own idiom for a fatal startup condition:
`worker.py:1920-1922` logs `log.error("pje.main.session_init_failed", action="aborting")`
and `return`s — a structured event and a **clean exit**. The repo already has the pattern
option (iii) wants.

---

## C. Options

First, the decomposition — the five options in the brief conflate **two orthogonal axes**,
and keeping them tangled is how a spec ends up "fixing" the wrong one:

- **Axis 1 — can the value auto-correct?** clamp vs abort. A property of *the value*.
- **Axis 2 — if it must abort, how does it fail?** import-time `raise` under
  `restart: unless-stopped` (crash-loop) vs deploy-time gate or clean runtime exit. A
  property of *the mechanism*.

Options (i) and (iii) are **both abort**, differing only on axis 2. The crash-loop is an
axis-2 defect and is fixable without touching axis 1. Conversely, clamping this one value
does nothing for the next must-abort check someone adds at import.

### (i) Keep the import-time `raise`

- **Prevents:** any process running with a TTL that can expire a live batch's reply queue
  — the silent data-loss mode from addendum Finding 1 (files on disk, batch reported
  failed).
- **Newly risks:** total outage from a typo. With `unless-stopped` + no rollback + no
  side-by-side deploy, a one-digit error takes out dashboard *and* worker with a bare
  traceback and no structured log. Restarts are unlimited (A1) — it never converges.
- **Interaction:** worst possible. Old container removed at `deploy.yml:127`, new one
  crash-looping, health gate burns 120 s and fails, nothing restores the predecessor.
- **Does NOT close** the real invariant: the check runs in one interpreter against one
  process's env. See "the blind spot both raise and clamp share" below.
- **Test:** `tests/test_result_queue_ttl.py:73-86` already covers it via
  `pytest.raises(ValueError)` on a monkeypatched reload. Note what that test *cannot*
  express: two interpreters with different envs.

### (ii) Clamp + `log.error`

- **Prevents:** the same data loss, without an outage. The corrected value is
  unambiguous — `max(configured, BATCH_MAX_DURATION_SECS + MARGIN, FLOOR)` — and it is
  **the identical expression already at `config.py:237-240`** for the default path. This is
  a ~3-line change that makes the override path obey the rule the default path already
  obeys.
- **Newly risks:** operator/system divergence — they set X, the app used Y. Real, but
  bounded here: the divergence is *always upward*, always in the safe direction, and a
  too-long TTL costs only bounded Redis memory (the key still expires; `redis` runs
  `maxmemory 96mb` + `allkeys-lru`, `docker-compose.yml:24-25`). Mitigate by logging at
  `error` with both values and exposing the effective value on `/health`, so "what is the
  container actually using" is answerable without reading source.
- **Interaction:** benign — no crash-loop, container becomes healthy, deploy passes.
- **Blocker to solve in the spec:** `config.py` has **no logger** and is imported before
  `structlog.configure()` runs in both entry points (B8). A `log.error` at import would go
  through an unconfigured structlog. Either record the clamp into a module-level variable
  and emit it from `main()` after configure, or move the clamp into `validate_config()`
  (i.e. combine with (iii)).
- **Test:** reload config with an inverted override, assert the effective value is the
  clamped one *and* that the divergence was recorded; plus a test that no clamp is
  recorded on the happy path.

### (iii) Raise, but from `validate_config()` called by `main()`

- **Prevents:** the same as (i), *and* fixes the operator-experience half: structured
  `log.error` + `sys.exit(1)` instead of a bare import traceback.
- **Newly risks:** **it does not fix the crash-loop.** A non-zero exit from `main()` is
  restarted by `unless-stopped` exactly like an import raise. Docker cannot distinguish
  "deliberate refusal" from "crashed". This is the trap most likely to be mis-specced —
  (iii) alone buys legibility, not availability.
- **Interaction:** unchanged from (i) unless paired with (iv) or with a compose change.
- **Worth doing anyway** as a structural rule: it is the repo's own idiom
  (`worker.py:1920`), and it moves *future* must-abort checks (credentials, endpoints) off
  the import path — including `PJE_BASE_URL` at `config.py:79-84`, which has the identical
  crash-loop shape today.
- **Test:** call `validate_config()` directly with a bad env; assert the log event name and
  the exit code. Much easier to test than an import raise (no `importlib.reload` dance).

### (iv) Validate at deploy time

- **Prevents:** a bad value ever reaching a running container — the only option that
  removes the crash-loop *class* rather than one instance of it. Matches the repo's
  existing convention for must-abort values (`deploy.yml:34-51` secrets,
  `deploy.yml:162-177` MNI credentials).
- **Newly risks — and this is the trap:** a check that runs with the **runner's** env
  proves nothing. The whole failure mode here is *interpolation ≠ injection* (B6, A4): the
  value that matters is what compose injects into the container, which is neither the
  GitHub runner's env nor the VPS `.env`. A `python -c "import config"` in the Actions
  runner would validate a completely unrelated environment and return a confident green.
  The check must run **as the service**, e.g.
  `docker compose run --rm --no-deps worker python -c "import config"` and the same for
  `dashboard` — once **per service**, since their envs differ.
- **Second limit:** a single-interpreter import still cannot see cross-container
  divergence. Running it per service does — but only if the check compares each service's
  view against a shared expectation, not against itself.
- **Interaction:** must run **before** `docker compose up -d --build` at `deploy.yml:127`,
  or the old container is already gone and the gate is pointless.
- **Test:** in CI, run the gate against a deliberately inverted env and assert it fails —
  the "prove the corpus rejects the broken input" discipline. A gate never observed failing
  is not known to work.

### (v) Combination — **RECOMMENDED**

Ordered by value-per-line:

1. **Clamp this value (axis 1).** Make the explicit-override path reuse the `max(...)`
   already at `config.py:237-240`; record the divergence and emit `log.error` from
   `main()` after `structlog.configure()`. Delete the `raise` at `:252-257`. ~3 lines of
   logic. Removes the crash-loop for the only check that can currently produce one from a
   *clampable* value.
2. **Rule for axis 2: no `raise` at import, ever, under `restart: unless-stopped`.**
   Introduce `validate_config()` called from `main()` in both entry points; move the
   `PJE_BASE_URL` check (`config.py:79-84`) there too, since it has the same shape and is
   *not* clampable. Structured log + `sys.exit(1)`.
3. **Fix the deploy's availability hole (independent of all of the above).** Today any
   startup failure — bad config, missing module, bad image — leaves production with
   nothing, because the old container is removed at `:127` and there is no rollback (A3).
   This is worth more than the config item: it is the *general* protection, and
   `Dockerfile:19-21` records that this exact scenario (crash-loop from a `ModuleNotFound`)
   already happened once. Minimum viable: an `if: failure()` step that redeploys the
   previous SHA, or capture `docker compose logs --tail=100` on failure so the operator is
   not blind.
4. **Deploy-time gate**, per service, with the container's env (option (iv) as scoped
   above), placed before `:127`. Cheap, and it is the only mechanism that catches
   must-abort values before they can take anything down.
5. **Cross-container handshake** — see below. Highest correctness value, highest cost;
   defensible to defer.

### The blind spot BOTH `raise` and clamp share (do not let the spec overlook this)

`config.py`'s check — whether it raises or clamps — evaluates **one process's environment
in one interpreter**. But `BATCH_MAX_DURATION_SECS` is read only by the dashboard
(`dashboard_api.py:67`, enforced at `:706`) and `REDIS_RESULT_QUEUE_TTL_SECS` only by the
worker (`worker.py:62`, applied at `:123`). Concretely (addendum Finding 3): set
`BATCH_MAX_DURATION_SECS=14400` on the **dashboard** only; the dashboard derives its own
TTL of 86400 and passes; the worker keeps ceiling 3600 and TTL 86400 and passes; both
containers boot clean; a 3-hour batch outlives nothing it should. **Neither option (i) nor
option (ii) closes this.** Only a runtime handshake does — worker publishes its effective
TTL to a well-known Redis key at startup, dashboard asserts against it at batch start —
or deriving both constants from a single source evaluated in one place. The spec must not
claim clamp is "correct by construction"; it is correct only *within a process*.

`tests/test_result_queue_ttl.py` has the identical defect: it evaluates both constants in
one interpreter (`:37-51`), so cross-container divergence is not representable in it.

### The principle: what is clampable, what must abort

**A config value may be silently corrected when all three hold:**

1. **A correct value is derivable** from other trusted config — not guessed. Here:
   `BATCH_MAX_DURATION_SECS + MARGIN`, already computed at `config.py:238`.
2. **The safe direction is unambiguous.** Here: upward. Longer TTL is always safe; only
   *shorter* loses data. If both directions carried risk, there would be no safe clamp.
3. **The correction cannot violate another invariant.** Here: a longer TTL only consumes
   bounded Redis memory (still expires; LRU-evicted under `maxmemory`).

**A value MUST abort when it encodes external truth the app cannot derive** — credentials,
endpoints, keys, tribunal identifiers. `MNI_PASSWORD` fails all three: there is no
"nearest safe password". `PJE_BASE_URL` likewise — you cannot guess the right tribunal.

Applying it: **TTL-vs-ceiling passes all three → clamp.** MNI credentials, `PJE_BASE_URL`,
`DASHBOARD_API_KEY` → abort, and abort at **deploy time** (option (iv), the repo's existing
convention at `deploy.yml:34-51`) or via `validate_config()`, never at import under
`unless-stopped`.

Restated as one line for the spec: **derive-and-clamp what you can compute; refuse what
you must be told — and refuse it before the old container is gone, not after.**

---

## D. Traps a spec must not get wrong

1. **A clamp is itself a silent divergence.** Operator sets X, app uses Y. Mitigate with
   `log.error` (not `warning`) *and* by exposing the effective value on `/health` — but
   note the log fires at import, before `structlog.configure()` runs in both entry points
   (B8), so a naive `log.error` in `config.py` is unconfigured and may be invisible. This
   is the concrete implementation trap.
2. **A deploy-time check with the wrong env proves nothing.** The failure class *is*
   interpolation-vs-injection (A4). Validating in the GitHub runner, or against
   `/opt/pje-download/.env`, tests an environment no container ever sees. It must run as
   the service, per service, with the compose-injected env.
3. **The old container is gone before the new one is healthy** (`deploy.yml:127`,
   `container_name:` pinned, no `--wait`, no rolling update, redis-only `depends_on`) and
   **there is no rollback** (A3). Any spec that leaves an abort path reachable at container
   startup must first fix this, or it is shipping an outage mechanism.
4. **A guard against silent data loss must not itself cause an outage.** The guard's own
   premise — the failure it prevents is silent and expensive — argues for *loud and
   corrected*, not *dead and repeating*. A crash-loop is not louder than a `log.error`;
   with no structlog it is arguably quieter, since it never reaches log aggregation.
5. **`validate_config()` + `sys.exit(1)` does not fix the crash-loop.** `unless-stopped`
   restarts a clean non-zero exit identically. Legibility ≠ availability.
6. **The invariant is cross-container; no in-process check can express it.** See the blind
   spot above. Do not let a clamp create false confidence.
7. **Operator tuning via `.env` on the VPS is non-durable** — regenerated wholesale at
   `deploy.yml:94-112` — *and* never reaches the container. If the spec intends these vars
   to be operator-tunable at all, plumbing them into `environment:` is a prerequisite, and
   doing so is what *activates* the crash-loop hazard. Sequence matters: fix the failure
   mode before wiring the vars.
8. **Restarts are unlimited.** `unless-stopped` has no `max-retries` (A1). A bad value does
   not eventually give up and leave a stopped container an operator might notice — it
   retries at 60 s forever.
9. **CI cannot catch this class today.** `ci.yml:46-51` sets no timing vars, so pytest
   exercises defaults only — exactly the gap `TODO.md:21` describes. Any new gate must be
   proven to fail against a deliberately inverted env before it is trusted.

---

## Recommendation, in one paragraph

Clamp (option (ii)) for *this* value, delivered as part of the (v) combination: it is
three lines, it reuses the `max()` the default path at `config.py:237-240` already
performs, the correction direction is unambiguously safe, and it removes the only
currently-reachable path from a typo to a two-container outage. Pair it with the axis-2
rule — no `raise` at import under `unless-stopped`, must-abort checks move to
`validate_config()` and/or a per-service deploy-time gate that runs with the container's
env — and with a fix to the deploy's remove-before-healthy hole, which is worth more than
the config item on its own merits. Do not claim the result is correct by construction: the
cross-container invariant (addendum Finding 3) survives every option here and needs a
runtime handshake. And prioritise all of it as **low/deferred**: the guard is unreachable
in production today, and only becomes reachable the moment someone wires these vars into
`environment:` — which is the trigger the spec should attach the work to.
