# Repository Guidelines

## Project Structure & Runtime Architecture
- `dashboard_api.py` is the control plane: it validates requests, publishes one Redis job per process, aggregates worker results by batch, and persists `_progress.json` and `_report.json`.
- `worker.py` is the execution plane: it consumes `kratos:pje:jobs`, writes per-process downloads under `DOWNLOAD_BASE_DIR`, and replies to the batch-specific queue informed by the dashboard.
- `batch_downloader.py` remains the CLI/batch path for offline runs and recovery tooling; do not reintroduce it into the dashboard request path.
- `pje_session.py`, `mni_client.py`, and `gdrive_downloader.py` are the integration edges; prefer tightening contracts there instead of adding orchestration logic in the frontend.
- `tests/` is the safety net for the queue contract. When changing dashboard/worker coordination, update `tests/test_dashboard_api.py` and `tests/test_worker.py` together.

## Build, Test, and Development Commands
- `pip install -r requirements.txt` installs the Python runtime dependencies.
- `playwright install chromium` prepares the browser fallback locally when session-based flows are under test.
- `python dashboard_api.py --port 8007 --output ./downloads` starts the dashboard.
- `python worker.py` starts the Redis consumer; the dashboard is not the execution path anymore without it.
- `pytest tests/test_config.py tests/test_dashboard_api.py tests/test_worker.py tests/test_pje_session.py tests/test_batch_downloader.py -q` is the focused regression suite for orchestration and integrations.
- `ruff check config.py dashboard_api.py worker.py tests/test_config.py tests/test_dashboard_api.py tests/test_worker.py tests/test_pje_session.py tests/test_batch_downloader.py` keeps lint tight on the critical path.

## Coding Style & Naming Conventions
- Follow the existing Python style: type hints on public functions, concise docstrings, and no unnecessary abstraction layers.
- Keep queue payload keys stable and explicit: `jobId`, `batchId`, `numeroProcesso`, `replyQueue`, `outputSubdir`.
- Prefer additive compatibility when evolving worker result payloads; the dashboard and historical JSON files depend on predictable field names.

## Testing Guidelines
- Any change to batch lifecycle must cover success, partial failure, and timeout/error paths in `tests/test_dashboard_api.py`.
- Any change to worker queue semantics must cover malformed payloads, reply queue routing, and output directory handling in `tests/test_worker.py`.
- When changing persistence shape, verify both live progress (`_progress.json`) and completed reports (`_report.json`).

## Commit & Pull Request Guidelines
- Use conventional Portuguese prefixes such as `fix`, `refactor`, `docs`, `test`, and `chore`.
- Keep orchestration changes focused. Do not mix deploy hardening, queue semantics, and unrelated downloader logic without a good reason.

## Security & Configuration Tips
- Production requires `APP_ENV=production`, `DASHBOARD_API_KEY`, and a non-default `REDIS_PASSWORD`; the deploy workflow now fails when those secrets are missing.
- Do not hard-code credentials or fallback to open production defaults in workflow files, compose files, or source code.
- Treat `TRUST_X_FORWARDED_FOR` as opt-in only behind a trusted reverse proxy.
