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
# .env buscado automaticamente em: kratos-master/config/.env → ./ (via config.load_env())
```

## Stack
- Runtime: Python 3.12, aiohttp (not FastAPI), zeep (SOAP), structlog, asyncio
- SOAP calls: always via `asyncio.to_thread` — zeep is synchronous
- Test suite: pytest (183 tests) — run with `pytest tests/ -q` before any commit

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

## Security

- `DASHBOARD_API_KEY` env var required for POST endpoints in production (empty = dev mode, no auth)
- Session file written with 0600 permissions
- `PJE_BASE_URL` validated: must be HTTPS `.jus.br` domain
- Rate limiter parses `X-Forwarded-For` for real client IP behind proxy
- Worker health bound to 127.0.0.1 (not exposed externally)

## Known Issues (remaining)

- MNI blocked by cloud IP — Playwright fallback via `pje_session.py`
- Test coverage ~68% (77/113 symbols) — HARD symbols (browser/SOAP/Redis) remain untested
- No audit trail for CNJ 615/2025 compliance — needs Sprint 5

## Paths
- WSL: `/mnt/c/projetos-2026/pje-download`
- Dashboard: `:8007`, Worker health: `:8006`, Metrics: `:8007/metrics`
- Downloads output: `/data/downloads` (Docker) or `./downloads` (local)
