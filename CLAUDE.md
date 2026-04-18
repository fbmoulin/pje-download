# pje-download — CLAUDE.md

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
ruff check dashboard_api.py batch_downloader.py mni_client.py worker.py gdrive_downloader.py pje_session.py config.py metrics.py audit_sync.py file_utils.py async_retry.py
ruff format dashboard_api.py batch_downloader.py mni_client.py worker.py gdrive_downloader.py pje_session.py config.py metrics.py audit_sync.py file_utils.py async_retry.py
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
- Test suite: pytest (398 tests) — run with `pytest tests/ -q` before any commit
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

## Security

- `DASHBOARD_API_KEY` env var required for POST endpoints in production (empty = dev mode, no auth)
- Session file written with 0600 permissions
- `PJE_BASE_URL` validated: must be HTTPS `.jus.br` domain
- Rate limiter parses `X-Forwarded-For` for real client IP behind proxy
- Worker health bound to 127.0.0.1 (not exposed externally)
- CNJ 615/2025 audit trail: `audit.py` logs every document access to JSON-L (`/data/audit/audit-YYYY-MM-DD.jsonl`, 0600 perms, append-only)

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

- MNI blocked by cloud IP — Playwright fallback via `pje_session.py`
- Test coverage ~85% — remaining ~15% are deep Playwright integration paths (low ROI)

## Backlog (não-código)

Tudo acionável-via-código das auditorias técnicas de 2026-04-17 e 2026-04-18 está em branches/PRs ativos. Restante:

1. **Deploy prod** — SSH na VPS, setar `AUDIT_SYNC_ENABLED=true` + `DATABASE_URL=<audit_writer URL>`, restart do container `pje-dashboard`. Validado localmente via docker-compose + Playwright.
2. ~~**Grafana dashboard** (fecha P0.4)~~ — DONE 2026-04-18. Stack (Prometheus 2.55 + Grafana 11.3 + Alertmanager 0.27 + blackbox_exporter 0.25) provisionada no openclaw VPS via `ops/monitoring/stack/` (docker-compose). Scrape cross-host via Tailscale. 4 scrape jobs + 5 alert rules + 8 panels. Telegram `@kaiOpsBot` dedicado. Spec: `docs/superpowers/specs/2026-04-18-grafana-dashboard-design.md`.
3. **Sprint 3B (R1)** — Split `worker.download_process` (438-line mega-method) em phase-methods + `DownloadContext` dataclass. +8 phase-isolation tests. Plan: `docs/superpowers/plans/2026-04-18-audit-remediation.md#sprint-3b`. ~3-4h focused work.
4. **Sprint 4 (A1/A2)** — Arquitetural (deferido por design):
   - A1: Typed Redis queue protocol (`JobMessage`, `ResultMessage` dataclasses em novo `protocol.py`). Schedule quando um novo campo precisar ser adicionado.
   - A2: `dashboard_api` state globals → request-scoped `AppContext`. Schedule quando test-isolation for um problema real.

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
- WSL: `/mnt/c/projetos-2026/pje-download`
- Dashboard: `:8007`, Worker health: `:8006`, Metrics: `:8007/metrics`
- Downloads output: `/data/downloads` (Docker) or `./downloads` (local)
