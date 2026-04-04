# pje-download — P0/P1 Hardening Sprint

> Created: 2026-04-04 | Status: APPROVED
> Scope: 12 bug fixes (3 CRITICAL + 9 HIGH) + 2 new test modules (~28 tests)

## Context

Three parallel research agents (architecture explorer, bug reviewer, test coverage analyzer) audited all 8 Python modules (~4,500 lines). The codebase grew organically from CLI script to web service; the happy path is solid but error handling, concurrency, and resource cleanup have gaps.

Current state: 73 tests passing, master branch clean.

## Bug Fixes

### CRITICAL (3)

#### BUG-1: Browser not closed on session-save failure
- **File:** `pje_session.py:99-105`
- **Root cause:** `session_file.write_text()` can raise (disk full, permissions). When it does, `browser.close()` on line 105 is skipped.
- **Fix:** Wrap lines 99-105 in `try/finally: await browser.close()`.

#### BUG-2: TOCTOU race on `_login_running` flag
- **File:** `dashboard_api.py:487-505`
- **Root cause:** `_login_running = True` is set inside `_do_login()` (the spawned task), not before `asyncio.create_task()`. Two near-simultaneous POST requests can both pass the guard.
- **Fix:** Set `_login_running = True` in `handle_session_login` before `create_task`. Remove the assignment from `_do_login`.

#### BUG-3: Concurrent `submit_batch` can orphan running task
- **File:** `dashboard_api.py:103-133, 338-353`
- **Root cause:** Between the "is running?" check (line 341) and `submit_batch()` (line 353), there are `await` points. A second request can slip through, overwriting `_task` and orphaning the first.
- **Fix:** Add `_batch_lock = asyncio.Lock()` and wrap the check+submit critical section in `async with _batch_lock`.

### HIGH (9)

#### BUG-4: Parse exception silently returns `success=True` with 0 documents
- **File:** `mni_client.py:511-517`
- **Root cause:** `_parse_processo()` catches all exceptions, logs a warning, and returns a partial `MNIProcesso`. The caller treats this as success. Result: silent data loss.
- **Fix:** When `_parse_processo` raises, return `MNIResult(success=False, error="parse_failure: {exc}")`.

#### BUG-5: WSDL fetch blocks event loop on first `consultar_processo` call
- **File:** `mni_client.py:223`
- **Root cause:** `self._get_client()` is called synchronously. First invocation fetches WSDL over HTTPS (up to 60s blocking).
- **Fix:** Change to `client = await asyncio.to_thread(self._get_client)` in `consultar_processo`.

#### BUG-6: `_seen_checksums` grows without bound
- **File:** `mni_client.py:142, 704`
- **Root cause:** Set accumulates SHA-256 hashes forever across all downloads for the lifetime of the `MNIClient` instance.
- **Fix:** Scope `_seen_checksums` as a parameter to `download_documentos` (local set per batch), not an instance attribute.

#### BUG-7: Path traversal check missing in `batch_downloader.py`
- **File:** `batch_downloader.py:362-364`
- **Root cause:** `worker.py` has `is_relative_to` check; `batch_downloader.py` does not. CLI callers bypass the API validation layer.
- **Fix:** Add `if not proc_dir.resolve().is_relative_to(output_dir.resolve()): raise ValueError(...)` after constructing `proc_dir`.

#### BUG-8: `stream=True` negated by `dl_resp.content`
- **File:** `gdrive_downloader.py:227`
- **Root cause:** Despite `stream=True`, `dl_resp.content` reads the entire response into memory. Large scanned PDFs (50-200MB) can exhaust RAM.
- **Fix:** Replace with `for chunk in dl_resp.iter_content(chunk_size=65536): f.write(chunk)`.

#### BUG-9: `requests.Session` never closed
- **File:** `gdrive_downloader.py:145`
- **Root cause:** `session = requests.Session()` is created but never closed. TCP connections leak for long-running processes.
- **Fix:** Use `with requests.Session() as session:` context manager.

#### BUG-10: `gdrive_map` accepted without validation
- **File:** `dashboard_api.py:350-353`
- **Root cause:** No size limit or URL format validation on `gdrive_map`. Attacker can send 100K entries or arbitrary URLs.
- **Fix:** Validate `len(gdrive_map) <= MAX_BATCH_SIZE` and check each value is a valid GDrive folder URL via `extract_folder_id()`.

#### BUG-11: Blocking file I/O on event loop in `handle_index`
- **File:** `dashboard_api.py:522-530`
- **Root cause:** `html_path.read_text()` is synchronous, called on every `GET /` request.
- **Fix:** `text = await asyncio.to_thread(html_path.read_text, "utf-8")`.

#### BUG-12: `SESSION_FILE` uses relative path, diverges from config
- **File:** `pje_session.py:39`
- **Root cause:** `SESSION_FILE = Path("pje_session.json")` is relative to CWD. In Docker, CWD is container root, not `/data/`. `config.SESSION_STATE_PATH` points to the correct location but `pje_session.py` doesn't use it.
- **Fix:** `SESSION_FILE = Path(os.getenv("SESSION_STATE_PATH", "pje_session.json"))` or import from config. Ensure `batch_downloader.py` also uses the unified path.

## New Test Modules

### `tests/test_pje_session.py` (~18 tests)

Pure function tests (no mocks needed):
- `_safe_filename`: special chars stripped, length limited, empty input
- `_unique_path`: no collision returns original, collision appends suffix
- `_guess_ext`: content-type mapping, unknown type, None

Mocked Playwright tests:
- `_load_state`: existing file loads JSON, missing file raises FileNotFoundError, corrupt JSON raises
- `is_valid`: mock playwright → page navigates → returns True/False
- `interactive_login`: mock playwright → success returns True, timeout returns False
- `download_processo`: mock `_try_api` success, mock `_try_api` fail → `_try_browser` fallback

### `tests/test_config.py` (~10 tests)

- `is_valid_processo`: valid CNJ format, missing segment, extra digit, whitespace stripped, empty string, non-numeric
- `load_env`: tmp `.env` file with `KEY=value`, comment stripping (`KEY=val # comment`), missing file returns without error

## Definition of Done

- All 12 bugs fixed with targeted code changes
- `pytest tests/ -q` reports ≥ 100 tests passing (73 existing + ~28 new)
- `ruff check` and `ruff format` pass
- No regressions in existing 73 tests
- CLAUDE.md updated with new test count

## Out of Scope (P2/P3 backlog)

- Worker health cache (30s TTL)
- Rate limiter `X-Forwarded-For` support
- `app.js:isAntigo()` year rule sync
- `_save_document` async write
- CLI gdrive_map JSON error handling
- Silent `_load_history` error logging
- `mni_client.py:_parse_processo` comprehensive test coverage
- `worker.py:download_process` test coverage
- `gdrive_downloader.py:download_gdrive_folder` test coverage
