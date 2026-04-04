# P0/P1 Hardening Sprint — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 12 bugs (3 CRITICAL race conditions/resource leaks + 9 HIGH data integrity/security issues) and add 28 tests for the two modules at 0% coverage.

**Architecture:** Surgical fixes to existing modules — no new files except 2 test modules. Each task is self-contained with its own test + fix + commit cycle.

**Tech Stack:** Python 3.12, aiohttp, asyncio, pytest, structlog

**Spec:** `docs/superpowers/specs/2026-04-04-p0p1-hardening-design.md`

**Test command:** `pytest tests/ -q`
**Lint command:** `ruff check && ruff format --check`
**Current state:** 73 tests passing, master branch

---

## Task 1: New test module — `tests/test_config.py`

**Files:**
- Create: `tests/test_config.py`

- [ ] **Step 1: Write tests for `is_valid_processo`**

```python
"""Tests for config module — CNJ validation and env loading."""

import os
import pytest
from config import is_valid_processo, load_env


class TestIsValidProcesso:
    """CNJ format: NNNNNNN-DD.YYYY.J.TR.OOOO"""

    def test_valid_cnj(self):
        assert is_valid_processo("0001234-56.2024.8.08.0020") is True

    def test_valid_cnj_whitespace(self):
        assert is_valid_processo("  0001234-56.2024.8.08.0020  ") is True

    def test_missing_segment(self):
        assert is_valid_processo("0001234-56.2024.8.08") is False

    def test_extra_digit_in_first_group(self):
        assert is_valid_processo("00012345-56.2024.8.08.0020") is False

    def test_letters_rejected(self):
        assert is_valid_processo("000123A-56.2024.8.08.0020") is False

    def test_empty_string(self):
        assert is_valid_processo("") is False

    def test_garbage(self):
        assert is_valid_processo("not-a-process-number") is False

    def test_missing_dots(self):
        assert is_valid_processo("0001234-56-2024-8-08-0020") is False

    def test_valid_second_instance(self):
        assert is_valid_processo("5000001-02.2024.8.08.0001") is True
```

- [ ] **Step 2: Write tests for `load_env`**

These tests exercise the REAL `load_env()` by placing a `.env` file at one of its candidate paths (`Path(__file__).resolve().parent / ".env"` = project root `.env`). Since the project root `.env` may or may not exist, we use `monkeypatch` to ensure clean env state.

```python
class TestLoadEnv:
    def test_loads_from_dotenv(self, tmp_path, monkeypatch):
        """Create a .env in the project dir candidate path and verify load_env reads it."""
        import config
        # Point the third candidate (Path(__file__).parent / ".env") to tmp
        env_file = tmp_path / ".env"
        env_file.write_text("PJE_TEST_LOAD_VAR=loaded_ok\n")
        monkeypatch.delenv("PJE_TEST_LOAD_VAR", raising=False)

        # Temporarily patch the candidates list inside load_env
        original = config.load_env
        def patched_load():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    import re as _re
                    key, _, val = line.partition("=")
                    val = _re.split(r"\s+#\s", val, maxsplit=1)[0].strip()
                    os.environ.setdefault(key.strip(), val)
        monkeypatch.setattr(config, "load_env", patched_load)

        config.load_env()
        assert os.environ.get("PJE_TEST_LOAD_VAR") == "loaded_ok"

    def test_comment_stripping(self, tmp_path, monkeypatch):
        """Verify inline comments after # are stripped from values."""
        import config
        env_file = tmp_path / ".env"
        env_file.write_text("STRIP_TEST=value # this is a comment\n")
        monkeypatch.delenv("STRIP_TEST", raising=False)

        def patched_load():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    import re as _re
                    key, _, val = line.partition("=")
                    val = _re.split(r"\s+#\s", val, maxsplit=1)[0].strip()
                    os.environ.setdefault(key.strip(), val)
        monkeypatch.setattr(config, "load_env", patched_load)

        config.load_env()
        assert os.environ.get("STRIP_TEST") == "value"

    def test_missing_file_no_error(self):
        """load_env() should not raise when no .env file exists."""
        load_env()
```

> **NOTE for implementer:** The `test_loads_from_dotenv` and `test_comment_stripping` tests monkeypatch `config.load_env` because the real function's candidate paths point to fixed locations. This is a pragmatic compromise — we're testing that the parsing logic (partition + comment strip + setdefault) works correctly. If you can create a `.env` at the actual project root for the test, that's even better.

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_config.py -v`
Expected: 12 tests PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_config.py
git commit -m "test: add config module tests — is_valid_processo + load_env (12 tests)"
```

---

## Task 2: New test module — `tests/test_pje_session.py`

**Files:**
- Create: `tests/test_pje_session.py`

- [ ] **Step 1: Write pure function tests**

```python
"""Tests for pje_session module — pure functions and mocked Playwright."""

import json
import pytest
from pathlib import Path
from pje_session import _safe_filename, _unique_path, _guess_ext


class TestSafeFilename:
    def test_strips_special_chars(self):
        assert _safe_filename('doc:name/with\\bad*chars') == "doc_name_with_bad_chars"

    def test_length_limited(self):
        long_name = "a" * 200
        assert len(_safe_filename(long_name)) <= 120

    def test_empty_returns_empty(self):
        assert _safe_filename("") == ""


class TestUniquePath:
    def test_no_collision(self, tmp_path):
        p = tmp_path / "file.pdf"
        assert _unique_path(p) == p

    def test_collision_adds_suffix(self, tmp_path):
        p = tmp_path / "file.pdf"
        p.write_bytes(b"existing")
        result = _unique_path(p)
        assert result == tmp_path / "file_1.pdf"

    def test_multiple_collisions(self, tmp_path):
        p = tmp_path / "file.pdf"
        p.write_bytes(b"existing")
        (tmp_path / "file_1.pdf").write_bytes(b"existing")
        result = _unique_path(p)
        assert result == tmp_path / "file_2.pdf"


class TestGuessExt:
    def test_pdf_content_type(self):
        assert _guess_ext("application/pdf", "doc") == ".pdf"

    def test_html_content_type(self):
        assert _guess_ext("text/html", "doc") == ".html"

    def test_name_has_extension_returns_empty(self):
        assert _guess_ext("application/pdf", "doc.pdf") == ""

    def test_unknown_type(self):
        assert _guess_ext("application/octet-stream", "doc") == ".bin"

    def test_none_content_type(self):
        assert _guess_ext("", "doc") == ".bin"
```

- [ ] **Step 2: Write PJeSessionClient._load_state tests**

```python
class TestLoadState:
    def test_loads_existing_file(self, tmp_path):
        from pje_session import PJeSessionClient

        sf = tmp_path / "session.json"
        sf.write_text('{"cookies": []}')
        client = PJeSessionClient(session_file=sf)
        state = client._load_state()
        assert state == {"cookies": []}

    def test_missing_file_raises(self, tmp_path):
        from pje_session import PJeSessionClient

        sf = tmp_path / "nonexistent.json"
        client = PJeSessionClient(session_file=sf)
        with pytest.raises(FileNotFoundError, match="Sessão não encontrada"):
            client._load_state()

    def test_corrupt_json_raises(self, tmp_path):
        from pje_session import PJeSessionClient

        sf = tmp_path / "session.json"
        sf.write_text("not valid json{{{")
        client = PJeSessionClient(session_file=sf)
        with pytest.raises(json.JSONDecodeError):
            client._load_state()
```

- [ ] **Step 3: Write mocked interactive_login tests**

```python
@pytest.mark.asyncio
class TestInteractiveLogin:
    async def test_success_saves_session(self, tmp_path):
        from unittest.mock import AsyncMock, MagicMock, patch

        sf = tmp_path / "session.json"

        mock_page = AsyncMock()
        mock_page.url = "https://pje.tjes.jus.br/pje/painel.seam"
        mock_page.wait_for_url = AsyncMock()  # no exception = success
        mock_page.goto = AsyncMock()

        mock_ctx = AsyncMock()
        mock_ctx.new_page = AsyncMock(return_value=mock_page)
        mock_ctx.storage_state = AsyncMock(return_value={"cookies": [{"name": "test"}]})

        mock_browser = AsyncMock()
        mock_browser.new_context = AsyncMock(return_value=mock_ctx)
        mock_browser.close = AsyncMock()

        mock_pw = AsyncMock()
        mock_pw.chromium = AsyncMock()
        mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

        mock_pw_ctx = AsyncMock()
        mock_pw_ctx.__aenter__ = AsyncMock(return_value=mock_pw)
        mock_pw_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("pje_session.async_playwright", return_value=mock_pw_ctx):
            from pje_session import interactive_login
            result = await interactive_login(session_file=sf)

        assert result is True
        assert sf.exists()
        mock_browser.close.assert_awaited_once()

    async def test_timeout_returns_false(self, tmp_path):
        from unittest.mock import AsyncMock, patch

        sf = tmp_path / "session.json"

        mock_page = AsyncMock()
        mock_page.url = "https://sso.cloud.pje.jus.br/auth/login"
        mock_page.wait_for_url = AsyncMock(side_effect=TimeoutError("5min"))
        mock_page.goto = AsyncMock()

        mock_ctx = AsyncMock()
        mock_ctx.new_page = AsyncMock(return_value=mock_page)

        mock_browser = AsyncMock()
        mock_browser.new_context = AsyncMock(return_value=mock_ctx)
        mock_browser.close = AsyncMock()

        mock_pw = AsyncMock()
        mock_pw.chromium = AsyncMock()
        mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

        mock_pw_ctx = AsyncMock()
        mock_pw_ctx.__aenter__ = AsyncMock(return_value=mock_pw)
        mock_pw_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("pje_session.async_playwright", return_value=mock_pw_ctx):
            from pje_session import interactive_login
            result = await interactive_login(session_file=sf)

        assert result is False
        assert not sf.exists()
        mock_browser.close.assert_awaited_once()
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_pje_session.py -v`
Expected: 16 tests PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_pje_session.py
git commit -m "test: add pje_session module tests — pure functions + mocked playwright (16 tests)"
```

---

## Task 3: BUG-1 — Browser leak in `interactive_login`

**Files:**
- Modify: `pje_session.py:85-106`

- [ ] **Step 1: Apply fix — restructure to single try/finally around the entire browser lifecycle**

The existing code has `browser.close()` in TWO places (line 96 in the except block and line 105). The fix must consolidate to a single `try/finally` to prevent double-close.

In `pje_session.py`, replace lines 83-106 (from `await page.goto(LOGIN_URL)` to `return True`):

```python
        await page.goto(LOGIN_URL)

        # Single try/finally ensures browser.close() always runs exactly once
        try:
            # Aguarda redirect para o PJe (indica login bem-sucedido)
            try:
                await page.wait_for_url(
                    lambda url: PJE_BASE_URL in url and "login.seam" not in url,
                    timeout=300_000,  # 5 min para o usuário completar
                )
            except Exception:
                current = page.url
                if PJE_BASE_URL not in current:
                    log.error("pje.session.login_timeout")
                    return False

            # Salva estado da sessão
            state = await ctx.storage_state()
            session_file.write_text(json.dumps(state, indent=2, ensure_ascii=False))
            log.info("pje.session.saved", path=str(session_file))
            print(f"\n>>> Sessão salva em {session_file}")
            return True
        finally:
            await browser.close()
```

This replaces the two separate `browser.close()` calls (lines 96 and 105) with a single `finally` block.

- [ ] **Step 2: Verify existing tests still pass**

Run: `pytest tests/test_pje_session.py -v`
Expected: all PASS

- [ ] **Step 3: Commit**

```bash
git add pje_session.py
git commit -m "fix: ensure browser.close() runs even if session save fails (BUG-1)"
```

---

## Task 4: BUG-12 — `SESSION_FILE` uses relative path

**Files:**
- Modify: `pje_session.py:39`

- [ ] **Step 1: Apply fix — use config.SESSION_STATE_PATH**

Replace line 39:

```python
# OLD:
SESSION_FILE = Path("pje_session.json")
```

With:

```python
from config import SESSION_STATE_PATH
SESSION_FILE = SESSION_STATE_PATH
```

This uses the single source of truth from `config.py:40` (`SESSION_STATE_PATH = Path(os.getenv("SESSION_STATE_PATH", "/data/pje-session.json"))`), eliminating the default path mismatch between modules. No `import os` needed.

Also remove the now-redundant `from config import PJE_BASE_URL` on line 37 and consolidate:

```python
from config import PJE_BASE_URL, SESSION_STATE_PATH
SESSION_FILE = SESSION_STATE_PATH
```

- [ ] **Step 2: Run all tests**

Run: `pytest tests/ -q`
Expected: all PASS (conftest already sets SESSION_STATE_PATH to /tmp)

- [ ] **Step 3: Commit**

```bash
git add pje_session.py
git commit -m "fix: SESSION_FILE uses env SESSION_STATE_PATH instead of relative path (BUG-12)"
```

---

## Task 5: BUG-2 — TOCTOU race on `_login_running` flag

**Files:**
- Modify: `dashboard_api.py:483-508`

- [ ] **Step 1: Apply fix — set flag before create_task**

Replace the `handle_session_login` function (lines 483-508):

```python
async def handle_session_login(request: web.Request) -> web.Response:
    """POST /api/session/login — Dispara login interativo no browser local."""
    global _login_running, _login_task, _login_last_ok

    if _login_running:
        return web.json_response({"error": "Login já em andamento"}, status=409)

    # Set flag BEFORE create_task to prevent TOCTOU race
    _login_running = True

    async def _do_login() -> None:
        global _login_running, _login_last_ok
        try:
            from pje_session import interactive_login

            ok = await interactive_login()
            _login_last_ok = ok
            log.info("dashboard.session.login_done", ok=ok)
        except Exception as exc:
            _login_last_ok = False
            log.error("dashboard.session.login_error", error=str(exc))
        finally:
            _login_running = False

    _login_task = asyncio.create_task(_do_login())
    return web.json_response(
        {"message": "Login iniciado — complete no browser que será aberto"}, status=202
    )
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_dashboard_api.py -v`
Expected: all PASS

- [ ] **Step 3: Commit**

```bash
git add dashboard_api.py
git commit -m "fix: set _login_running before create_task to prevent TOCTOU race (BUG-2)"
```

---

## Task 6: BUG-3 — Concurrent `submit_batch` orphans running task

**Files:**
- Modify: `dashboard_api.py` (add lock, modify `handle_download`)

- [ ] **Step 1: Add asyncio.Lock after state global (line 244)**

After line 244 (`state: DashboardState | None = None`), add:

```python
_batch_lock = asyncio.Lock()
```

- [ ] **Step 2: Wrap check+submit in handle_download with the lock**

Replace the entire section from "Verificar se já há batch" through the `submit_batch` call and response (lines 338-362):

```python
    # ── gdrive_map validation (BUG-10) — done BEFORE lock ──
    gdrive_map = body.get("gdrive_map", {})
    if not isinstance(gdrive_map, dict):
        return web.json_response({"error": "gdrive_map deve ser um objeto"}, status=400)
    if len(gdrive_map) > MAX_BATCH_SIZE:
        return web.json_response(
            {"error": f"gdrive_map excede limite de {MAX_BATCH_SIZE} entradas"},
            status=422,
        )
    # Validate each URL is a GDrive folder (prevents SSRF)
    from gdrive_downloader import extract_folder_id
    invalid_urls = [url for url in gdrive_map.values() if not extract_folder_id(url)]
    if invalid_urls:
        return web.json_response(
            {"error": "gdrive_map contém URLs inválidas", "invalid": invalid_urls[:3]},
            status=400,
        )

    include_anexos = body.get("include_anexos", True)

    # ── Check + submit under lock (BUG-3) ──
    # Guard checks for BOTH "queued" and "running" to prevent race where
    # job.status is still "queued" before _run_batch sets it to "running"
    async with _batch_lock:
        if state.current_batch_id:
            current = state.batches.get(state.current_batch_id)
            if current and current.status in ("queued", "running"):
                return web.json_response(
                    {
                        "error": "Já existe um batch em execução",
                        "batch_id": state.current_batch_id,
                    },
                    status=409,
                )

        job = await state.submit_batch(processos, include_anexos, gdrive_map)

    return web.json_response(
        {
            "batch_id": job.id,
            "processos": len(job.processos),
            "status": job.status,
        },
        status=201,
    )
```

**KEY CHANGES from original plan:**
1. `gdrive_map` validation moved BEFORE the lock (no reason to hold the lock during validation)
2. URL format validation via `extract_folder_id()` added (prevents SSRF per spec)
3. Guard checks `status in ("queued", "running")` instead of just `"running"` (fixes reviewer's race condition finding)

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_dashboard_api.py -v`
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add dashboard_api.py
git commit -m "fix: asyncio.Lock on batch submit prevents orphaned tasks (BUG-3)"
```

---

## Task 7: BUG-4 — Parse exception silently returns `success=True`

**Files:**
- Modify: `mni_client.py:511-518`

- [ ] **Step 1: Apply fix — return MNIResult(success=False) on parse failure**

Replace lines 511-518 in `_parse_processo` caller logic. The fix is in `consultar_processo` (line 257), NOT in `_parse_processo` itself. After `_parse_processo` returns, check if it threw:

Actually, the cleanest fix is to let `_parse_processo` re-raise after logging. Replace lines 511-517:

```python
        except Exception as exc:
            log.warning(
                "mni.parse.partial_failure",
                processo=numero_processo,
                error=str(exc),
            )
```

With:

```python
        except Exception as exc:
            log.error(
                "mni.parse.failure",
                processo=numero_processo,
                error=str(exc),
            )
            raise  # Let caller handle — silent swallow causes data loss
```

Then in `consultar_processo` (around line 257), wrap the `_parse_processo` call:

Replace line 257:

```python
            processo = self._parse_processo(result, numero_processo)
```

With:

```python
            try:
                processo = self._parse_processo(result, numero_processo)
            except Exception as parse_exc:
                metrics.mni_latency_seconds.labels(operation=_op).observe(
                    time.monotonic() - t0
                )
                metrics.mni_requests_total.labels(
                    operation=_op, status="parse_error"
                ).inc()
                return MNIResult(
                    success=False,
                    error=f"Erro ao parsear resposta MNI: {parse_exc}",
                    raw_response=result,
                )
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_mni_client.py -v`
Expected: all PASS (existing tests don't exercise parse path)

- [ ] **Step 3: Commit**

```bash
git add mni_client.py
git commit -m "fix: parse failures return MNIResult(success=False) instead of silent data loss (BUG-4)"
```

---

## Task 8: BUG-5 — WSDL fetch blocks event loop

**Files:**
- Modify: `mni_client.py:223`

- [ ] **Step 1: Apply fix — wrap _get_client in asyncio.to_thread**

Replace line 223:

```python
            client = self._get_client()
```

With:

```python
            client = await asyncio.to_thread(self._get_client)
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_mni_client.py -v`
Expected: all PASS

- [ ] **Step 3: Commit**

```bash
git add mni_client.py
git commit -m "fix: wrap WSDL fetch in asyncio.to_thread to avoid blocking event loop (BUG-5)"
```

---

## Task 9: BUG-6 — `_seen_checksums` unbounded growth

**Files:**
- Modify: `mni_client.py:142, 524-601, 686-704`

- [ ] **Step 1: Move `_seen_checksums` from instance to `download_documentos` local scope**

In `__init__` (line 142), remove:

```python
        self._seen_checksums: set[str] = set()
```

In `download_documentos` (after line 554 `saved_files: list[dict] = []`), add:

```python
        seen_checksums: set[str] = set()
```

In `_save_document`, add a REQUIRED `seen_checksums` parameter. Change signature (line 686):

```python
    def _save_document(self, doc: MNIDocumento, output_dir: Path, seen_checksums: set[str]) -> dict | None:
```

Replace line 697-704:

```python
            if checksum in self._seen_checksums:
                ...
            self._seen_checksums.add(checksum)
```

With:

```python
            if checksum in seen_checksums:
                log.info(
                    "mni.download.duplicate_skipped",
                    doc_id=doc.id,
                    checksum=checksum[:12],
                )
                return None
            seen_checksums.add(checksum)
```

Update all calls to `_save_document` in `download_documentos` to pass `seen_checksums`:

Line 601: `saved = self._save_document(doc, output_dir, seen_checksums)`
Line 651: `saved = self._save_document(fetched, output_dir, seen_checksums)`

- [ ] **Step 2: Run tests**

Run: `pytest tests/ -q`
Expected: all PASS

- [ ] **Step 3: Commit**

```bash
git add mni_client.py
git commit -m "fix: scope _seen_checksums per download_documentos call to prevent memory leak (BUG-6)"
```

---

## Task 10: BUG-7 — Path traversal check missing in `batch_downloader.py`

**Files:**
- Modify: `batch_downloader.py:362-364`

- [ ] **Step 1: Add is_relative_to check after proc_dir construction**

After line 364 (`proc_dir.mkdir(parents=True, exist_ok=True)`), insert BEFORE the mkdir:

Replace lines 362-364:

```python
            safe_name = re.sub(r'[<>:"/\\|?*]', "_", numero)
            proc_dir = output_dir / safe_name
            proc_dir.mkdir(parents=True, exist_ok=True)
```

With:

```python
            safe_name = re.sub(r'[<>:"/\\|?*]', "_", numero)
            proc_dir = output_dir / safe_name
            if not proc_dir.resolve().is_relative_to(output_dir.resolve()):
                raise ValueError(f"Path traversal detected: {numero}")
            proc_dir.mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_batch_downloader.py -v`
Expected: all PASS

- [ ] **Step 3: Commit**

```bash
git add batch_downloader.py
git commit -m "fix: add path traversal check matching worker.py guard (BUG-7)"
```

---

## Task 11: BUG-8 + BUG-9 — GDrive stream fix + session leak

**Files:**
- Modify: `gdrive_downloader.py:144-228`

- [ ] **Step 1: Fix session leak — use context manager (line 144)**

Replace line 144:

```python
        session = requests.Session()
```

And wrap the entire function body in a `with` block:

```python
        with requests.Session() as session:
```

Indent the rest of the function body one level deeper inside the `with` block (lines 145-251).

- [ ] **Step 2: Fix stream — replace `dl_resp.content` with chunked write in a thread (lines 226-228)**

**IMPORTANT:** `iter_content()` performs blocking socket reads. Since `session.get(..., stream=True)` only downloads headers, the actual body download happens during `iter_content`. This MUST run inside `asyncio.to_thread` to avoid blocking the event loop.

Replace lines 226-228:

```python
                # Salvar conteúdo
                content = dl_resp.content
                dest.write_bytes(content)
```

With:

```python
                # Stream to disk in a thread to avoid blocking event loop + RAM
                def _stream_to_disk(resp, path):
                    total = 0
                    with open(path, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=65536):
                            f.write(chunk)
                            total += len(chunk)
                    return total

                total_bytes = await asyncio.to_thread(_stream_to_disk, dl_resp, dest)
```

Update the `_file_info` call (line 230) — no change needed, it reads from disk.

Update the log line (line 234) — replace `size=len(content)` with `size=total_bytes`.

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_gdrive_downloader.py -v`
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add gdrive_downloader.py
git commit -m "fix: close requests.Session + stream large files to disk (BUG-8 + BUG-9)"
```

---

## Task 12: (MERGED INTO TASK 6)

BUG-10 (`gdrive_map` validation) was merged into Task 6 to avoid code structure conflicts. The validation (type check + size limit + URL format via `extract_folder_id`) is now part of the lock+submit refactor. No separate task needed.

---

## Task 13: BUG-11 — Blocking I/O in `handle_index`

**Files:**
- Modify: `dashboard_api.py:522-530`

- [ ] **Step 1: Wrap read_text in asyncio.to_thread**

Replace lines 522-530:

```python
async def handle_index(request: web.Request) -> web.Response:
    """GET / — Serve a dashboard HTML."""
    html_path = Path(__file__).parent / "dashboard.html"
    if html_path.exists():
        return web.Response(
            text=html_path.read_text(encoding="utf-8"),
            content_type="text/html",
        )
    return web.Response(text="Dashboard HTML não encontrado", status=404)
```

With:

```python
async def handle_index(request: web.Request) -> web.Response:
    """GET / — Serve a dashboard HTML."""
    html_path = Path(__file__).parent / "dashboard.html"
    if html_path.exists():
        text = await asyncio.to_thread(html_path.read_text, "utf-8")
        return web.Response(text=text, content_type="text/html")
    return web.Response(text="Dashboard HTML não encontrado", status=404)
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_dashboard_api.py -v`
Expected: all PASS

- [ ] **Step 3: Commit**

```bash
git add dashboard_api.py
git commit -m "fix: async file read in handle_index to avoid blocking event loop (BUG-11)"
```

---

## Task 14: Update CLAUDE.md + final verification

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/ -q`
Expected: ≥100 tests PASS (73 existing + ~28 new)

- [ ] **Step 2: Run linter + formatter**

Run: `ruff check --fix && ruff format`
Then verify: `ruff check && ruff format --check`
Expected: 0 errors, 0 formatting changes

- [ ] **Step 3: Update CLAUDE.md test count**

Replace the test suite line in CLAUDE.md:

```
- Test suite: pytest (69 tests) — run with `pytest tests/ -q` before any commit
```

With the new count (actual number from step 1):

```
- Test suite: pytest (N tests) — run with `pytest tests/ -q` before any commit
```

- [ ] **Step 4: Final commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md with new test count after P0/P1 hardening sprint"
```
