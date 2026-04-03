# pje-download — CLAUDE.md

## Stack
- Runtime: Python 3.12, aiohttp (not FastAPI), zeep (SOAP), structlog, asyncio
- SOAP calls: always via `asyncio.to_thread` — zeep is synchronous
- No test suite — verify changes with `python -c "import ast; ast.parse(open('f').read())"` + `ruff check`

## Env Loading (critical gotcha)
- `config.py` constants are module-level — they may be empty strings if `.env` not yet loaded
- Always call `_load_env()` before constructing `MNIClient()` or reading MNI credentials
- Lazy imports inside functions (e.g. `batch_downloader.py`) are intentional — preserve them

## Linting
- E402 (module-level import not at top) is intentional in `dashboard_api.py` and `mni_client.py` — do not fix
- Run: `ruff check dashboard_api.py batch_downloader.py mni_client.py worker.py`

## Security (do not weaken)
- CORS is restricted to localhost-only (`_ALLOWED_ORIGINS`) — do not revert to `"*"`
- Rate limiter tracks last-seen per IP to prevent memory leaks — keep `_rate_bucket_last_seen`
- MNI credentials are validated before any SOAP call — keep fail-fast check in `download_batch()`

## Paths
- WSL: `/mnt/c/projetos-2026/pje-download`
- Dashboard: `:8007`, Worker health: `:8006`
- Downloads output: `/data/downloads` (Docker) or `./downloads` (local)
