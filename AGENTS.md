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
- `pytest tests/ -q` is the full suite. Verified PR103 code `e0a5d72`: CI43551254323 had 1331 passed, no skips, 1 warning with real Redis; local validation had 1317 passed, 14 skipped, 1 warning without Redis. Counts are dated evidence, not permanent assertions.
- `uvx ruff@0.14.14 check .` and `uvx ruff@0.14.14 format --check .` match CI exactly: CI pins ruff 0.14.14 and lints the whole repo, not a file list.
- `python tools/verify_spec.py docs/specs/*.md` is the canonical verifier for Markdown specification documents (SDD specs).

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
- The repo is public and handles court records: never commit CPF/CNPJ/CNJ-shaped literals (the pre-push gate and gitleaks block them). Tests build such values at runtime.
- Claude Code sessions get project hooks from `.claude/settings.json` (`tools/prompt_gate.py`) only when opened at the repo root; see `CLAUDE.md` "Prompt gate".


## Current Handoff and D2 Evidence

- Read `HANDOFF.md` and `TODO.md` before resuming. Dated handoffs are historical snapshots, not current production state.
- SPEC-D2-001 / PR103 is ready for review, not merged/deployed. Mac measurement and isolated UserPromptSubmit smoke completed via Felipe's supplied output. On14/11 Felipe explicitly closed D2/Task9/T7: keep all three quality rules advisory, no promotion/removal or retroactive extension. This decision supersedes the previous extension calendar; no new deadline or schedule. See Task9 for the dated evidence. Historical event labeling remains optional and does not block closure.
- Manual CLI: `python3 tools/prompt_gate.py --label <id> <regra> fp|tp`; short syntax only for single-rule events. Do not apply a legacy multi-rule label to each rule.
- Keep evidence append-only and outside git. `--report` is read-only; missing/corrupt evidence is inconclusive, not a zero sample or approval. Use one labeler at a time.
- Do not change PII/Stop, enable quality blocking, extend a review deadline or remove a rule merely because the technical implementation passed CI.
- Rollback keeps v2 labels; the old CLI report must not be used for D2 decisions after rollback.
- Focused regression: `pytest tests/test_prompt_gate.py tests/test_prompt_gate_hook.py tests/test_prompt_gate_stop.py tests/test_prompt_gate_telemetry.py tests/test_prompt_gate_d2.py tests/test_refine_prompt_skill.py -q`.
- Documentation-only changes can be skipped by paths-ignore, but a synchronization of PR103 may still run CI because its overall diff includes Python. Check actual runs; cite the tested SHA and do not infer test execution from a documentation commit.
