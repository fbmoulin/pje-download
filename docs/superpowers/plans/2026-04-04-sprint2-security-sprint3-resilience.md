# Sprint 2+3: Security Hardening + Resilience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 5 CRITICAL + 15 HIGH issues from the final audit. Eliminate all security blockers and production resilience gaps.

**Architecture:** Surgical fixes to existing modules + one new shared utility module (`config.py` consolidation). No new services or endpoints.

**Tech Stack:** Python 3.12, aiohttp, asyncio, pytest, structlog

**Audit report:** `docs/reports/2026-04-04-audit-final.md`
**Previous spec:** `docs/superpowers/specs/2026-04-04-p0p1-hardening-design.md`

**Test command:** `pytest tests/ -q`
**Lint command:** `ruff check --fix && ruff format`
**Current state:** 101 tests passing, master branch

---

## File Structure

| File | Changes |
|------|---------|
| `config.py` | Add `sanitize_filename()`, `unique_path()`, `atomic_write_text()`, validate `PJE_BASE_URL` |
| `dashboard_api.py` | API key middleware, remove info disclosure, batch eviction, _load_history logging |
| `mni_client.py` | Use shared `sanitize_filename`, fix metric, re-raise disk-full in `_save_document` |
| `pje_session.py` | Use shared `sanitize_filename`/`unique_path`, fix `read_text` encoding, atomic+0600 session write, sanitize `dl.suggested_filename` |
| `batch_downloader.py` | Use shared `sanitize_filename`, atomic `_report.json` write |
| `worker.py` | Fix `_acquire_session_lock`, use shared `sanitize_filename`/`unique_path`, health cache, bind 127.0.0.1 |
| `gdrive_downloader.py` | Sanitize Content-Disposition filename, stream timeout |
| `tests/test_config.py` | Tests for new shared utilities |
| `tests/test_dashboard_api.py` | Tests for API key middleware, eviction |

---

## SPRINT 2: Security Hardening

### Task 1: Credential rotation + .gitignore audit (C1)

**Files:**
- Modify: `.env`
- Verify: `.gitignore`

- [ ] **Step 1: Verify .env is in .gitignore**

Run: `grep -n "^\.env$" .gitignore`
Expected: Match found (already present). If NOT found, add it.

- [ ] **Step 2: Check if .env was ever committed to git history**

Run: `git log --all --diff-filter=A -- .env`
If output is non-empty, the credentials are in git history. Note: the file was committed in `a0c6f08` (the cleanup commit that added Zone.Identifier files). This needs to be addressed.

- [ ] **Step 3: Rotate credentials**

Replace `.env` contents — set new MNI password (user must provide), new Redis password. Do NOT commit this file.

- [ ] **Step 4: Remove .env from git tracking**

```bash
git rm --cached .env 2>/dev/null || true
git rm --cached ".env:Zone.Identifier" 2>/dev/null || true
```

- [ ] **Step 5: Commit**

```bash
git add .gitignore
git commit -m "security: remove .env from tracking — credentials must be rotated"
```

> **IMPORTANT:** After this commit, Felipe must rotate the MNI password via the tribunal portal and update the Redis password in the Docker deployment.

---

### Task 2: API key middleware for POST endpoints (C2)

**Files:**
- Modify: `dashboard_api.py`
- Modify: `config.py`
- Test: `tests/test_dashboard_api.py`

- [ ] **Step 1: Add DASHBOARD_API_KEY to config.py**

After line 64 (`DASHBOARD_PORT`), add:

```python
DASHBOARD_API_KEY = os.getenv("DASHBOARD_API_KEY", "")
```

- [ ] **Step 2: Add auth middleware to dashboard_api.py**

After the `rate_limit_middleware` function, add:

```python
@web.middleware
async def api_key_middleware(request: web.Request, handler):
    """Require API key for mutating endpoints. Skipped when DASHBOARD_API_KEY is empty."""
    from config import DASHBOARD_API_KEY

    if not DASHBOARD_API_KEY:
        return await handler(request)  # No key configured = dev mode

    if request.method != "POST":
        return await handler(request)

    provided = request.headers.get("X-API-Key", "")
    if not provided or not hmac.compare_digest(provided, DASHBOARD_API_KEY):
        return web.json_response({"error": "Unauthorized"}, status=401)

    return await handler(request)
```

Add `import hmac` at the top of the file.

- [ ] **Step 3: Register middleware in create_app**

In `create_app()`, add to the middleware stack (after rate_limit):

```python
    app.middlewares.append(api_key_middleware)
```

- [ ] **Step 4: Write tests**

Add to `tests/test_dashboard_api.py`:

```python
class TestApiKeyMiddleware:
    async def test_no_key_configured_allows_all(self, aiohttp_client, tmp_path):
        """When DASHBOARD_API_KEY is empty, all requests pass through."""
        import dashboard_api
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr("config.DASHBOARD_API_KEY", "")
        app = dashboard_api.create_app(tmp_path)
        client = await aiohttp_client(app)
        resp = await client.post("/api/download", json={"processos": []})
        assert resp.status != 401
        monkeypatch.undo()

    async def test_wrong_key_returns_401(self, aiohttp_client, tmp_path):
        """Wrong API key returns 401."""
        import dashboard_api
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr("config.DASHBOARD_API_KEY", "correct-key")
        app = dashboard_api.create_app(tmp_path)
        client = await aiohttp_client(app)
        resp = await client.post(
            "/api/download",
            json={"processos": ["0001234-56.2024.8.08.0020"]},
            headers={"X-API-Key": "wrong-key"},
        )
        assert resp.status == 401
        monkeypatch.undo()

    async def test_correct_key_passes(self, aiohttp_client, tmp_path):
        """Correct API key passes through to handler."""
        import dashboard_api
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr("config.DASHBOARD_API_KEY", "correct-key")
        app = dashboard_api.create_app(tmp_path)
        client = await aiohttp_client(app)
        resp = await client.post(
            "/api/download",
            json={"processos": []},
            headers={"X-API-Key": "correct-key"},
        )
        assert resp.status != 401  # May be 400 (empty processos) but not 401
        monkeypatch.undo()
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_dashboard_api.py -v`

- [ ] **Step 6: Commit**

```bash
git add config.py dashboard_api.py tests/test_dashboard_api.py
git commit -m "security: add API key middleware for POST endpoints (C2)"
```

---

### Task 3: Fix _acquire_session_lock (C3)

**Files:**
- Modify: `worker.py:107-120`

- [ ] **Step 1: Fix — separate ImportError from OSError**

Replace lines 107-120:

```python
    def _acquire_session_lock(self) -> bool:
        """Acquire advisory lock on session state file (prevents multi-instance corruption)."""
        lock_path = SESSION_STATE_PATH.with_suffix(".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            import fcntl

            self._session_lock_fh = open(lock_path, "w")
            fcntl.flock(self._session_lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except ImportError:
            # fcntl unavailable (Windows) — proceed without locking
            log.warning("pje.session.lock_unavailable", reason="fcntl not available")
            return True
        except OSError as exc:
            # Lock held by another process — do NOT proceed
            log.error("pje.session.lock_held", path=str(lock_path), error=str(exc))
            if self._session_lock_fh:
                self._session_lock_fh.close()
                self._session_lock_fh = None
            return False
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_worker.py -v`

- [ ] **Step 3: Commit**

```bash
git add worker.py
git commit -m "security: fix _acquire_session_lock — reject when lock held by another process (C3)"
```

---

### Task 4: Remove info disclosure from API responses (H4-H7)

**Files:**
- Modify: `dashboard_api.py`

- [ ] **Step 1: Remove output_dir from /api/status**

Replace line 280:
```python
            "output_dir": str(state.output_dir.resolve()),
```
With:
```python
            "output_dir": state.output_dir.name,  # Only dirname, not full path
```

- [ ] **Step 2: Remove session_file path from /api/session/status**

Replace line 484:
```python
            "session_file": str(SESSION_FILE),
```
With removal (delete the line entirely).

- [ ] **Step 3: Sanitize error in /api/session/verify**

Replace line 503:
```python
        return web.json_response({"valid": False, "error": str(exc)}, status=500)
```
With:
```python
        return web.json_response({"valid": False, "error": "Erro interno na verificação"}, status=500)
```

- [ ] **Step 4: Run tests + commit**

```bash
pytest tests/ -q
git add dashboard_api.py
git commit -m "security: remove info disclosure from API responses (H4-H7)"
```

---

### Task 5: Path traversal in Playwright + GDrive filenames (H2, H3)

**Files:**
- Modify: `pje_session.py:298-300`
- Modify: `gdrive_downloader.py:199-205`

- [ ] **Step 1: Fix pje_session.py — sanitize dl.suggested_filename**

Replace lines 298-300:
```python
                dl = await dl_info.value
                dest = output_dir / dl.suggested_filename
                await dl.save_as(dest)
```
With:
```python
                dl = await dl_info.value
                safe_name = _safe_filename(dl.suggested_filename or "download.pdf")
                dest = _unique_path(output_dir / safe_name)
                if not dest.resolve().is_relative_to(output_dir.resolve()):
                    raise ValueError(f"Path traversal in filename: {dl.suggested_filename}")
                await dl.save_as(dest)
```

- [ ] **Step 2: Fix gdrive_downloader.py — sanitize Content-Disposition filename**

After line 203 (`filename = filename_match.group(1).strip()`), add:
```python
                    # Sanitize to prevent path traversal
                    filename = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', filename).strip()[:120]
                    if '..' in filename or filename.startswith('/'):
                        filename = f"gdrive_{file_id}.pdf"
```

- [ ] **Step 3: Run tests + commit**

```bash
pytest tests/ -q
git add pje_session.py gdrive_downloader.py
git commit -m "security: sanitize Playwright + GDrive filenames to prevent path traversal (H2, H3)"
```

---

### Task 6: Session file permissions + encoding fix (H1, H14)

**Files:**
- Modify: `pje_session.py:99-101, 129`

- [ ] **Step 1: Fix session file write with 0600 permissions**

Replace line 101:
```python
            session_file.write_text(json.dumps(state, indent=2, ensure_ascii=False))
```
With:
```python
            import os as _os
            content = json.dumps(state, indent=2, ensure_ascii=False)
            fd = _os.open(str(session_file), _os.O_WRONLY | _os.O_CREAT | _os.O_TRUNC, 0o600)
            with _os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
```

- [ ] **Step 2: Fix _load_state encoding**

Replace line 129:
```python
        return json.loads(self.session_file.read_text())
```
With:
```python
        return json.loads(self.session_file.read_text(encoding="utf-8"))
```

- [ ] **Step 3: Run tests + commit**

```bash
pytest tests/ -q
git add pje_session.py
git commit -m "security: session file 0600 permissions + explicit UTF-8 encoding (H1, H14)"
```

---

### Task 7: Validate PJE_BASE_URL (M4)

**Files:**
- Modify: `config.py:39`

- [ ] **Step 1: Add URL validation**

Replace line 39:
```python
PJE_BASE_URL = os.getenv("PJE_BASE_URL", "https://pje.tjes.jus.br/pje")
```
With:
```python
_pje_url = os.getenv("PJE_BASE_URL", "https://pje.tjes.jus.br/pje")
if _pje_url != "https://pje.tjes.jus.br/pje" and (
    not _pje_url.startswith("https://") or ".jus.br" not in _pje_url
):
    raise ValueError(f"PJE_BASE_URL must be HTTPS .jus.br URL, got: {_pje_url}")
PJE_BASE_URL = _pje_url
```

- [ ] **Step 2: Run tests + commit**

```bash
pytest tests/ -q
git add config.py
git commit -m "security: validate PJE_BASE_URL is HTTPS .jus.br domain (M4)"
```

---

## SPRINT 3: Resilience + DRY

### Task 8: Consolidate sanitize_filename + unique_path in config.py (H13)

**Files:**
- Modify: `config.py`
- Modify: `mni_client.py` (remove `_sanitize_filename`)
- Modify: `pje_session.py` (remove `_safe_filename`, `_unique_path`)
- Modify: `batch_downloader.py` (use shared `sanitize_filename`)
- Modify: `worker.py` (use shared functions)
- Modify: `tests/test_config.py` (add tests)
- Modify: `tests/test_pje_session.py` (update imports)

- [ ] **Step 1: Add canonical functions to config.py**

After `is_valid_processo`, add:

```python
def sanitize_filename(name: str, maxlen: int = 100) -> str:
    """Sanitize a string for use as a filename. Strips dangerous chars, limits length."""
    sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    return sanitized[:maxlen].strip(". ")


def unique_path(path: Path) -> Path:
    """Return a non-colliding path by appending _1, _2, etc. if path exists."""
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    i = 1
    while (path.parent / f"{stem}_{i}{suffix}").exists():
        i += 1
    return path.parent / f"{stem}_{i}{suffix}"


def atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Write text to file atomically via tmp+rename."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding=encoding)
    tmp.replace(path)
```

- [ ] **Step 2: Write tests for new functions**

Add to `tests/test_config.py`:

```python
from config import sanitize_filename, unique_path, atomic_write_text


class TestSanitizeFilename:
    def test_strips_dangerous_chars(self):
        assert sanitize_filename('doc:name/with\\bad*chars') == "doc_name_with_bad_chars"

    def test_strips_null_and_control(self):
        assert sanitize_filename("file\x00name\x1f.pdf") == "file_name_.pdf"

    def test_length_limited(self):
        assert len(sanitize_filename("a" * 200)) <= 100

    def test_custom_maxlen(self):
        assert len(sanitize_filename("a" * 200, maxlen=50)) <= 50

    def test_strips_edge_dots(self):
        assert sanitize_filename("...file...") == "file"

    def test_empty(self):
        assert sanitize_filename("") == ""


class TestUniquePath:
    def test_no_collision(self, tmp_path):
        p = tmp_path / "file.pdf"
        assert unique_path(p) == p

    def test_collision(self, tmp_path):
        p = tmp_path / "file.pdf"
        p.write_bytes(b"x")
        assert unique_path(p) == tmp_path / "file_1.pdf"


class TestAtomicWriteText:
    def test_writes_content(self, tmp_path):
        p = tmp_path / "test.json"
        atomic_write_text(p, '{"key": "value"}')
        assert p.read_text() == '{"key": "value"}'

    def test_no_tmp_file_left(self, tmp_path):
        p = tmp_path / "test.json"
        atomic_write_text(p, "content")
        assert not (tmp_path / "test.json.tmp").exists()
```

- [ ] **Step 3: Update mni_client.py — import from config, remove local _sanitize_filename**

Replace `_sanitize_filename` usage:
```python
from config import sanitize_filename as _sanitize_filename
```
Remove the `_sanitize_filename` function definition (lines 809-816).

- [ ] **Step 4: Update pje_session.py — import from config, remove locals**

Replace local `_safe_filename` and `_unique_path` with:
```python
from config import PJE_BASE_URL, SESSION_STATE_PATH, sanitize_filename, unique_path
```
Remove the function definitions. Update all call sites: `_safe_filename(x)` → `sanitize_filename(x, maxlen=120)`, `_unique_path(p)` → `unique_path(p)`.

Update `tests/test_pje_session.py` imports accordingly.

- [ ] **Step 5: Update batch_downloader.py — use shared sanitize_filename**

Replace the inline `re.sub(r'[<>:"/\\|?*]', "_", numero)` (line 362) with:
```python
from config import sanitize_filename
safe_name = sanitize_filename(numero)
```

- [ ] **Step 6: Update worker.py — use shared functions**

Replace the inline `re.sub(r'[<>:"/\\|?*\.\s]+', "_", ...)` with import from config. Replace `_unique_filename` with `unique_path`.

- [ ] **Step 7: Run tests + commit**

```bash
pytest tests/ -q
ruff check --fix && ruff format
git add config.py mni_client.py pje_session.py batch_downloader.py worker.py tests/test_config.py tests/test_pje_session.py
git commit -m "refactor: consolidate sanitize_filename + unique_path + atomic_write in config.py (H13)"
```

---

### Task 9: Batch eviction policy (C4)

**Files:**
- Modify: `dashboard_api.py`

- [ ] **Step 1: Add MAX_HISTORY and eviction to DashboardState**

Add constant after `MAX_BATCH_SIZE`:
```python
MAX_BATCH_HISTORY = 100  # max completed batches kept in memory
```

Add eviction method to `DashboardState`:
```python
    def _evict_old_batches(self) -> None:
        """Remove oldest completed batches when history exceeds limit."""
        completed = [
            (bid, job) for bid, job in self.batches.items()
            if job.status in ("done", "failed") and bid != self.current_batch_id
        ]
        if len(completed) <= MAX_BATCH_HISTORY:
            return
        # Sort by finished_at, remove oldest
        completed.sort(key=lambda x: x[1].finished_at or "")
        to_remove = len(completed) - MAX_BATCH_HISTORY
        for bid, _ in completed[:to_remove]:
            del self.batches[bid]
        log.info("dashboard.evicted_batches", count=to_remove)
```

Call `self._evict_old_batches()` at the end of `_load_history()` and at the end of `_run_batch()` (after setting job.status).

- [ ] **Step 2: Run tests + commit**

```bash
pytest tests/ -q
git add dashboard_api.py
git commit -m "fix: add batch eviction policy (max 100 in memory) to prevent OOM (C4)"
```

---

### Task 10: Atomic _report.json write (C5)

**Files:**
- Modify: `batch_downloader.py:587-590`

- [ ] **Step 1: Replace write_text with atomic_write_text**

Replace lines 587-590:
```python
    report_path = output_dir / "_report.json"
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
```
With:
```python
    from config import atomic_write_text

    report_path = output_dir / "_report.json"
    atomic_write_text(report_path, json.dumps(report, indent=2, ensure_ascii=False))
```

- [ ] **Step 2: Run tests + commit**

```bash
pytest tests/ -q
git add batch_downloader.py
git commit -m "fix: atomic write for _report.json to prevent corruption on crash (C5)"
```

---

### Task 11: Fix download_documentos metric + _save_document disk-full (H9, H10)

**Files:**
- Modify: `mni_client.py`

- [ ] **Step 1: Fix metric — condition on saved_files**

Replace lines 697-699:
```python
        metrics.mni_requests_total.labels(
            operation="download_documentos", status="success"
        ).inc()
```
With:
```python
        _status = "success" if saved_files else "no_docs_saved"
        metrics.mni_requests_total.labels(
            operation="download_documentos", status=_status
        ).inc()
```

- [ ] **Step 2: Fix _save_document — re-raise OSError (disk-full)**

Replace lines 741-748:
```python
        except Exception as exc:
            log.warning(
                "mni.download.save_failed",
                doc_id=doc.id,
                nome=doc.nome,
                error=str(exc),
            )
            return None
```
With:
```python
        except OSError as exc:
            log.error(
                "mni.download.save_failed_disk",
                doc_id=doc.id,
                error=str(exc),
            )
            raise  # Disk-full must propagate — don't silently skip
        except Exception as exc:
            log.warning(
                "mni.download.save_failed",
                doc_id=doc.id,
                nome=doc.nome,
                error=str(exc),
            )
            return None
```

- [ ] **Step 3: Run tests + commit**

```bash
pytest tests/ -q
git add mni_client.py
git commit -m "fix: condition download metric on saved_files + re-raise disk-full (H9, H10)"
```

---

### Task 12: Health endpoint cache + bind localhost (H7, H8)

**Files:**
- Modify: `worker.py`

- [ ] **Step 1: Add health cache to __init__**

In `PJeSessionWorker.__init__`, add:
```python
        self._health_cache: dict | None = None
        self._health_cache_time: float = 0.0
        self._health_cache_ttl: float = 30.0  # seconds
```

- [ ] **Step 2: Cache MNI health check result**

In `_health_handler`, replace the MNI check block (lines 967-977) with:

```python
        # MNI connectivity (cached 30s)
        now = time.monotonic()
        if self.mni_client is not None:
            if self._health_cache and (now - self._health_cache_time) < self._health_cache_ttl:
                checks["mni"] = self._health_cache.get("status", "unknown")
            else:
                try:
                    mni_health = await asyncio.wait_for(
                        self.mni_client.health_check(), timeout=5.0
                    )
                    self._health_cache = mni_health
                    self._health_cache_time = now
                    checks["mni"] = mni_health["status"]
                except Exception:
                    checks["mni"] = "unreachable"
            # MNI status does NOT affect overall healthy — worker can still process from Redis
        else:
            checks["mni"] = "disabled"
```

Add `import time` at the top of the method if not already imported.

- [ ] **Step 3: Bind to 127.0.0.1**

Replace line 954:
```python
        site = web.TCPSite(runner, "0.0.0.0", HEALTH_PORT)
```
With:
```python
        site = web.TCPSite(runner, "127.0.0.1", HEALTH_PORT)
```

**NOTE:** The docker-compose.yml may need updating if the health check is called from outside the container. If Docker healthcheck calls `localhost:8006`, binding to `127.0.0.1` inside the container is fine. If an external orchestrator needs it, keep `0.0.0.0` but add the API key middleware.

- [ ] **Step 4: Remove last_error from health response**

In the health response dict (around line 1010), remove:
```python
            "last_error": self._last_error,
```

- [ ] **Step 5: Run tests + commit**

```bash
pytest tests/ -q
git add worker.py
git commit -m "fix: health endpoint — cache MNI check, bind localhost, remove internal details (H7, H8)"
```

---

### Task 13: Rate limiter X-Forwarded-For + stream timeout (H11, H12)

**Files:**
- Modify: `dashboard_api.py`
- Modify: `gdrive_downloader.py`

- [ ] **Step 1: Fix rate limiter — parse X-Forwarded-For**

In `rate_limit_middleware`, replace:
```python
    ip = request.remote or "unknown"
```
With:
```python
    # Trust X-Forwarded-For from Docker bridge (first hop only)
    forwarded = request.headers.get("X-Forwarded-For", "")
    ip = forwarded.split(",")[0].strip() if forwarded else (request.remote or "unknown")
```

- [ ] **Step 2: Fix GDrive stream timeout**

In `gdrive_downloader.py`, in the `_try_requests_parse` function, wrap the `_stream_to_disk` call with `asyncio.wait_for`:

Replace:
```python
                total_bytes = await asyncio.to_thread(_stream_to_disk, dl_resp, dest)
```
With:
```python
                try:
                    total_bytes = await asyncio.wait_for(
                        asyncio.to_thread(_stream_to_disk, dl_resp, dest),
                        timeout=300,  # 5 min max per file
                    )
                except asyncio.TimeoutError:
                    log.warning("gdrive.requests.stream_timeout", file_id=file_id)
                    dest.unlink(missing_ok=True)  # Clean up partial file
                    continue
```

- [ ] **Step 3: Run tests + commit**

```bash
pytest tests/ -q
git add dashboard_api.py gdrive_downloader.py
git commit -m "fix: rate limiter X-Forwarded-For + GDrive stream 5min timeout (H11, H12)"
```

---

### Task 14: Fix double lock acquire + _load_history logging (H15, L5)

**Files:**
- Modify: `worker.py`
- Modify: `dashboard_api.py`

- [ ] **Step 1: Fix double lock in load_session**

In `worker.py`, find the second `self._acquire_session_lock()` call (around line 236 in `load_session`). Remove it — the lock was already acquired at line 190. If line 190 was skipped (no session file), add the lock acquire before the manual-login save path instead:

The fix depends on the exact flow. Read `load_session()` fully and ensure `_acquire_session_lock()` is called exactly once before any write, and `_release_session_lock()` in the corresponding finally/cleanup.

- [ ] **Step 2: Fix _load_history logging**

In `dashboard_api.py`, replace line 100-101:
```python
            except Exception:
                pass
```
With:
```python
            except Exception as exc:
                log.warning("dashboard.history.load_failed", file=str(report_file), error=str(exc))
```

- [ ] **Step 3: Run tests + commit**

```bash
pytest tests/ -q
git add worker.py dashboard_api.py
git commit -m "fix: single lock acquire in load_session + log _load_history errors (H15, L5)"
```

---

### Task 15: Final verification + docs update

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/ -q`
Expected: ≥110 tests PASS

- [ ] **Step 2: Run linter**

Run: `ruff check --fix && ruff format`
Then: `ruff check && ruff format --check`

- [ ] **Step 3: Update CLAUDE.md**

Update test count and sprint status. Mark Sprint 2+3 as DONE.

- [ ] **Step 4: Commit + push**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md after Sprint 2+3 — security + resilience hardening"
git push origin master
```
