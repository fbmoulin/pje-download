# pje-download — CLAUDE.md

## Scope — TJES only (decided 2026-10-03)

The owner's focus is **TJES** (`MNI_TRIBUNAL=TJES`, the production deployment). The code still supports `TJES_2G`, `TJBA`, `TJBA_2G`, `TJCE` and `TRT17`, but they are **not a work target**: do not audit, measure, test against, expand or "complete" them, and do not list them as pending items.

- **Why:** the 2026-09/10 sessions drifted into multi-tribunal work (the 6-tribunal SSRF expansion, per-tribunal WSDL re-checks) and the owner called it out: "o meu foco por enquanto e apenas no tjes. entao ja fizemos mais do que deveriamos".
- **How to apply:** before proposing or starting a task, ask whether it changes the TJES path. If it only affects another tribunal, skip it and mention it in at most one line. Generic fixes that happen to live in shared code (MNI client, worker, dashboard) are fine — the test is the TJES path, not the file.
- **Already in the code, harmless for TJES:** `MNI_FORBID_EXTERNAL_TRIBUNALS` defaults to all six tribunals (#54) and only acts when `MNI_TRIBUNAL` is one of them. To go back to the TJES-only setting without a code change, set `MNI_FORBID_EXTERNAL_TRIBUNALS=TJES` in the environment.

## Commands

```bash
# Desenvolvimento local
python dashboard_api.py --port 8007 --output ./downloads
python worker.py  # requer Redis

# Docker (recomendado)
docker compose up -d                    # dashboard + redis
docker compose --profile worker up -d  # + pje worker

# Testes e lint
pytest tests/ -q
ruff check .          # CI lints the whole repo (c72b4b4), not a file allowlist
ruff format --check .
# ⚠️ CI pins ruff==0.14.14 (ci.yml, PR #39). Lint locally with that exact version or you will
# disagree with CI in both directions. Unpinned, 0.16.0 reports 208 errors on this tree.

# Spec verifier (SDD) — CI gate, also runnable locally (ci.yml step "Verify Markdown specs")
python tools/verify_spec.py docs/specs/*.md

# Gitleaks infra rules — verifies BOTH directions (4 must fire, 8 must not).
# Run it after ANY edit to .gitleaks.toml. Not in CI yet (the runner has no
# gitleaks), so nothing else exercises these rules.
bash tools/verify_gitleaks_rules.sh
```

## Environment (mínimo)

```bash
export MNI_USERNAME="12345678900"  # CPF sem pontos
export MNI_PASSWORD="senha"
export MNI_TRIBUNAL="TJES"        # TJES | TJES_2G | TJBA | TJBA_2G | TJCE | TRT17
export AUDIT_LOG_DIR="/data/audit" # CNJ 615/2025 audit trail (default: /data/audit)
# .env buscado automaticamente em: kratos-master/config/.env → ./ (via config.load_env())
```

## Stack
- Runtime: Python 3.12, aiohttp (not FastAPI), zeep (SOAP), structlog, asyncio
- SOAP calls: always via `asyncio.to_thread` — zeep is synchronous
- Test suite: pytest — **683 tests** (measured 2026-10-01: 681 pass + 2 Redis-socket tests that need a live Redis) — run with `pytest tests/ -q` before any commit
  - ⚠️ **Without a reachable redis you get "681 passed, 2 skipped", and the 2 skips are silent.**
    They are `tests/test_redis_socket_timeout.py` and `tests/test_result_queue_ttl.py` — the only
    real-socket tests, and precisely the ones that matter when bumping `redis[hiredis]`. CI
    publishes redis on 6379 deliberately so they run. Locally: `docker run -d --rm -p 6379:6379
    redis:7.4-alpine` first, or treat a green run as proving nothing about the redis pin.
- Audit sink: asyncpg → Railway Postgres (optional, opt-in via `AUDIT_SYNC_ENABLED`). **Requires PG 15+** — syncer self-disables on older versions (see Sprint 12 B5)
- Shared helpers: `file_utils.py` (`total_bytes`, `merge_file_lists`), `async_retry.py` (`AsyncRetry` class for exponential backoff)

## Env Loading (critical gotcha)
- `config.py` constants are module-level — they may be empty strings if `.env` not yet loaded
- Always call `_load_env()` before constructing `MNIClient()` or reading MNI credentials
- Lazy imports inside functions (e.g. `batch_downloader.py`) are intentional — preserve them

## Linting
- E402 (module-level import not at top) is intentional in `dashboard_api.py` and `mni_client.py` — do not fix

## Security (do not weaken)
- CORS is restricted to localhost-only (`_ALLOWED_ORIGINS`) — do not revert to `"*"`
- Rate limiter tracks last-seen per IP to prevent memory leaks — keep `_rate_bucket_last_seen`
- MNI credentials are validated before any SOAP call — keep fail-fast check in `download_batch()`
- `deploy.yml` runs from `workflow_run` in the base repo **with production secrets**, so its `if:` must only ever deploy a commit of THIS repo: it requires `workflow_run.event` to be `push`/`workflow_dispatch` and `head_repository.full_name == github.repository`. `head_branch == 'master'` alone is not enough — a fork PR from the fork's own `master` passes it. Pinned by `tests/test_deploy_workflow_guard.py` (deploy.yml is not exercised by any PR). Also set required reviewers on the `production` GitHub environment — that setting is not visible from the repo.

## Metrics (metrics.py)
- All Prometheus metrics use a dedicated `REGISTRY = CollectorRegistry()` — NOT the default global
- This prevents `ValueError: Duplicated timeseries` when the module is re-imported across tests
- `/metrics` endpoint in `dashboard_api.py` uses `headers={"Content-Type": CONTENT_TYPE_LATEST}` — do NOT use `content_type=` kwarg (aiohttp rejects charset embedded in that kwarg)
- To add instrumentation: `import metrics` at module top level (metrics.py has no env-var deps)
- Pattern: `t0 = time.monotonic()` before call, `metrics.X.observe(time.monotonic() - t0)` at every return path

## Test Patterns (critical)
- **Lazy import mocking**: MNIClient and download_gdrive_folder are imported lazily inside functions
  — patch at source module: `patch("mni_client.MNIClient")`, NOT `patch("batch_downloader.MNIClient")`
- **worker.py import side effect**: `DOWNLOAD_BASE_DIR.mkdir()` runs on import
  — set `DOWNLOAD_BASE_DIR=/tmp/pje-test-downloads` in conftest BEFORE importing worker
- **env var propagation in worker tests**: use `importlib.reload(w)` after `monkeypatch.setenv`
- **aiohttp test client**: `async with TestClient(TestServer(create_app(tmp_path))) as client:`
- **A test that evicts modules from `sys.modules` must restore the originals.** Tests that imported `dashboard_api`/`metrics` at collection time keep the OLD objects while `patch("dashboard_api.X")` and lazy imports resolve the NEW ones. Fixed in `test_image_dependency_pins.py` (2026-10-01): left unrestored, a dashboard test that takes 1.5 s alone spun until killed when run after it; alphabetical order was the only thing hiding it. Check with `pytest tests/test_image_dependency_pins.py tests/` (pins file first).
- **A full disk must fail the job, and only a full disk.** `_save_document` raises `file_utils.DiskWriteError` (an `OSError` subclass); `download_documentos` phase 2 and `worker._try_mni_download` re-raise it so `download_process` fails the job with the real message instead of trying the API/browser fallbacks on the same disk. Do NOT broaden those `except` clauses to `OSError`: `requests.ConnectionError`/`Timeout` subclass it, and a network blip on one batch is meant to be survived (`test_network_oserror_in_one_batch_does_not_stop_the_next`).
- **zeep `Transport(timeout=...)` is only the WSDL *load* timeout.** SOAP POSTs use `operation_timeout` (default `None` = no socket timeout); `mni_client._get_client` sets both. `asyncio.wait_for` around `to_thread` cancels only the awaiter, never the thread.

## Completed Sprints

Sprint 1 — P0/P1 Hardening (2026-04-04):
- Scope: 12 bug fixes (3 CRITICAL, 9 HIGH) + 28 new tests
- Status: DONE — 73→101 tests

Sprint 2+3 — Security + Resilience (2026-04-04):
- Scope: 5 CRITICAL + 15 HIGH. API key auth, session lock, path traversal, DRY, eviction
- Status: DONE — 101→111 tests

Sprint 4 — Test Coverage Expansion (2026-04-04):
- Plan: `docs/superpowers/plans/2026-04-04-sprint4-test-expansion.md`
- Scope: +72 tests across 5 test files. Pure functions, middleware, handlers, SOAP parser, eviction
- Status: DONE — 111→183 tests, ~68% symbol coverage

Sprint 5 — CNJ 615/2025 Audit Trail + HARD Test Coverage (2026-04-04):
- Spec: `docs/superpowers/specs/2026-04-04-sprint5-audit-trail-hard-tests.md`
- Scope: New audit.py module (JSON-L append-only), 8 instrumentation points, +65 tests (SOAP mocks, Playwright smoke, GDrive)
- Status: DONE — 183→248 tests, ~85% symbol coverage

Sprint 6 — Graceful Shutdown + Redis Retry (2026-04-04):
- Scope: Signal handlers (SIGTERM/SIGINT), Redis init retry (5x backoff), blpop exponential backoff, lpush retry with local fallback, dashboard progress save on shutdown
- Status: DONE — 248→257 tests

Sprint 7 — Audit Sync to Railway Postgres (2026-04-17, merge 6612135 #3):
- Scope: `audit_sync.py` background syncer (tails JSON-L, inserts to Postgres, idempotent dedupe via composite UNIQUE NULLS NOT DISTINCT), `migrations/001_audit_entries.sql`, dashboard lifecycle hooks, Dockerfile fix (faltava redis + asyncpg). Validado end-to-end contra Railway 18.3 real.
- Status: DONE — 303→348 tests

Sprint 8 — P0 Audit: auth on GET + torn-read logging (2026-04-17, merge dd27556 #6):
- Scope: `api_key_middleware` agora exige X-API-Key em todo `/api/*` (só POST antes; vazava CNJs). `dashboard.progress.read_failed` log estruturado nos `except` antes silenciosos. Removido `_test_dashboard_import.py` morto.
- Status: DONE — 348→353 tests

Sprint 9 — P1 Audit: pool lifetime + rpush retry + log hygiene (2026-04-17, merge de4d57f #7):
- Scope: `asyncpg.create_pool(max_inactive_connection_lifetime=30.0, max_size=1)` contra restart do Railway. `_rpush_with_retry` com 3 tentativas + backoff exponencial. `_try_official_api` com `except` isolado para `json()` que NUNCA loga `str(exc)` (vazava Set-Cookie).
- Status: DONE — 353→359 tests

Sprint 10 — P0.2 Audit: browser fallback characterization (2026-04-17, merge 3f9dde7 #8):
- Scope: 12 testes cobrindo `_download_via_browser`, `_try_full_download_button` e `extract_gdrive_link_from_pje` (eram 0% covered). Helpers `_fake_locator` e `_page_stub_with` para stubar Playwright Page boundary. Zero código de produção alterado.
- Status: DONE — 359→371 tests

Sprint 11 — P2 Audit: circuit breaker + PJe retry + cursor cleanup (2026-04-17, merge 574c1fb #9):
- Scope: blpop circuit breaker (`REDIS_CIRCUIT_THRESHOLD=20` → `_health_status="redis_unreachable"` → `/health` 503). `_try_official_api` com `timeout=10_000ms` + 3 tentativas em 5xx/exception (backoff cap 5s). `rotate_logs` limpa sidecars `.cursor` junto com `.jsonl`.
- Status: DONE — 371→377 tests
- Falsos-positivos descartados: indexes em audit_entries (migration 001 já cria), pipelining hset (zero chamadas no código).

Sprint 12 — 5 Production Bugs (2026-04-18, PR #12):
- Plan: `docs/superpowers/plans/2026-04-18-audit-remediation.md#sprint-1`
- Scope: 5 bugs surfaced by 3-lens audit (code-quality + adversarial + architecture agents).
  - **B1** `batch_downloader.py:518` — Playwright success path missing `progress.save(force=True)` before early return (crash-resume re-downloaded completed processos)
  - **B2** `audit_sync.py:149, 423` — datetime mix naive/aware silently froze lag gauge (now `_coerce_utc` helper)
  - **B3** `batch_downloader.py:459,511,557,659` — `sum(f["tamanhoBytes"] ...)` KeyError marked successful downloads as failed (now `.get()` with defensive defaults)
  - **B4** `audit_sync.py:347` — `audit_sync_rows_total{success}` incremented inside `_insert_batch` before `_save_cursor`, overcounting on crash-recovery (moved to `_sync_file` post-cursor-save)
  - **B5** `audit_sync.py _verify_pg_version` — PG<15 silently ignored `UNIQUE NULLS NOT DISTINCT`, producing duplicate NULL-keyed audit rows. Syncer now self-disables with ERROR log on old PG.
- Status: DONE — 377→388 tests (+11 targeted regression tests)

Sprint 13 — DRY Helpers + Config Constants (2026-04-18, PR #13, stacked on #12):
- Plan: `docs/superpowers/plans/2026-04-18-audit-remediation.md#sprint-2`
- Scope (zero behavior change, pure refactor):
  - **Q1** Extract `file_utils.total_bytes` helper — replaces 17 copies of `sum(int(item.get("tamanhoBytes", 0) or 0) for item in X)` across worker/dashboard/batch_downloader
  - **Q2** Dedupe `_merge_downloaded_files` — was verbatim copy in worker.py and batch_downloader.py; now `file_utils.merge_file_lists` with compat aliases
  - **Q3** Extract `dashboard_api._safe_load_json` helper — consolidates 3 repeated JSON-load-with-except patterns in `_load_history` / `_load_active_batch`
  - **Q4** Move 7 magic numbers to `config.py` constants: `PLAYWRIGHT_FULL_DOWNLOAD_TIMEOUT_MS` (300_000), `PLAYWRIGHT_INDIVIDUAL_DOWNLOAD_TIMEOUT_MS` (30_000), `REDIS_BLPOP_TIMEOUT_SECS` (5), `REDIS_CIRCUIT_THRESHOLD` (20), `MNI_HEALTH_CACHE_TTL_SECS` (30), `RESULT_WAIT_TIMEOUT_SECS` (360), `RESULT_POLL_BLPOP_TIMEOUT_SECS` (5) — all env-configurable
- Gotcha: Python function-scope shadow bug surfaced in `batch_downloader.download_batch` (local `total_bytes` shadowed imported helper); renamed local to `batch_total_bytes` with defensive comment
- Status: DONE — test count unchanged (388)

Sprint 14 — _run_batch split + AsyncRetry helper (2026-04-18, PR #14, stacked on #13):
- Plan: `docs/superpowers/plans/2026-04-18-audit-remediation.md#sprint-3a`
- Scope:
  - **R3** `async_retry.AsyncRetry` class — consolidates 2 of 3 hand-rolled exponential-backoff loops (worker Redis init + `dashboard._rpush_with_retry`). Third site (`worker._try_official_api`) intentionally kept (retries on HTTP 5xx, not exceptions; returns None on exhaustion). +10 unit tests.
  - **R2** Split `dashboard_api.DashboardState._run_batch` (170L → 30L orchestrator + 3 phase methods): `_enqueue_batch` (publish + build state), `_poll_results_loop` (drain reply queue), `_finalize_batch` (status ladder + metrics). New `BatchPollState` dataclass. New `_FATAL_WORKER_STATUSES = frozenset({"session_expired", "captcha_required"})`. Preserved order of all side effects + metric increments.
- Status: DONE — 388→398 tests

Sprint 15 — A1 typed protocol + A2 AppContext (2026-05-01, PR #20, tagged v2.5.0):
- Plan: `docs/superpowers/plans/2026-04-18-audit-remediation.md#sprint-4` (originally deferred — lifted when test-isolation drift surfaced via broken WIP)
- Scope:
  - **A1** New `protocol.py` (122L) with `JobMessage` / `ResultMessage` / `ProgressMessage` / `DeadLetterEntry` TypedDicts + `from_json`/`to_json` helpers + `job_from_json` validation (rejects non-dict, missing `jobId`/`numeroProcesso`). `worker._publish_result` and `_publish_dead_letter` migrated. Wire format byte-identical to v2.4 — old workers/dashboards interop. Remaining producer site `worker._try_official_api` deferred to follow-up PR.
  - **A2** 7 module-level mutable globals in `dashboard_api.py` (`state`, `_batch_lock`, `_login_running`, `_login_task`, `_login_last_ok`, `_rate_buckets`, `_rate_bucket_last_seen`) collapsed into `AppContext` dataclass at `app[APP_CTX_KEY]`. Handlers + middlewares read via `request.app[APP_CTX_KEY]`. Eliminates test-isolation drift between invocations.
  - **chore** Untracked accidental `.coverage` 53KB blob, gitignored `.coverage` + `.pytest_cache/`.
- Closure commit: `d90fb26` (CLAUDE.md backlog #4 marked DONE, this Sprint 15 entry, README test count + protocol.py row).
- Status: DONE — 416→424 tests, ruff clean, 4/4 CI checks green. Approved by `coderabbit:code-reviewer` agent (zero blocking, 5 non-blocking suggestions tracked as backlog item 4 follow-ups). Tag `v2.5.0` annotated.

Post-v2.5.0 — Deploy Verifier + Spec Verifier (SDD) (2026-06-26 → 2026-07, HEAD `9d25778`):
- Plan: `docs/plans/2026-06-26-vps-deploy-verifier-sdd.md`; canonical spec: `docs/specs/sdd-pje-download.md`
- **Phase 1 — generic-VPS deploy hardening** (`deploy.yml`):
  - Task 1.1 — fail-fast `Validate required secrets` step: aborts the deploy up front if any of `VPS_SSH_KEY`/`VPS_HOST`/`VPS_USER`/`MNI_USERNAME`/`MNI_PASSWORD`/`REDIS_PASSWORD`/`DASHBOARD_API_KEY` is missing (`: "${{ secrets.X:?... }}"`). Deploy is now parameterized by `VPS_HOST` secret — no hardcoded host.
  - Task 1.2 — removed the dead VPS IP `191.252.204.250` (timing out on `/healthz` + `/api/status`) from docs.
  - Task 1.3 — post-deploy MNI credential smoke test.
- **Phase 2 — Spec Verifier promoted to a real feature** (`tools/verify_spec.py`):
  - Canonical verifier for SDD Markdown specs. 11 structural checks (min length, USER VALIDATION GATE, writing-plans / subagent-driven-development / plan-quality-gate skills, table, References, Goal, TDD, `### Task`, frequent-commits). Exit 0 pass / 1 fail / 2 usage.
  - Tasks 2.1+2.2 — unit tests (`tests/test_verify_spec.py`) + wired into CI as the `Verify Markdown specs` step in `ci.yml` (`python tools/verify_spec.py docs/specs/*.md`). Task 2.3 — added the 4 quality checks (Goal / TDD / Task / frequent-commits).
- Interstitial: dep bumps (aiohttp 3.14.1, python-deps group #24), added coverage (file_utils contract, concurrent rate-limit, worker shutdown-on-session-expiry), repo-wide `ruff check .`/`format .`, manual `workflow_dispatch` on CI.
- Status: DONE — 424→441 tests. `v2.5.0` remains the latest tag (post-v2.5.0 work not yet tagged).

## Security

- `DASHBOARD_API_KEY` env var required for POST endpoints in production (empty = dev mode, no auth)
- Session file written with 0600 permissions — on BOTH write paths: `pje_session.interactive_login` and `worker.load_session` (`_ensure_owner_only_file` creates it 0600 *before* Playwright's `storage_state(path=...)` writes, which would otherwise use the umask → 0644)
- `PJE_BASE_URL` validated by `config.validate_pje_base_url`: HTTPS and the parsed **hostname** must end in `.jus.br`. Never go back to `".jus.br" in url` — it accepts `https://evil.com/?x=.jus.br` and `https://pje.jus.br.evil.com`
- `config.CNJ_PATTERN` is `re.ASCII`: without it `\d` matches Arabic-Indic/fullwidth digits
- `static/js/app.js` `esc()` escapes `& < > " '` (it is used inside attributes). Never build an inline `on…="fn('${x}')"` handler from data — escaping cannot make that safe; use `data-*` + a delegated listener. Pinned by `tests/test_dashboard_js_escaping.py` (runs the real functions under `node`; skipped if node is absent).
- Rate limiter parses `X-Forwarded-For` for real client IP behind proxy
- Worker health bound to 127.0.0.1 (not exposed externally)
- CNJ 615/2025 audit trail: `audit.py` logs every document access to JSON-L (`/data/audit/audit-YYYY-MM-DD.jsonl`, 0600 perms, append-only)

## Prompt gate (Claude Code hooks)

`.claude/settings.json` registers `tools/prompt_gate.py` as a `UserPromptSubmit` hook and as a
`Stop` hook, and denies `Read` on `downloads/`, `downloads_batch/` and `/data/`. Spec and
evidence: `docs/specs/2026-10-03-prompt-quality-gate.md`; premortems in `.premortems/`.

- **What it does:** a typed prompt with a valid CPF/CNPJ (bare or punctuated) is blocked
  before the main model runs — no exemption, not even `!!`. On the first evaluated prompt of a
  session it *warns* (never blocks, unless promoted) when the prompt has no target file or
  reproducible symptom, no definition of done, or 3+ stacked tasks. At Stop it runs ruff
  (`uvx ruff@0.14.14`) on the `.py` files changed **during the turn** and pytest on
  `tests/test_<mod>*.py`; lint or test failures send Claude back to fix them.
- ⚠️ **It is a safety net, not a sanitizer. Do not type PII into prompts.** A blocked prompt
  still reaches Haiku for the session title and is written verbatim to the local transcript.
  Party names, RG, addresses and case excerpts are not detected. A hook timeout or a crashed
  interpreter fails open.
- ⚠️ **Open Claude Code at the repo root.** A session started in a subdirectory (`cd tests &&
  claude`) does not load the project `.claude/settings.json` — no PII gate, no Stop hook, no
  deny (verified with CLI 2.1.288 in `-p` mode; re-check interactively on WSL).
- ⚠️ **Review fork PRs with hooks off.** The hooks run the checked-out `tools/prompt_gate.py`
  and the tests on every prompt/Stop, and `config.py` loads `.env` on import. Use
  `"disableAllHooks": true` in `.claude/settings.local.json` (not committed) while reviewing.
- **Escape hatches:** start a prompt with `!!` to skip the quality warnings (PII still
  blocks). Kill switch: exit and relaunch with `PROMPT_GATE_DISABLE=1 claude --continue`.
  Never put `PROMPT_GATE_DISABLE` in the committed settings — a test enforces that.
- **Local knobs** (set them in `.claude/settings.local.json` → `env`; the settings `env`
  overrides the shell): `PROMPT_GATE_PYTHON` = interpreter with the project deps for the Stop
  pytest (the hook's `python3` usually lacks them and then only warns);
  `PROMPT_GATE_RUFF` = ruff command; `PROMPT_GATE_BLOCK_RULES=possui_dod,...` = promote a
  quality rule to blocking (only after the dated D2 review, spec Task 9).
- **Labeling warnings** (feeds the D2 review): each warning shows `gate [<id>]`. Run
  `python3 tools/prompt_gate.py --label <id> fp|tp`, and `--report` for per-rule counts and the
  unlabeled ids with their `session_id`/`prompt_id`. Telemetry lives in
  `${XDG_STATE_HOME:-~/.local/state}/pje-prompt-gate/` — never the prompt text, never a hash.
- **Tests that hold PII-shaped values build them at runtime** (`tests/test_prompt_gate.py`
  helpers). `.gitleaks.toml` also flags formatted CNJ numbers and formatted CPFs regardless of
  check digit, so never commit such literals, not even invalid ones.

## Audit Sync (Railway Postgres, Phase 2)

Local JSON-L remains the **source of truth**; Railway is a write-only redundant sink.
Default disabled (`AUDIT_SYNC_ENABLED=false`).

**Required for production:**
- Use an **append-only** Postgres role, not the admin role. One-time setup:
  ```sql
  CREATE ROLE audit_writer LOGIN PASSWORD '...';
  GRANT CONNECT ON DATABASE railway TO audit_writer;
  GRANT USAGE ON SCHEMA public TO audit_writer;
  GRANT INSERT, SELECT ON audit_entries TO audit_writer;
  GRANT USAGE, SELECT ON SEQUENCE audit_entries_id_seq TO audit_writer;
  ```
  `SELECT` is required by Postgres for the arbiter-index lookup in `INSERT ... ON CONFLICT (cols) DO NOTHING`. The role is still effectively append-only — no UPDATE, DELETE, or TRUNCATE.
- TLS: `sslmode=require` is enough for managed providers (Railway, Neon, Supabase). Only set `sslmode=verify-full` when the server has a CA-signed cert; the syncer respects the URL's `sslmode` and will only build a strict `SSLContext` for `verify-full`/`verify-ca`.
- Never log `DATABASE_URL` — always pass through `audit_sync._scrub_url()` first. Covered by `test_no_password_in_logs_during_lifecycle`.

**Bootstrap sequence:**
1. Provision Railway Postgres, copy `DATABASE_URL`.
2. Set `AUDIT_SYNC_AUTO_MIGRATE=true` + admin URL, restart dashboard → schema created.
3. Flip `AUDIT_SYNC_AUTO_MIGRATE=false`, rotate `DATABASE_URL` to the `audit_writer` role, restart.

**Correctness invariant:** a JSON-L line is complete only when it ends with `\n`. `_parse_complete_lines` stops at any partial tail so the cursor never advances past in-flight writes. Never violate this rule — tests in `tests/test_audit_sync.py::TestParseCompleteLines`.

**Idempotency:** inserts use `ON CONFLICT (ts, event_type, processo_numero, documento_id) DO NOTHING` against a composite `UNIQUE NULLS NOT DISTINCT` constraint (PG 15+). Replaying a tick is a no-op. (The earlier design of a generated dedupe column had a PG immutability issue — see `docs/superpowers/specs/` for the story.)

**Railway project:** `pje-audit` (id `3c561ec2-27d4-4278-aa49-9f7187a49e2b`, host `nozomi.proxy.rlwy.net:27048/railway`). Migration 001+002 already applied. Admin URL + `audit_writer` URL in local `.env` (not committed).

## Known Issues (remaining)

- ~~MNI blocked by cloud IP~~ — **RESOLVED 2026-07-18, entry was stale.** It was never a
  whitelist/ofício problem: PJe/TJES sits behind AWS CloudFront with country geo-restriction, so
  a non-BR IP gets `403` (POP `BOS50`) and a BR IP gets `200` (POP `GRU3`). Fixed by moving the
  VPS to the São Paulo datacenter. See backlog item 1's follow-up below for the full diagnosis —
  this section contradicted it for a week. Re-confirmed 2026-07-25: production `/health` reports
  `mni: healthy`, and the live TJES WSDL fetches with HTTP 200 from a BR IP.
- Test coverage ~85% — remaining ~15% are deep Playwright integration paths (low ROI)
- ~~**`/health` carries no build identity**~~ — **FIXED 2026-07-27** (implemented; the PR is not
  merged yet — merging is a production deploy). `BUILD_SHA` build arg → `ENV` →
  `config.build_identity()` → `build_sha` in the worker's `/health` and the dashboard's
  `/healthz`; `deploy.yml` asserts both against the commit it deployed and fails on a mismatch,
  on `"unknown"`, or on an absent value. Read it with
  `curl -s localhost:8006/health | jq -r .build_sha` (worker) or
  `curl -s localhost:8007/healthz | jq -r .build_sha` (dashboard, public — no API key needed).
  - ⚠️ **Do NOT move `ARG BUILD_SHA` out of the tail of each Dockerfile target.** An `ARG`
    invalidates every layer below it; near the top, every deploy re-runs `pip install` and
    `playwright install chromium`. Measured 2026-07-27: with the ARG last, a rebuild with a
    *different* SHA completes in **0.6 s** with `pip install` served from cache, and the reported
    value still changes. `ARG` is stage-scoped — each target declares its own.
  - ⚠️ **What it does not prove:** the image is built on the production host from an rsynced
    tree, so `build_sha` means "built from the tree the workflow labelled X", not "the tree is
    byte-for-byte commit X". It catches the real failure mode (image not rebuilt, container not
    replaced), not a hand-edit on the box.
  - Deploying by registry digest stays deliberately out of scope for a single-host app.

## Backlog — Phase 1 Complete (Items 1–6 DONE)

**As of 2026-09-29:** All Phase 1 backlog items (1–6) are complete and merged, and Phase 2 T2.1 and T2.2A are merged and deployed (#54, #53). Remaining Phase 2 items are below.

**Test suite status:** 681 passed, 2 skipped without Redis (no failures).

### Phase 1 Completed Items (2026-04-04 → 2026-09-27)

1. ~~**Deploy prod**~~ — ✅ **DONE 2026-07-18 — app NO AR num Hostinger VPS, MNI FUNCIONANDO** (run verde `29647851676`, 5/5). Host **`pje-vps`** (alias SSH em `~/.ssh/config`; valor real em GitHub Secrets `VPS_HOST`) (VM Hostinger `<id no painel>`, KVM 2, **datacenter São Paulo id 14**; o IP antigo `2.24.126.161` era Boston/dc 24 e está morto). 3 containers `Up (healthy)`: dashboard :8007, worker :8006, redis. **`mni check: healthy`** ✅ (ver resolução do geo-bloqueio no follow-up abaixo). Dashboard acessível só por **túnel SSH** (`ssh -L 8007:localhost:8007 pje-vps`), 8007 fechado de fora pelo firewall. **`AUDIT_SYNC_ENABLED` segue off** (sink Railway opcional; audit JSON-L local funciona no volume).
   - **⚠️ CORREÇÃO DO DIAGNÓSTICO ANTERIOR (estava ERRADO):** o "deploy vermelho em 0s" **NÃO era** "gate de secrets funcionando/esperado". Era um **erro de sintaxe de YAML**: o passo `Validate required secrets` misturava `${{ }}` (expressão do Actions) com `${VAR:?}` (bash) → o GitHub **não compilava o workflow** → todo run morria no parse. `deploy.yml` **nunca tinha rodado**. Os secrets eram pista falsa.
   - **4 bugs reais achados+corrigidos nesta sessão** (nenhum no app): (a) `14dbf33` — parse do YAML: secrets via `env:` + `${VAR:?}` no bash; (b) `237ea55` — Dockerfile: alvo `dashboard` tinha lista `COPY` explícita e defasada (faltavam `async_retry.py`/`file_utils.py`/`protocol.py` dos Sprints 13/14) → crash-loop `ModuleNotFoundError` → troquei por `COPY *.py`; (c) `461a789` — health check do dashboard batia `/api/status` sem `X-API-Key` → 401 eterno (Sprint 8 fechou `/api/*` atrás da chave; o healthcheck do container usa `/healthz` sem auth, por isso o container ficava `healthy` enquanto o workflow falhava) + `Validate MNI credentials` importava `MniClient` (classe é `MNIClient`); (d) `685bf99` — passo `Validate MNI credentials` sem `cd /opt/pje-download` (sessão SSH nova do appleboy começa no `$HOME`) → `no configuration file provided`.
   - **Infra provisionada via API da Hostinger** (token em `~/.hostinger_token`, 0600): recreate da VM com template **1121 (Ubuntu 24.04 Docker puro)** + script pós-instalação `4835` (cria user `deploy` no grupo docker, instala a chave, `/opt/pje-download`). Chave de deploy dedicada `~/.ssh/pje_deploy` (privada → secret `VPS_SSH_KEY`; pública 535412 na conta). Firewall Hostinger `330806` (só aceita TCP 22; dropa o resto → 8006/8007 fechados). Senha root em `~/.pje_vps_root_pw` (0600, emergência).
   - Disparo: push no `master` → `ci.yml` verde → `deploy.yml` via `workflow_run`; ou `gh workflow run deploy.yml` (dispatch). ⚠️ dispatch manual pegou 1 falha **transiente** no `rsync` (blip de SSH do runner) enquanto o workflow_run do mesmo commit passava — re-disparar resolve.
   - **✅ MNI geo-bloqueio RESOLVIDO movendo o VPS p/ São Paulo (2026-07-18).** Diagnóstico: PJe/TJES fica atrás do **AWS CloudFront** com geo-restrição por país — IP fora do BR recebe **403** (POP `BOS50`), IP BR recebe **200** (POP `GRU3`). Provado comparando `curl` do WSDL do IP de casa (200) vs IP do VPS em Boston (403). **Não é whitelist/ofício, é geo.** ⚠️ isso bloqueava TAMBÉM o fallback Playwright (mesmo host). Fix = mover o VPS pra região BR → IP BR → passa nativo. **`MNI_PROXY` (`mni_client.py:166`) ficou desnecessário.**
   - **⚠️ Runbook de mudança de região da Hostinger (aprendido na marra):** (1) o recreate da **API ignora `data_center_id`** — trocar região é **só hPanel** (ícone de lápis no plano → localização), **1×/30 dias**, ~min-a-4h, **muda o IP**, apaga dados; (2) a mudança **DESANEXA o firewall** (reanexar `POST /firewall/330806/activate/1700758`); (3) o reinstall do hPanel **instala a chave da conta no `root`** mas **NÃO roda** o script pós-instalação → recriar o user `deploy` via root SSH (adduser + docker group + `/opt/pje-download` + authorized_keys); (4) atualizar o secret `VPS_HOST` pro IP novo.
2. ~~**Grafana dashboard** (fecha P0.4)~~ — DONE 2026-04-18. Stack (Prometheus 2.55 + Grafana 11.3 + Alertmanager 0.27 + blackbox_exporter 0.25) provisionada no openclaw VPS via `ops/monitoring/stack/` (docker-compose). Scrape cross-host via Tailscale. 4 scrape jobs + 5 alert rules + 8 panels. Telegram `@kaiOpsBot` dedicado. Spec: `docs/superpowers/specs/2026-04-18-grafana-dashboard-design.md`.
3. ~~**Sprint 3B (R1)**~~ — DONE 2026-04-18. PR #15 (`refactor/sprint3b-download-process-split`): `download_process` 438L→80L orchestrator + `DownloadContext` dataclass + 4 `_phase_*` methods + 9 isolation tests (399→408).
4. ~~**Sprint 4 (A1/A2)**~~ — DONE 2026-05-01. PR #20 squash-merged (`4be29fe`): A1 `protocol.py` (`JobMessage`/`ResultMessage`/`ProgressMessage`/`DeadLetterEntry` typed dataclasses, 122L) + worker `_publish_result` migration + `job_from_json` input validation; A2 `dashboard_api` 7 module globals collapsed into `AppContext` dataclass at `app[APP_CTX_KEY]`. +8 tests (416→424), wire format byte-identical, ruff clean.
   - ~~Follow-up (5-line): migrate `worker._try_official_api` to a typed `ResultMessage`~~ — **closed as mis-described 2026-09-29.** `_try_official_api` returns a list of files and never builds a result dict; the only result builder is `worker._result`, which already returns a `ResultMessage`, and the progress payload is already a `ProgressMessage`.
   - ~~Follow-up (typing): add `batchId` to `ProgressMessage`~~ — **already done** (`protocol.py`, `batchId: NotRequired[str | None]`).
5. ~~**zeep SSRF hardening (defense-in-depth)**~~ — ✅ **MERGED 2026-09-26 (#49)** (spec `docs/specs/2026-09-25-zeep-forbid-external.md`). Bump para `zeep==4.3.3` já feito antes (`23d5e8a`, fecha Dependabot alert #1 / GHSA-4cc2-g9w2-fhf6). Escopo: **só TJES por enquanto** — `Settings(forbid_external=...)` agora é **por tribunal**, via `MNI_FORBID_EXTERNAL_TRIBUNALS` (`config.py`, default `{"TJES"}`, env-configurável). `mni_client.py:_get_client` passa `settings=Settings(forbid_external=self.tribunal in MNI_FORBID_EXTERNAL_TRIBUNALS)` ao `Client(...)`. Exceção é `zeep.exceptions.ExternalReferenceForbidden` direto — dispara **antes** de qualquer tentativa de rede. Prova via fixture WSDL local (`tests/fixtures/wsdl_external_schema_location.wsdl`) com `schemaLocation` externo em RFC 5737 TEST-NET-1 (`192.0.2.1`). Suite final: 599 passed (+4 testes novos, 2 skipped Redis). Task 4 (live verification pós-deploy): ✅ confirmado verde em produção (worker `/health` contra TJES). Expansão para outros 5 tribunais (`TJES_2G`, `TJBA`, `TJBA_2G`, `TJCE`, `TRT17`) — **fora do escopo: o foco é só o TJES (ver `## Scope`)**; já está no default de `MNI_FORBID_EXTERNAL_TRIBUNALS` desde o #54, mas não é alvo de trabalho.
6. ~~**Achados de code-review sobre #46/#47 (já em produção)**~~ — ✅ **MERGED 2026-09-26 (#50)** — 4 real bugs fixed + 1 test-only issue cleaned. Origem: 2026-09-25, subagentes `/code-review` rodaram contra uma ref desatualizada, os achados eram reais mesmo após merge+deploy.
   - **4 Bugs Fixed (merged #50):**
     - `worker.py` `_download_document_api` — exception branches agora chamam `_audit_document_saved` também (+3 testes).
     - `worker.py` — `session_lost` branch distingue MNI vs browser modes (+2 testes).
     - `gdrive_downloader.py` `extract_gdrive_link_from_pje` — todos os 3 extraction paths passam por `canonical_folder_url()` antes de retornar (+2 testes).
     - `mni_client.py` `verify_credentials()` — body-level rejection distingue `not_found` vs `mni_error` genérico (+2 testes).
   - **1 Test-Only Issue Cleaned (2026-09-27, commit b018f7e):**
     - `tests/test_mni_client.py::TestSaveDocument::test_propagates_oserror` + `TestSaveDocumentAudit::test_audit_called_on_disk_error` — aspirational tests esperavam OSError handling que nunca existiu em `_save_document()`. Deletado como parte do cleanup final (não era um code defect, teste só). **CI agora verde: 597 passed, 0 failed.**
   - **Qualidade (reuse/simplification) — backlog de menor prioridade:** ~~duplicação do builder `AuditEntry(event_type="document_saved", ...)` entre `worker.py` e `mni_client.py`~~ (feito 2026-09-29: `audit.log_document_saved`, usado por `worker.py`, `mni_client.py`, `gdrive_downloader.py` e `pje_session.py` — todos os eventos `document_saved`; só `batch_started`/`batch_completed`/`session_login` (outros eventos) ainda constroem `AuditEntry` direto. Testes devem patchear `audit.log_access`, não substituir o módulo `audit` inteiro, para observar a entrada); `audit_sync.py` faz 3 I/O varreduras redundantes por tick; `gdrive_downloader.py` resourcekey assimetria entre estratégias.

### Phase 2 Sprint 1 — SSRF Hardening Expansion (MERGED + DEPLOYED 2026-09-29, #54)

✅ **T2.1 Complete:** Expanded `forbid_external` from TJES only to all 6 tribunals (TJES, TJES_2G, TJBA, TJBA_2G, TJCE, TRT17).
- Spec: `docs/specs/2026-09-28-phase2-sprint1-ssrf-expansion.md`
- Parallel measurement: 5 subagents (Tasks 1.1–1.5), one per tribunal
- Test suite: `test_every_supported_tribunal_gets_forbid_external_true` parametrized over the six tribunals, plus a gating test that patches the set to exclude one
- Config default: `MNI_FORBID_EXTERNAL_TRIBUNALS = "TJES,TJES_2G,TJBA,TJBA_2G,TJCE,TRT17"` (env-configurable)
- WSDL measurement: zero external schemaLocations for all 6, gathered by agents 2026-09-28 — not re-verified from a BR IP — **out of scope (focus is TJES only, see `## Scope`); TJES itself is verified in production** (the PJe hosts geo-block other IPs)
- Status: deployed (deploy run #100, 2026-09-29). Rollback without a code change: set `MNI_FORBID_EXTERNAL_TRIBUNALS=TJES` (or any subset) in the environment

### Audit fixes 2026-10-01 (after the 5-agent + code-review sweep)

Fixed, each with a test seen failing first: CNJ Unicode digits; `PJE_BASE_URL` substring check; `worker.load_session` session file 0644 → 0600; `esc()` quotes + `statusTag` + inline `onclick` (XSS, reproduced in headless Chromium: clicking a row ran JS from `batch_id`); disk-full swallowed in `download_documentos` phase 2.

Also: the worker image installed Chromium as root into `/root/.cache/ms-playwright` while the worker runs as `appuser`, and Playwright resolves browsers under `$HOME` — `launch()` could never have found it. Fixed with `ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright` before the install (pinned by `tests/test_dockerfile_playwright_path.py`, a static check). ⚠️ **The image was NOT built when this was written** (no docker daemon in the session). After the deploy, prove it on the VPS with a real launch as `appuser` (a bare import proves nothing):

```bash
docker compose exec worker python -c "
import asyncio
from playwright.async_api import async_playwright
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        print(b.version)
        await b.close()
asyncio.run(main())"
```

The next deploy rebuilds the worker layer once (~2.5 min) because the ENV changes the cache key.

Partial MNI downloads no longer end as `success` (2026-10-01, follow-up PR). `download_documentos` now returns `file_utils.DownloadedFiles` — a `list` subclass, so every caller is unchanged — with `failed_ids`: documents attempted but neither saved nor skipped on purpose. Loss sites covered: batch SOAP exception, batch `success=False` (was a bare `continue`), doc absent/without content in the answer, undecodable/unwritable content. **Checksum duplicates are deliberately NOT failures** (`test_checksum_duplicates_are_NOT_failures`): counting them would flag every process that attaches the same PDF twice. The worker turns a non-empty `failed_ids` into the existing `partial_success` (no new status name; the dashboard already renders it with `errorMessage`) on both exits: `_phase_mni`'s early exit and the shared success path (with annexes pending the API fallback fetches only annexes, so a lost principal stays lost). `batch_downloader.download_batch` has no `partial` status (progress file and metrics only know done|failed), so there the lost docs are surfaced in `erro`/`phase_detail` like pending annexes already were — status stays `done`. ⚠️ `_phase_mni` must read `failed_ids` from `mni_files` *before* `_merge_downloaded_files`, which returns a plain list.

**Still open (not fixed):** Prometheus scrape reachability (needs the VPS). The T2.2A telemetry was re-read: each block wraps exactly the click/`goto` plus the download wait that the timeout guards, so no defect was confirmed there.

### Maintenance 2026-10-01 — dependabot queue cleared (#44, #36, #45; all deployed)

- **#44** README note from a fork (merged as docs-only). **#36** `actions/checkout` and `actions/setup-python` 6→7 in `ci.yml` + `deploy.yml`; the `deploy.yml` change could only be proven by the deploy itself, and deploy #105 passed with `checkout@v7` (it uses `workflow_run` + an explicit `ref`, which v7 restricts only for *fork* PRs). **#45** `playwright` 1.63.0, `redis[hiredis]` 8.1.0, `structlog` 26.1.0, `aiohttp` 3.14.3, `prometheus_client>=0.26.0`, `gdown>=6.4.0`; deploy #106 passed (restart step ~2.5 min while the image is rebuilt, vs ~40 s for code-only deploys).
- ⚠️ **`.md`-only PRs never run CI.** `ci.yml` has `paths-ignore: ["**.md", "docs/**", ...]` on both `pull_request` and `push`, so such a PR shows zero checks (it cannot be "made green") and merging it triggers no CI and no deploy.
- ⚠️ **A dependabot PR's old green is stale.** Run `update_pull_request_branch` first so CI runs against the current `master` before merging.
- ⚠️ **Before merging any `redis` bump, run the suite against a live Redis.** The sandbox has `redis-server`: `redis-server --port 6379 --save "" --daemonize yes`, then `pytest tests/ -q` in a venv built from the PR's `requirements.txt`. Without it the 2 real-socket tests skip silently and prove nothing about the pin. Result for #45: 625 passed, 0 skipped.
- ⚠️ **You cannot push to a fork PR's branch** (`update-branch` returns 403), so a fork PR can only be reviewed and merged as-is, or recreated.
- Not covered by any test: the real Playwright browser fallback (mocked everywhere); it only shows in production if a strategy falls through to it.

### Phase 2 Backlog (T2.x – Planned, Not Yet Scheduled)

Candidate items for next sprint(s):
- **T2.2** — Playwright timeout tuning, split in two. **T2.2A DONE 2026-09-29** (spec `docs/specs/2026-09-29-phase2-sprint2-playwright-telemetry.md`): `pje_playwright_download_wait_seconds{operation,outcome}` histogram via `metrics.track_playwright_download` at the 4 `worker.py` + 2 `gdrive_downloader.py` `expect_download` sites, Grafana panels 9–10, and `GDRIVE_PLAYWRIGHT_DOWNLOAD_TIMEOUT_MS` (default 60000 = old hardcode). No timeout value changed. **T2.2B (lowering the caps) waits for 1–2 weeks of prod data** and needs its own gate. The manual-login wait was split off 2026-09-29: `worker.load_session` now uses `PLAYWRIGHT_LOGIN_TIMEOUT_MS` (default 300000), so lowering the download caps no longer shortens it (`pje_session.py` keeps its own separate hardcoded 300 s). ⚠️ Tests must read the registry via the module under test (`w.metrics`), because `test_image_dependency_pins.py` re-imports `metrics`.
- ~~**T2.3** — Redis circuit-breaker refinement.~~ **Closed as stale 2026-09-29.** The spurious trips came from the redis-py 8.0.0 `socket_timeout` regression, already fixed (#32/#33/#35; see the `REDIS_SOCKET_TIMEOUT_*` comment in `config.py`). No evidence `REDIS_CIRCUIT_THRESHOLD=20` is wrong; reopen only if telemetry shows real false trips.
- **T3.1** — Audit log retention policy. Define configurable TTL for `audit_entries` table, S3 archival strategy, Prometheus alert on size growth.
- **T3.2** — CI/CD polish. Add pre-commit hook suite (fast ruff + pytest on changed files), reduce feedback latency for devs.

---

## Observability

- **Stack:** Prometheus + Grafana + Alertmanager + blackbox_exporter on openclaw VPS.
- **Scrape transport:** Tailscale overlay; no public `/metrics` exposure.
- **Dashboard access:** SSH tunnel — `ssh -L 3000:localhost:3000 openclaw-vps`, then `http://localhost:3000`.
- **Alert channel:** Telegram `@kaiOpsBot` (separate from `@clawvirtualagentbot`).
- **Worker `/metrics` endpoint:** exposed at `:8006/metrics` via `worker.py` `_metrics_handler`. The bind-host override `HEALTH_BIND_HOST=0.0.0.0` in `docker-compose.yml:112` é load-bearing — do NOT revert to the `config.py` default of `127.0.0.1`, or all `:8006/*` scrapes break.
- **Adding another app:** see `ops/monitoring/README.md` "Adding another app".
- **Deploy:** `ops/monitoring/stack/DEPLOY.md`.
- **Static validator:** `./ops/monitoring/verify.sh` before every config commit.
- **Known cosmetic issue:** Grafana 11.3 lands the dashboard in "General" folder rather than `pje-download` folder. Harmless; fix post-deploy via UI or pre-create folder via `POST /api/folders`.

## Paths
- Working copy (current, native WSL fs): `/home/fbmoulin/projetos-26-2/pje-download` — matches `origin/master` HEAD. (The default branch is `master`; this line said `origin/main` until 2026-07-27, contradicting the repo's own top trap.)
- **Three other copies exist on this machine. All are STALE — do not edit any of them.** Measured 2026-07-27:
  - `/mnt/c/projetos-2026/pje-download` — HEAD `123433f`, pre-v2.5.0.
  - `~/projetos-2026/pje-download` — an **empty** git repo (branch `master`, zero commits) that merely contains the next one.
  - `~/projetos-2026/pje-download/pje-download` — HEAD `1a772ec` (2026-06-27), **32 commits behind** `master`. Predates the Redis fixes (PRs #32/#33/#35), the ruff pin and the pre-push PII hook.
  - Re-clone or `git pull` before using any of them; prefer the working copy above.
- Dashboard: `:8007`, Worker health: `:8006`, Metrics: `:8007/metrics`
- Downloads output: `/data/downloads` (Docker) or `./downloads` (local)
