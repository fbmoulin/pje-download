# SPEC — Build identity in `/health`, asserted at deploy time

**Status:** draft, awaiting user validation
**Author:** sessão 2026-07-27
**Target:** `config.py`, `worker.py` (`_health_handler`), `dashboard_api.py` (`handle_healthz`),
`Dockerfile`, `docker-compose.yml`, `.github/workflows/deploy.yml`
**References:** `TODO.md` § "Aberto agora" item 1; `docs/handoff/2026-07-25-ci-pin-guarda-pii-e-revisao.md`
§ "Próxima ação concreta"; template `scripts/deploy.sh` + `apps/api_gateway/main.py:264` of
pdf-graph (read, not executed).

---

## Goal

Make the deploy prove **which build** is answering, not merely that *something* answers.

Today `deploy.yml` waits for `"healthy": true` on the worker and for a `worker_status` of
`ready|consuming` on the dashboard. Both assertions are satisfied by the **previous** build. If
`docker compose up -d --build` silently reuses a stale image, or the rebuilt image never replaces
the running container, every step stays green and the deploy reports success while the old code
serves traffic. That is the 2026-07-18 pdf-graph failure mode — an image tagged `1.7.6` that
contained `v1.7.3` code, which invalidated three sessions of measurement — and nothing in this
repo would currently detect it.

Verifying a deploy here means SSH-ing to the box, grepping file content and reading container
start times. This spec replaces that with a value readable over HTTP.

## Scope

**In scope**

- A single source for the running build's identity, injected at image build time.
- `build_sha` in the body of the worker's `/health` and the dashboard's `/healthz`.
- The value baked into both image targets.
- `deploy.yml` asserting that the SHA reported by the running worker equals the SHA it deployed.

**Out of scope (deliberately)**

- Publishing images to a registry and deploying by digest. For a single-host app the rsync flow
  defends itself, and adding a registry is a much larger change with its own auth failure modes
  (a silent private-registry auth failure is one of the two mechanisms that produced the
  pdf-graph incident). Revisit only if a second host appears.
- Adding `build_sha` to the authenticated `/api/status` payload. `/healthz` is public, which is
  what makes the assertion checkable over HTTP without shipping the API key to the checker.
- Rotating the deploy key / `DASHBOARD_API_KEY` — separate TODO item, deferred by Felipe.

## Architecture

### Where the value comes from

`BUILD_SHA` is a **Docker build argument** injected by the deploy workflow, promoted to an `ENV`
so the running process can read it. The application reads it through one helper:

```python
# config.py
def build_identity() -> str:
    """SHA of the commit this image was built from; "unknown" outside a CI build."""
    return os.environ.get("BUILD_SHA", "").strip() or "unknown"
```

Read at **call time**, not captured in a module-level constant. Module constants in this repo are
evaluated at import, before `load_env()` has necessarily run, and a constant cannot be exercised
by a test without `importlib.reload`. A function is testable with plain `monkeypatch.setenv`.

**Why an env var and not a file written by the deploy:** a file is a second artifact that can
drift from the image independently — it would be perfectly possible to rsync a fresh `BUILD_SHA`
file next to a stale image, which reproduces the exact lie this change exists to detect. Baking
the value into the image layer makes the identity travel *with* the code it describes.

### Where the `ARG` goes in the Dockerfile — load-bearing

`ARG BUILD_SHA` **invalidates every layer below it.** Placed near the top it would force
`pip install -r requirements.txt` **and** `playwright install chromium` to re-run on every single
deploy, turning a fast deploy into a multi-minute one. It therefore goes at the **end** of each
target, after all `COPY` instructions, where the only layer it invalidates is its own.

`ARG` is also **scoped to a build stage**. This Dockerfile has three (`base`, `dashboard`,
`worker`); declaring it once in `base` does not make it visible in the other two. It is declared
in each target that needs it.

Changing `ENV BUILD_SHA` changes the image, which is what makes `docker compose up -d` actually
recreate the container — a useful side effect, not an accident.

### What the assertion does and does not prove

Because the image is built **on the production host from an rsynced tree**, `build_sha` proves
*"this image was built from the tree that the workflow labelled X"*. It does not prove the tree is
byte-for-byte commit X — a hand-edit on the box between rsync and build would not be caught.

That limit is acceptable because it is not the failure mode being defended against. The mechanism
that actually bites is **the image not being rebuilt or the container not being replaced**, and
against that the assertion is exact: the old image carries the old SHA, or none at all.

`"unknown"` is treated as a **failure**, not as a pass. A missing value means the build arg was not
threaded through, which is indistinguishable from the stale-image case at assertion time.

## Task decomposition

| Task | Files | Independently testable |
|---|---|---|
| T1 — helper + failing tests (RED) | `config.py`, `tests/test_build_identity.py` | yes — tests fail before T2 |
| T2 — expose in both endpoints (GREEN) | `worker.py`, `dashboard_api.py` | yes — same tests pass |
| T3 — bake into the image | `Dockerfile`, `docker-compose.yml` | yes — `docker build --build-arg` |
| T4 — deploy writes + asserts | `.github/workflows/deploy.yml` | only end-to-end, on a real deploy |
| T5 — docs + PR | `TODO.md`, `CLAUDE.md`, `CHANGELOG.md` | n/a |

## Dependencies

- T2 depends on T1 (helper must exist).
- T4 depends on T3 (nothing to assert until the value is baked in).
- T4 cannot be verified without a real deploy — see Risks.

## Risks and plan B

| Risk | Mitigation |
|---|---|
| `ARG` placement forces a full rebuild each deploy | `ARG`/`ENV` last in each target; verify by timing a second local build |
| The new assertion turns a working deploy red on first run, because the currently running image predates the change | The assertion runs **after** `up -d --build` in the same job, so the image being asserted is the one this deploy just built. There is no "previous image" window. |
| `ARG` declared only in `base`, invisible in the targets | Declared per target; a local `docker build --build-arg` on each target proves it |
| Merging is itself a production deploy | PR is code-touching → tier 🔴, needs explicit authorization before merge |

**Plan B** if the assertion proves flaky in practice: keep `build_sha` in the payload (it is useful
on its own for any manual check) and downgrade the deploy step to a warning. Do **not** delete the
field — the value is what makes a measurement citable afterwards.

## Test strategy — TDD

RED first, in `tests/test_build_identity.py`:

1. `config.build_identity()` returns `"unknown"` when `BUILD_SHA` is unset or blank.
2. `config.build_identity()` returns the value when set.
3. The worker's `/health` body contains `build_sha` equal to the env value.
4. The dashboard's `/healthz` body contains `build_sha` equal to the env value.

Tests 3 and 4 assert on the response **body**, not on the helper — a test that only exercises the
helper would pass even if neither endpoint were wired up, which is the failure this change exists
to prevent. The endpoint tests are the ones that carry the contract.

Full suite must run **against a live redis** (`docker run -d --rm -p 6379:6379 redis:7.4-alpine`),
otherwise `test_redis_socket_timeout.py` and `test_result_queue_ttl.py` skip in silence and a
green run proves nothing about the redis pin.

## Acceptance criteria

- [ ] `curl :8006/health | jq .build_sha` returns the deployed commit SHA on the running worker.
- [ ] `curl :8007/healthz | jq .build_sha` does the same for the dashboard.
- [ ] Both return `"unknown"` when built without the arg, and the deploy **fails** on `"unknown"`.
- [ ] `deploy.yml` fails when the reported SHA differs from the SHA it deployed.
- [ ] A second consecutive build does not re-run `pip install` or `playwright install`.
- [ ] Full suite green with a reachable redis, 0 skipped.
- [ ] `ruff check .` and `ruff format --check .` clean under **0.14.14**, the version CI pins.

## USER VALIDATION GATE

Merging this PR **is a production deploy** — the repo deploys `master` automatically via
`workflow_run`. Felipe authorizes the merge; this session does not merge on its own.

The first deploy after merge is also the first execution of the new assertion. If it fails, read
the reported SHA before assuming the assertion is wrong: a mismatch on the very first run is
evidence the mechanism works.

## Skills

`writing-plans` for the plan, `subagent-driven-development` not used (no real parallelism — the
tasks are sequentially dependent), `plan-quality-gate` via the acceptance criteria above, TDD as
described. Commits are frequent and atomic, one per task.
