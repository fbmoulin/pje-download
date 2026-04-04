# Sprint 5: CNJ 615/2025 Audit Trail + HARD Test Coverage

> **Project:** pje-download v1.6 → v1.7
> **Date:** 2026-04-04
> **Status:** APPROVED
> **Approach:** A (dedicated audit module) + X (mock-heavy unit tests)

---

## 1. Goals

1. **CNJ 615/2025 Audit Trail** — structured, append-only JSON-L log of every document access, ready for future Supabase migration
2. **HARD Test Coverage** — raise symbol coverage from ~68% to ~85% by testing SOAP, Playwright (smoke), and Redis-adjacent symbols

### Success Criteria

- [ ] Every document save (MNI, PJe API, PJe browser, GDrive) produces an audit entry
- [ ] Audit log is append-only JSON-L, one file per day, 0600 permissions
- [ ] `audit.py` has 100% test coverage
- [ ] Symbol coverage reaches >=82% (target 85%)
- [ ] All 183 existing tests still pass
- [ ] `ruff check` and `ruff format` clean

---

## 2. CNJ 615/2025 Audit Trail Design

### 2.1 New Module: `audit.py`

Location: `pje-download/audit.py` (same level as config.py, mni_client.py)

#### Schema

```python
@dataclass
class AuditEntry:
    # Identifiers
    timestamp: str              # ISO 8601 UTC (auto-filled)
    event_type: str             # Literal["document_saved", "batch_started", "batch_completed", "session_login"]

    # Document context
    processo_numero: str        # CNJ format
    documento_id: str | None    # MNI doc ID or None
    documento_tipo: str | None  # "sentenca", "peticao", "despacho", etc.
    documento_nome: str | None  # sanitized filename

    # Provenance
    fonte: str                  # Literal["mni_soap", "pje_api", "pje_browser", "google_drive"]
    tribunal: str               # "TJES", "TJBA", "TJCE", "TRT17"

    # Integrity
    tamanho_bytes: int | None
    checksum_sha256: str | None

    # Request context
    batch_id: str | None
    client_ip: str | None       # from X-Forwarded-For (dashboard requests)
    api_key_hash: str | None    # SHA256[:16] of API key, never the key itself

    # Outcome
    status: str                 # Literal["success", "error", "duplicate_skipped"]
    erro: str | None
    duracao_s: float | None
```

#### Public API

```python
def log_access(entry: AuditEntry) -> None:
    """Append entry as JSON line to daily audit file.
    Thread-safe via threading.Lock. Creates dir if needed.
    File permissions: 0600. Encoding: UTF-8."""

def get_audit_dir() -> Path:
    """Return AUDIT_LOG_DIR env var or default Path('/data/audit')."""

def rotate_logs(max_days: int = 90) -> int:
    """Delete audit-*.jsonl files older than max_days. Returns count deleted."""
```

#### File Format

- Path: `{AUDIT_LOG_DIR}/audit-YYYY-MM-DD.jsonl`
- One JSON object per line, no pretty-printing
- Append-only (open with `"a"`)
- File created with `os.open(..., 0o600)` on first write of the day
- Thread-safe: module-level `threading.Lock`

#### Environment Variable

```bash
AUDIT_LOG_DIR=/data/audit  # default, overridable
```

### 2.2 Instrumentation Points

| # | Module | Function | Anchor | Event Type | Context Available |
|---|--------|----------|--------|------------|-------------------|
| 1 | `mni_client.py` | `_save_document()` | after L728 (write_bytes) | `document_saved` | doc.id, doc.tipo, doc.nome, checksum, size, processo from parent scope, self.tribunal |
| 2 | `pje_session.py` | `_try_api()` | after L255 (write_bytes) / L264 (log) | `document_saved` | doc name, size, numero, fonte="pje_api", tribunal=config.MNI_TRIBUNAL |
| 3 | `pje_session.py` | `_try_browser()` | after L322 (append block) | `document_saved` | filename, stat().st_size, numero, fonte="pje_browser", tribunal=config.MNI_TRIBUNAL |
| 4 | `gdrive_downloader.py` | `_try_requests_parse()` | after L257 (files.append _file_info) | `document_saved` | filename, size from _file_info, folder_id, fonte="google_drive", tribunal=config.MNI_TRIBUNAL |
| 5 | `gdrive_downloader.py` | `_try_playwright_download()` | after download.save_as in loop | `document_saved` | filename, size, fonte="google_drive", tribunal=config.MNI_TRIBUNAL |
| 6 | `batch_downloader.py` | `download_batch()` | L335 (start) | `batch_started` | batch_id, processos list, include_anexos |
| 7 | `batch_downloader.py` | `download_batch()` | after L554 (log), before L564 (report) | `batch_completed` | batch_id, total_docs, total_bytes, done, failed, elapsed |
| 8 | `dashboard_api.py` | `handle_session_login()` | after login success | `session_login` | client_ip, tribunal=config.MNI_TRIBUNAL |

#### Instrumentation Pattern

Each call site adds 3-5 lines. All `AuditEntry` fields except `timestamp` (auto-filled) default to `None`.

**Example: `_save_document()` in `mni_client.py`** (MNI has full context):

```python
import audit
audit.log_access(audit.AuditEntry(
    event_type="document_saved",
    processo_numero=processo_numero,
    documento_id=doc.id,
    documento_tipo=doc.tipo,
    documento_nome=safe_name,
    fonte="mni_soap",
    tribunal=self.tribunal,
    tamanho_bytes=len(content),
    checksum_sha256=checksum,
    status="success",
))
```

**Example: `_try_api()` in `pje_session.py`** (less context, no checksum):

```python
import audit, config
audit.log_access(audit.AuditEntry(
    event_type="document_saved",
    processo_numero=numero,
    documento_nome=nome,
    fonte="pje_api",
    tribunal=config.MNI_TRIBUNAL,  # fallback: from global config
    tamanho_bytes=len(content),
    checksum_sha256=None,  # Phase 1: no checksum for non-MNI sources
    status="success",
))
```

#### Design Decisions

- **`tribunal` field**: MNI sources use `self.tribunal`. Non-MNI sources (pje_session, gdrive) use `config.MNI_TRIBUNAL` as fallback since the app is configured per-tribunal.
- **`checksum_sha256`**: Only available for MNI sources (computed at save time). For PJe browser/API and GDrive, set to `None` in Phase 1. Phase 2 can add incremental hashing via `hashlib.file_digest()` on the saved file.
- **`os.open` pattern for 0600**: Use `fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)` then `os.fdopen(fd, "a")`. Never use bare `open(path, "a")` which inherits umask.
- **`rotate_logs()` thread safety**: Must acquire the same module-level lock. Only deletes files for dates strictly before `today - max_days` (never today's file).

### 2.3 Future Supabase Migration Path

When scaling:
1. Add `supabase` dependency
2. Create `audit_entries` table mirroring `AuditEntry` schema
3. Add async batch uploader in `audit.py` (read JSON-L, POST to Supabase, mark uploaded)
4. JSON-L remains as local fallback (write-ahead log pattern)

No code changes needed in the 4 instrumented modules — only `audit.py` internals change.

---

## 3. HARD Test Coverage Design

### 3.1 New Fixtures in `conftest.py`

#### `mock_playwright`

```python
@pytest.fixture
def mock_playwright():
    """Full Playwright mock chain: playwright -> browser -> context -> page."""
    page = AsyncMock()
    page.url = "https://pje.tjes.jus.br/pje/painel.seam"
    page.goto = AsyncMock()
    page.content = AsyncMock(return_value="<html></html>")
    page.locator = MagicMock()
    page.wait_for_url = AsyncMock()
    page.request = AsyncMock()  # for page.request.get()

    download = AsyncMock()
    download.suggested_filename = "documento.pdf"
    download.save_as = AsyncMock()
    page.expect_download = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=download),
        __aexit__=AsyncMock(return_value=False),
    ))

    ctx = AsyncMock()
    ctx.new_page = AsyncMock(return_value=page)
    ctx.storage_state = AsyncMock(return_value={"cookies": [], "origins": []})

    browser = AsyncMock()
    browser.new_context = AsyncMock(return_value=ctx)
    browser.close = AsyncMock()

    pw = AsyncMock()
    pw.chromium.launch = AsyncMock(return_value=browser)

    pw_cm = AsyncMock()
    pw_cm.__aenter__ = AsyncMock(return_value=pw)
    pw_cm.__aexit__ = AsyncMock(return_value=False)

    return pw_cm, pw, browser, ctx, page, download
```

#### `mock_zeep_client`

```python
@pytest.fixture
def mock_zeep_client():
    """Zeep SOAP client mock with consultarProcesso response variants."""
    client = MagicMock()

    # Success response
    resp_ok = MagicMock()
    resp_ok.sucesso = True
    resp_ok.mensagem = ""
    resp_ok.processo = MagicMock()
    resp_ok.processo.dadosBasicos.numero = "0001234-56.2024.8.08.0001"
    resp_ok.processo.dadosBasicos.classeProcessual = "Execucao"
    resp_ok.processo.documento = [
        MagicMock(id="DOC001", nome="Sentenca", tipo="sentenca",
                  mimetype="application/pdf", conteudo=b64encode(b"PDF"),
                  tamanho=1024),
    ]

    client.service.consultarProcesso.return_value = resp_ok
    client._resp_ok = resp_ok  # for test customization

    return client
```

#### `mock_redis`

```python
@pytest.fixture
def mock_redis():
    """Async Redis mock with queue operations."""
    r = AsyncMock()
    r.ping = AsyncMock(return_value=True)
    r.brpop = AsyncMock(return_value=("pje:jobs", b'{"jobId":"J1","numeroProcesso":"001"}'))
    r.lpush = AsyncMock()
    r.set = AsyncMock()
    r.get = AsyncMock(return_value=None)
    r.close = AsyncMock()
    return r
```

### 3.2 Test Targets by Priority

#### Priority 1 — Easy/Medium (7 symbols, +20-25 tests)

| Symbol | File | Tests to Write |
|--------|------|----------------|
| `_save_document()` full | mni_client.py | checksum match, dedup skip, OSError propagation, base64 decode |
| `_parse_processo()` | mni_client.py | TJES response, TJBA variant, missing attrs, empty documentos |
| `_acquire_session_lock()` | worker.py | success, lock already held, no fcntl (Windows) |
| `_release_session_lock()` | worker.py | normal release, fh is None |
| `_on_cleanup()` | dashboard_api.py | CancelledError handling, batch task cancel |
| `_try_gdown()` | gdrive_downloader.py | success, module not installed, download fail |
| `health_check()` | mni_client.py | healthy response, WSDL timeout |

#### Priority 2 — SOAP Hard (5 symbols, +15-20 tests)

| Symbol | File | Tests to Write |
|--------|------|----------------|
| `consultar_processo()` | mni_client.py | success, mni_error, not_found, auth_failed, 403, timeout |
| `_call_consultar_processo()` | mni_client.py | normal call, Fault exception, generic exception |
| `download_documentos()` | mni_client.py | single batch, multi-batch, dedup, phase1 only, phase2 fetch |
| `_get_client()` | mni_client.py | first call (create), cached call, WSDL fetch failure |
| `_run_batch()` | dashboard_api.py | success completion, exception recovery, progress update |

#### Priority 3 — Playwright Smoke (9 symbols, +10-12 tests)

| Symbol | File | Tests to Write |
|--------|------|----------------|
| `interactive_login()` | pje_session.py | browser launched, session saved |
| `is_valid()` | pje_session.py | valid session, expired |
| `download_processo()` | pje_session.py | api path, browser fallback |
| `_try_api()` | pje_session.py | success, auth fail |
| `_try_browser()` | pje_session.py | download triggered |
| `load_session()` | worker.py | session loaded, expired → re-login |
| `_download_via_browser()` | worker.py | docs downloaded |
| `_try_playwright_download()` | gdrive_downloader.py | success, timeout |
| `handle_session_login()` | dashboard_api.py | login started |

### 3.3 New Test File

- `tests/test_audit.py` — dedicated tests for `audit.py`:
  - `test_log_access_creates_file` — first write creates daily file
  - `test_log_access_appends` — second write appends, not overwrites
  - `test_log_access_daily_rotation_filename` — correct date in filename
  - `test_log_access_file_permissions` — 0600 on Unix
  - `test_log_access_thread_safety` — concurrent writes don't corrupt
  - `test_log_access_schema_fields` — all fields serialized to JSON
  - `test_rotate_logs_deletes_old` — files older than max_days removed
  - `test_rotate_logs_keeps_recent` — recent files preserved
  - `test_get_audit_dir_default` — returns /data/audit when no env
  - `test_get_audit_dir_custom` — reads AUDIT_LOG_DIR env var

### 3.4 Coverage Projection

| Phase | Tests Added | Cumulative | Symbol Coverage |
|-------|-----------|------------|-----------------|
| Current | 183 | 183 | ~68% |
| Audit module | +10 | 193 | ~70% |
| P1 easy/medium | +20-25 | ~215 | ~76% |
| P2 SOAP hard | +15-20 | ~233 | ~82% |
| P3 Playwright smoke | +10-12 | ~245 | ~85% |
| **Total** | **+55-67** | **~245** | **~85%** |

> **Note:** Coverage percentages are estimates. The hard floor is >=82% (success criteria). 85% is the stretch goal.

---

## 4. Files Changed

### New Files
- `audit.py` — audit trail module (~80 lines)
- `tests/test_audit.py` — audit tests (~120 lines)

### Modified Files
- `conftest.py` — add 3 fixtures (mock_playwright, mock_zeep_client, mock_redis)
- `mni_client.py` — add audit.log_access() call in _save_document() (+5 lines)
- `pje_session.py` — add audit.log_access() in _try_api() and _try_browser() (+10 lines)
- `gdrive_downloader.py` — add audit.log_access() in 2 save points (+10 lines)
- `batch_downloader.py` — add audit.log_access() for batch_started/completed (+10 lines)
- `config.py` — add AUDIT_LOG_DIR constant (+2 lines)
- `tests/test_mni_client.py` — expand with SOAP mock tests (+~100 lines)
- `tests/test_worker.py` — expand with lock + Playwright smoke (+~60 lines)
- `tests/test_pje_session.py` — expand with Playwright smoke (+~50 lines)
- `tests/test_gdrive_downloader.py` — expand with gdown + Playwright smoke (+~40 lines)
- `tests/test_dashboard_api.py` — expand with _run_batch + cleanup (+~30 lines)
- `CLAUDE.md` — update test count, add audit section

### Not Changed
- `metrics.py` — no audit metrics (keep it simple)
- `docker-compose.yml` — audit dir created by code, no volume needed yet
- `worker.py` — only test additions, no audit instrumentation (worker delegates to mni_client/pje_session)

---

## 5. Constraints

- Zero new dependencies (stdlib only: `dataclasses`, `json`, `threading`, `os`, `pathlib`, `datetime`)
- Audit writes must not block or slow down downloads (sync append is fast enough for single-process)
- Audit schema is forward-compatible with Supabase table (field names = column names)
- All existing 183 tests must continue to pass
- `ruff check` + `ruff format` clean before commit
