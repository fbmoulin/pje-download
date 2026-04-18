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
ruff check dashboard_api.py batch_downloader.py mni_client.py worker.py gdrive_downloader.py pje_session.py config.py metrics.py
ruff format dashboard_api.py batch_downloader.py mni_client.py worker.py gdrive_downloader.py pje_session.py config.py metrics.py
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
- Test suite: pytest (377 tests) — run with `pytest tests/ -q` before any commit
- Audit sink: asyncpg → Railway Postgres (optional, opt-in via `AUDIT_SYNC_ENABLED`)

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

Tudo acionável-via-código da auditoria técnica de 2026-04-17 está em master. Falta:

1. **Deploy prod** — SSH na VPS, setar `AUDIT_SYNC_ENABLED=true` + `DATABASE_URL=<audit_writer URL>`, restart do container `pje-dashboard`. Validado localmente via docker-compose + Playwright.
2. **Grafana dashboard** (fecha P0.4) — provisionar Grafana (reusar VPS openclaw se possível), consumir `/metrics` do `:8007`. Alertas:
   - `pje_audit_sync_lag_seconds_event_time > 60` → atraso de sync
   - `pje_audit_sync_batches_total{status="failed"}` → Railway caiu
   - `pje_worker_dead_letters_total > 0` → jobs malformados
   - Liveness probe via `/health` detecta circuit breaker (`_health_status=redis_unreachable`)

## Paths
- WSL: `/mnt/c/projetos-2026/pje-download`
- Dashboard: `:8007`, Worker health: `:8006`, Metrics: `:8007/metrics`
- Downloads output: `/data/downloads` (Docker) or `./downloads` (local)
