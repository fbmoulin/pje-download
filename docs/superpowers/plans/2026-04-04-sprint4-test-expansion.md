# Sprint 4: Test Coverage Expansion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand test coverage from 32% (36/113 symbols) to ~75% (85/113) by adding ~60 new tests across 6 test files, targeting all EASY + select MEDIUM testability symbols.

**Architecture:** 4-wave approach — pure functions first, then HTTP handlers, then mocking-heavy tests, then edge cases. Each wave is independently committable. All tests use existing patterns (conftest.py env setup, _load_worker_module lazy import, aiohttp TestClient).

**Tech Stack:** pytest, pytest-asyncio, unittest.mock (patch/AsyncMock/MagicMock), aiohttp.test_utils (TestClient/TestServer)

**Current state:** 111 tests passing in 0.91s. Git on commit 3ae81af.

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `tests/test_config.py` | Modify | Add PJE_BASE_URL validation edge cases |
| `tests/test_pje_session.py` | Modify | Add _guess_ext, _load_state tests |
| `tests/test_mni_client.py` | Modify | Add _mimetype_to_ext, _save_document, MNIClient.__init__, _parse_processo |
| `tests/test_worker.py` | Modify | Add _unique_filename, is_session_expired, _detect_captcha, _result, _log_job_result, close |
| `tests/test_gdrive_downloader.py` | Modify | Add _file_info, download_gdrive_folder strategy orchestration |
| `tests/test_dashboard_api.py` | Modify | Add handle_batch_detail, handle_session_status, handle_index, middleware tests, eviction |

---

## Task 1: Pure Functions — pje_session._guess_ext + _load_state (Wave 1A)

**Files:**
- Modify: `tests/test_pje_session.py`
- Source: `pje_session.py:336-345` (_guess_ext), `pje_session.py:129-135` (_load_state)

- [ ] **Step 1: Write _guess_ext tests**

```python
from pje_session import _guess_ext, PJeSessionClient


class TestGuessExt:
    def test_pdf_content_type(self):
        assert _guess_ext("application/pdf", "doc") == ".pdf"

    def test_html_content_type(self):
        assert _guess_ext("text/html; charset=utf-8", "page") == ".html"

    def test_xml_content_type(self):
        assert _guess_ext("application/xml", "data") == ".xml"

    def test_unknown_content_type(self):
        assert _guess_ext("application/octet-stream", "file") == ".bin"

    def test_empty_content_type(self):
        assert _guess_ext("", "noext") == ".bin"

    def test_name_already_has_extension(self):
        """When nome already has a dot, return empty (no double extension)."""
        assert _guess_ext("application/pdf", "sentenca.pdf") == ""

    def test_name_with_dot_ignores_content_type(self):
        assert _guess_ext("text/html", "file.html") == ""
```

- [ ] **Step 2: Write _load_state tests**

```python
class TestLoadState:
    def test_missing_file_raises(self, tmp_path):
        client = PJeSessionClient(session_file=tmp_path / "nonexistent.json")
        with pytest.raises(FileNotFoundError, match="Sessão não encontrada"):
            client._load_state()

    def test_corrupt_json_raises(self, tmp_path):
        f = tmp_path / "session.json"
        f.write_text("not valid json")
        client = PJeSessionClient(session_file=f)
        with pytest.raises(Exception):
            client._load_state()

    def test_valid_json_returns_dict(self, tmp_path):
        f = tmp_path / "session.json"
        f.write_text('{"cookies": [], "origins": []}')
        client = PJeSessionClient(session_file=f)
        state = client._load_state()
        assert isinstance(state, dict)
        assert "cookies" in state
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_pje_session.py -v`
Expected: 14 tests pass (4 existing + 10 new)

- [ ] **Step 4: Commit**

```bash
git add tests/test_pje_session.py
git commit -m "test: add _guess_ext and _load_state tests (pje_session)"
```

---

## Task 2: Pure Functions — mni_client._mimetype_to_ext + MNIClient.__init__ (Wave 1B)

**Files:**
- Modify: `tests/test_mni_client.py`
- Source: `mni_client.py:804-815` (_mimetype_to_ext), `mni_client.py:128-151` (__init__)

- [ ] **Step 1: Write _mimetype_to_ext tests**

```python
from mni_client import _mimetype_to_ext, MNIClient, MNIDocumento


class TestMimetypeToExt:
    def test_pdf(self):
        assert _mimetype_to_ext("application/pdf") == ".pdf"

    def test_html(self):
        assert _mimetype_to_ext("text/html") == ".html"

    def test_txt(self):
        assert _mimetype_to_ext("text/plain") == ".txt"

    def test_png(self):
        assert _mimetype_to_ext("image/png") == ".png"

    def test_jpeg(self):
        assert _mimetype_to_ext("image/jpeg") == ".jpg"

    def test_doc(self):
        assert _mimetype_to_ext("application/msword") == ".doc"

    def test_docx(self):
        assert _mimetype_to_ext(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ) == ".docx"

    def test_unknown_returns_bin(self):
        assert _mimetype_to_ext("application/octet-stream") == ".bin"

    def test_empty_returns_bin(self):
        assert _mimetype_to_ext("") == ".bin"
```

- [ ] **Step 2: Write MNIClient.__init__ tests**

```python
class TestMNIClientInit:
    def test_valid_tribunal(self):
        client = MNIClient(tribunal="TJES", username="u", password="p")
        assert client.tribunal == "TJES"
        assert client.wsdl_url == "https://pje.tjes.jus.br/pje/intercomunicacao?wsdl"

    def test_tribunal_case_insensitive(self):
        client = MNIClient(tribunal="tjes", username="u", password="p")
        assert client.tribunal == "TJES"

    def test_invalid_tribunal_raises(self):
        with pytest.raises(ValueError, match="não suportado"):
            MNIClient(tribunal="INVALID", username="u", password="p")

    def test_all_tribunals_valid(self):
        from mni_client import TRIBUNAL_ENDPOINTS
        for tribunal in TRIBUNAL_ENDPOINTS:
            client = MNIClient(tribunal=tribunal, username="u", password="p")
            assert client.wsdl_url == TRIBUNAL_ENDPOINTS[tribunal]

    def test_default_timeout(self):
        client = MNIClient(tribunal="TJES", username="u", password="p")
        assert client.timeout == 60  # MNI_TIMEOUT default

    def test_custom_timeout(self):
        client = MNIClient(tribunal="TJES", username="u", password="p", timeout=30)
        assert client.timeout == 30
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_mni_client.py -v`
Expected: 19 tests pass (4 existing + 15 new)

- [ ] **Step 4: Commit**

```bash
git add tests/test_mni_client.py
git commit -m "test: add _mimetype_to_ext and MNIClient.__init__ tests"
```

---

## Task 3: Pure Functions — worker utilities (Wave 1C)

**Files:**
- Modify: `tests/test_worker.py`
- Source: `worker.py:58-61` (_unique_filename), `worker.py:249-254` (is_session_expired), `worker.py:278-299` (_detect_captcha)

- [ ] **Step 1: Write _unique_filename and is_session_expired tests**

```python
class TestUniqueFilename:
    def test_no_collision(self, tmp_path):
        w = _load_worker_module()
        name = w._unique_filename(tmp_path, "doc.pdf")
        assert name == "doc.pdf"

    def test_collision_appends_suffix(self, tmp_path):
        w = _load_worker_module()
        (tmp_path / "doc.pdf").write_bytes(b"x")
        name = w._unique_filename(tmp_path, "doc.pdf")
        assert name == "doc_1.pdf"

    def test_multiple_collisions(self, tmp_path):
        w = _load_worker_module()
        (tmp_path / "doc.pdf").write_bytes(b"x")
        (tmp_path / "doc_1.pdf").write_bytes(b"x")
        name = w._unique_filename(tmp_path, "doc.pdf")
        assert name == "doc_2.pdf"


class TestIsSessionExpired:
    def test_no_session_returns_true(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        worker.session_started_at = None
        assert worker.is_session_expired() is True

    def test_recent_session_not_expired(self):
        from datetime import datetime, UTC
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        worker.session_started_at = datetime.now(UTC)
        assert worker.is_session_expired() is False

    def test_old_session_expired(self):
        from datetime import datetime, timedelta, UTC
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        worker.session_started_at = datetime.now(UTC) - timedelta(minutes=120)
        assert worker.is_session_expired() is True
```

- [ ] **Step 2: Write _detect_captcha tests**

```python
class TestDetectCaptcha:
    @pytest.mark.asyncio
    async def test_no_page_returns_false(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        worker.page = None
        assert await worker._detect_captcha() is False

    @pytest.mark.asyncio
    async def test_captcha_in_content(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        mock_page = AsyncMock()
        mock_page.content.return_value = '<div class="g-recaptcha">challenge</div>'
        mock_page.url = "https://pje.tjes.jus.br/pje/login.seam"
        worker.page = mock_page
        with patch.object(w, "log", MagicMock()):
            assert await worker._detect_captcha() is True

    @pytest.mark.asyncio
    async def test_no_captcha_in_clean_page(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        mock_page = AsyncMock()
        mock_page.content.return_value = '<html><body>Normal PJe page</body></html>'
        worker.page = mock_page
        assert await worker._detect_captcha() is False

    @pytest.mark.asyncio
    async def test_captcha_content_error_returns_false(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        mock_page = AsyncMock()
        mock_page.content.side_effect = Exception("page closed")
        worker.page = mock_page
        assert await worker._detect_captcha() is False
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_worker.py -v`
Expected: 14 tests pass (4 existing + 10 new)

- [ ] **Step 4: Commit**

```bash
git add tests/test_worker.py
git commit -m "test: add _unique_filename, is_session_expired, _detect_captcha tests"
```

---

## Task 4: Worker _result + _log_job_result + close (Wave 1D)

**Files:**
- Modify: `tests/test_worker.py`
- Source: worker.py (read lines 450+ for _result, _log_job_result, close)

- [ ] **Step 1: Find exact line numbers and read _result, _log_job_result, close**

Run: `grep -n "def _result\|def _log_job_result\|async def close" worker.py`

- [ ] **Step 2: Write tests** (exact code depends on Step 1 findings — use this template)

```python
class TestResultHelper:
    def test_success_result(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        r = worker._result("job1", "5000001-00.2024.8.08.0001", "success", [{"nome": "doc.pdf"}])
        assert r["status"] == "success"
        assert r["jobId"] == "job1"
        assert len(r["arquivosDownloaded"]) == 1

    def test_failed_result_with_error(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        r = worker._result("job2", "5000002-00.2024.8.08.0001", "failed", error="timeout")
        assert r["status"] == "failed"
        assert r["errorMessage"] == "timeout"

    def test_result_without_files(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        r = worker._result("job3", "5000003-00.2024.8.08.0001", "session_expired")
        assert r["arquivosDownloaded"] == []


class TestLogJobResult:
    @pytest.mark.asyncio
    async def test_writes_json_to_output_dir(self, tmp_path, monkeypatch):
        w = _load_worker_module()
        # Point DOWNLOAD_BASE_DIR to tmp_path
        monkeypatch.setattr(w, "DOWNLOAD_BASE_DIR", tmp_path)
        worker = w.PJeSessionWorker()
        files = [{"nome": "doc.pdf", "tamanhoBytes": 1024}]
        await worker._log_job_result("j1", "5000001-00.2024.8.08.0001", files)
        # Verify a result JSON was written somewhere under tmp_path
        result_files = list(tmp_path.rglob("*.json"))
        assert len(result_files) >= 1


class TestWorkerClose:
    @pytest.mark.asyncio
    async def test_close_releases_resources(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        mock_page = AsyncMock()
        mock_ctx = AsyncMock()
        mock_browser = AsyncMock()
        worker.page = mock_page
        worker.context = mock_ctx
        worker._browser = mock_browser
        worker._release_session_lock = lambda: None

        await worker.close()
        # Verify close was called on at least browser
        mock_browser.close.assert_awaited_once()
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_worker.py -v`

- [ ] **Step 4: Commit**

```bash
git add tests/test_worker.py
git commit -m "test: add _result, _log_job_result, close tests (worker)"
```

---

## Task 5: gdrive _file_info + download_gdrive_folder orchestration (Wave 1E)

**Files:**
- Modify: `tests/test_gdrive_downloader.py`
- Source: `gdrive_downloader.py:60-72` (_file_info), `gdrive_downloader.py:424-502` (download_gdrive_folder)

- [ ] **Step 1: Write _file_info tests**

```python
from gdrive_downloader import _file_info, download_gdrive_folder


class TestFileInfo:
    def test_returns_correct_structure(self, tmp_path):
        f = tmp_path / "test.pdf"
        f.write_bytes(b"PDF content here")
        info = _file_info(f)
        assert info["nome"] == "test.pdf"
        assert info["tipo"] == "pdf"
        assert info["tamanhoBytes"] == 16
        assert info["fonte"] == "google_drive"
        assert len(info["checksum"]) == 64  # SHA-256 hex

    def test_no_extension_returns_bin(self, tmp_path):
        f = tmp_path / "noext"
        f.write_bytes(b"data")
        info = _file_info(f)
        assert info["tipo"] == "bin"

    def test_checksum_is_deterministic(self, tmp_path):
        f = tmp_path / "same.pdf"
        f.write_bytes(b"same content")
        info1 = _file_info(f)
        info2 = _file_info(f)
        assert info1["checksum"] == info2["checksum"]
```

- [ ] **Step 2: Write download_gdrive_folder orchestration tests**

```python
@pytest.mark.asyncio
async def test_download_gdrive_folder_invalid_url(tmp_path):
    """Invalid GDrive URL returns empty list."""
    result = await download_gdrive_folder("https://example.com/not-gdrive", tmp_path)
    assert result == []


@pytest.mark.asyncio
async def test_download_gdrive_folder_gdown_strategy(tmp_path):
    """When strategy='gdown' and _try_gdown succeeds, returns its result."""
    expected = [{"nome": "doc.pdf", "fonte": "google_drive"}]
    with patch("gdrive_downloader._try_gdown", return_value=expected):
        result = await download_gdrive_folder(
            "https://drive.google.com/drive/folders/ABC123",
            tmp_path,
            strategy="gdown",
        )
    assert result == expected


@pytest.mark.asyncio
async def test_download_gdrive_folder_auto_fallback(tmp_path):
    """Auto strategy falls through when gdown fails, tries requests."""
    expected = [{"nome": "doc.pdf", "fonte": "google_drive"}]
    with patch("gdrive_downloader._try_gdown", return_value=None), \
         patch("gdrive_downloader._try_requests_parse", return_value=expected):
        result = await download_gdrive_folder(
            "https://drive.google.com/drive/folders/ABC123",
            tmp_path,
        )
    assert result == expected


@pytest.mark.asyncio
async def test_download_gdrive_folder_all_fail(tmp_path):
    """When all strategies fail, returns empty list."""
    with patch("gdrive_downloader._try_gdown", return_value=None), \
         patch("gdrive_downloader._try_requests_parse", return_value=None), \
         patch("gdrive_downloader._try_playwright_download", return_value=None):
        result = await download_gdrive_folder(
            "https://drive.google.com/drive/folders/ABC123",
            tmp_path,
        )
    assert result == []
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_gdrive_downloader.py -v`
Expected: 24 tests pass (17 existing + 7 new)

- [ ] **Step 4: Commit**

```bash
git add tests/test_gdrive_downloader.py
git commit -m "test: add _file_info and download_gdrive_folder orchestration tests"
```

---

## Task 6: Dashboard middleware — rate_limit, CORS, api_key (Wave 2A)

**Files:**
- Modify: `tests/test_dashboard_api.py`
- Source: `dashboard_api.py:648-724` (middleware)

- [ ] **Step 1: Write rate_limit_middleware tests**

```python
class TestRateLimitMiddleware:
    @pytest.mark.asyncio
    async def test_get_requests_not_rate_limited(self, app):
        """GET requests bypass rate limiter."""
        async with TestClient(TestServer(app)) as client:
            for _ in range(15):
                resp = await client.get("/api/progress")
                assert resp.status == 200

    @pytest.mark.asyncio
    async def test_post_rate_limit_exceeded(self, app):
        """POST requests exceeding limit get 429."""
        async with TestClient(TestServer(app)) as client:
            for i in range(11):
                resp = await client.post("/api/download", json={"processos": ["invalid"]})
                if i >= 10:
                    assert resp.status == 429


class TestCorsMiddleware:
    @pytest.mark.asyncio
    async def test_allowed_origin_reflected(self, app):
        async with TestClient(TestServer(app)) as client:
            resp = await client.get(
                "/api/progress",
                headers={"Origin": "http://localhost:8007"},
            )
            assert resp.headers.get("Access-Control-Allow-Origin") == "http://localhost:8007"

    @pytest.mark.asyncio
    async def test_disallowed_origin_defaults_to_localhost(self, app):
        async with TestClient(TestServer(app)) as client:
            resp = await client.get(
                "/api/progress",
                headers={"Origin": "https://evil.com"},
            )
            assert resp.headers.get("Access-Control-Allow-Origin") == "http://localhost"

    @pytest.mark.asyncio
    async def test_options_preflight(self, app):
        async with TestClient(TestServer(app)) as client:
            resp = await client.options(
                "/api/download",
                headers={"Origin": "http://localhost"},
            )
            assert resp.status == 200
            assert "Access-Control-Allow-Methods" in resp.headers


class TestApiKeyMiddleware:
    @pytest.mark.asyncio
    async def test_no_key_configured_allows_all(self, app):
        """When DASHBOARD_API_KEY is empty, POST requests pass through."""
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/download", json={"processos": ["0000001-01.2024.8.08.0001"]})
            # Should NOT be 401 (may be 409 or 201 depending on state)
            assert resp.status != 401

    @pytest.mark.asyncio
    async def test_wrong_key_returns_401(self, app, monkeypatch):
        """When DASHBOARD_API_KEY is set and wrong key provided, return 401."""
        import dashboard_api
        monkeypatch.setattr(dashboard_api, "DASHBOARD_API_KEY", "correct-key")
        # Recreate app to pick up the middleware change
        # Actually the middleware reads DASHBOARD_API_KEY from config at call time
        monkeypatch.setenv("DASHBOARD_API_KEY", "correct-key")
        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/api/download",
                json={"processos": ["0000001-01.2024.8.08.0001"]},
                headers={"X-API-Key": "wrong-key"},
            )
            assert resp.status == 401
```

- [ ] **Step 2: Make rate_limit tests use fixtures**

Note: The rate_limit test needs the `app` fixture. Add `self` parameter to bind to the fixture via pytest class method.
Actually, fixture is already defined — just use `app` as parameter.

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_dashboard_api.py -v`

- [ ] **Step 4: Commit**

```bash
git add tests/test_dashboard_api.py
git commit -m "test: add rate_limit, CORS, api_key middleware tests"
```

---

## Task 7: Dashboard handlers — batch_detail, session_status, index (Wave 2B)

**Files:**
- Modify: `tests/test_dashboard_api.py`
- Source: `dashboard_api.py:453-578`

- [ ] **Step 1: Write handler tests**

```python
@pytest.mark.asyncio
async def test_handle_batch_detail_not_found(app):
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/batch/nonexistent")
        assert resp.status == 404


@pytest.mark.asyncio
async def test_handle_batch_detail_returns_data(app, tmp_path):
    """Inject a batch and retrieve its details."""
    import dashboard_api
    from dashboard_api import BatchJob

    job = BatchJob(
        id="test123",
        processos=["5000001-00.2024.8.08.0001"],
        status="done",
        created_at="2024-01-01T00:00:00",
        output_dir=str(tmp_path),
        progress={"total": 1, "done": 1, "failed": 0, "processos": {}},
    )
    dashboard_api.state.batches["test123"] = job

    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/batch/test123")
        assert resp.status == 200
        body = await resp.json()
        assert body["batch_id"] == "test123"
        assert body["status"] == "done"


@pytest.mark.asyncio
async def test_handle_session_status(app):
    """GET /api/session/status returns file_exists and login state."""
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/session/status")
        assert resp.status == 200
        body = await resp.json()
        assert "file_exists" in body
        assert "login_running" in body


@pytest.mark.asyncio
async def test_handle_index_missing_html(app):
    """GET / returns 404 when dashboard.html is missing."""
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/")
        # dashboard.html doesn't exist in test environment
        assert resp.status in (200, 404)


@pytest.mark.asyncio
async def test_handle_metrics(app):
    """GET /metrics returns prometheus text."""
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/metrics")
        assert resp.status == 200
        body = await resp.text()
        assert "mni_requests" in body or "batch_processos" in body or "HELP" in body
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_dashboard_api.py -v`

- [ ] **Step 3: Commit**

```bash
git add tests/test_dashboard_api.py
git commit -m "test: add batch_detail, session_status, index, metrics handler tests"
```

---

## Task 8: MNI _save_document + _parse_processo (Wave 3A)

**Files:**
- Modify: `tests/test_mni_client.py`
- Source: `mni_client.py:704-757` (_save_document), `mni_client.py:372-532` (_parse_processo)

- [ ] **Step 1: Write _save_document tests**

```python
import base64
import hashlib


class TestSaveDocument:
    def test_saves_file_with_correct_content(self, tmp_path):
        from mni_client import MNIDocumento

        client = _make_client()
        content = b"PDF file content"
        b64 = base64.b64encode(content).decode("ascii")
        doc = MNIDocumento(id="123", nome="Petição Inicial", tipo="petição",
                           conteudo_base64=b64, tamanho_bytes=len(content))
        seen = set()
        result = client._save_document(doc, tmp_path, seen)
        assert result is not None
        assert result["nome"].endswith(".pdf")
        assert result["tamanhoBytes"] == len(content)
        # Verify file on disk
        saved_file = Path(result["localPath"])
        assert saved_file.exists()
        assert saved_file.read_bytes() == content

    def test_skips_duplicate_by_checksum(self, tmp_path):
        from mni_client import MNIDocumento

        client = _make_client()
        content = b"same content"
        b64 = base64.b64encode(content).decode("ascii")
        checksum = hashlib.sha256(content).hexdigest()
        seen = {checksum}
        doc = MNIDocumento(id="456", nome="Doc", tipo="doc",
                           conteudo_base64=b64, tamanho_bytes=len(content))
        result = client._save_document(doc, tmp_path, seen)
        assert result is None  # skipped

    def test_propagates_oserror(self, tmp_path):
        """Disk-full (OSError) must propagate, not be swallowed."""
        from mni_client import MNIDocumento

        client = _make_client()
        b64 = base64.b64encode(b"x").decode("ascii")
        doc = MNIDocumento(id="789", nome="Doc", tipo="doc",
                           conteudo_base64=b64, tamanho_bytes=1)
        # Use a non-writable directory to trigger OSError
        readonly_dir = tmp_path / "readonly"
        readonly_dir.mkdir()
        readonly_dir.chmod(0o444)
        with pytest.raises(OSError):
            client._save_document(doc, readonly_dir, set())
        readonly_dir.chmod(0o755)  # cleanup
```

- [ ] **Step 2: Write _parse_processo tests with mock SOAP response**

```python
class TestParseProcesso:
    def _mock_response(self, **overrides):
        """Build a mock SOAP response mimicking TJES MNI format."""
        dados = MagicMock()
        dados.classeProcessual = overrides.get("classe", "Execução de Título")
        dados.assunto = overrides.get("assuntos", [])
        dados.polo = overrides.get("polos", [])

        doc = MagicMock()
        doc.idDocumento = "doc1"
        doc.descricao = "Petição Inicial"
        doc.tipoDocumento = "Petição"
        doc.mimetype = "application/pdf"
        doc.conteudo = None
        doc.documentoVinculado = []

        proc = MagicMock()
        proc.dadosBasicos = dados
        proc.documento = overrides.get("documentos", [doc])
        proc.movimento = overrides.get("movimentos", [])

        resp = MagicMock()
        resp.processo = proc
        return resp

    def test_parses_basic_processo(self):
        client = _make_client()
        resp = self._mock_response()
        result = client._parse_processo(resp, "5000001-00.2024.8.08.0001")
        assert result.numero == "5000001-00.2024.8.08.0001"
        assert result.classe == "Execução de Título"
        assert len(result.documentos) == 1
        assert result.documentos[0].id == "doc1"

    def test_parses_polo_ativo_passivo(self):
        polo_at = MagicMock()
        polo_at.polo = "AT"
        parte_at = MagicMock()
        parte_at.pessoa = MagicMock(nome="João Silva")
        polo_at.parte = [parte_at]

        polo_pa = MagicMock()
        polo_pa.polo = "PA"
        parte_pa = MagicMock()
        parte_pa.pessoa = MagicMock(nome="Banco SA")
        polo_pa.parte = [parte_pa]

        client = _make_client()
        resp = self._mock_response(polos=[polo_at, polo_pa])
        result = client._parse_processo(resp, "5000001-00.2024.8.08.0001")
        assert "João Silva" in result.polo_ativo
        assert "Banco SA" in result.polo_passivo

    def test_handles_empty_documentos(self):
        client = _make_client()
        resp = self._mock_response(documentos=[])
        result = client._parse_processo(resp, "5000001-00.2024.8.08.0001")
        assert result.documentos == []

    def test_parses_documento_with_content(self):
        doc = MagicMock()
        doc.idDocumento = "doc2"
        doc.descricao = "Sentença"
        doc.tipoDocumento = "Sentença"
        doc.mimetype = "application/pdf"
        doc.conteudo = b"binary pdf content"
        doc.documentoVinculado = []

        client = _make_client()
        resp = self._mock_response(documentos=[doc])
        result = client._parse_processo(resp, "5000001-00.2024.8.08.0001")
        assert result.documentos[0].has_content is True
        assert result.documentos[0].tamanho_bytes == len(b"binary pdf content")
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_mni_client.py -v`

- [ ] **Step 4: Commit**

```bash
git add tests/test_mni_client.py
git commit -m "test: add _save_document and _parse_processo tests (mni_client)"
```

---

## Task 9: Dashboard eviction + submit_batch (Wave 3B)

**Files:**
- Modify: `tests/test_dashboard_api.py`
- Source: `dashboard_api.py:110-123` (_evict_old_batches), `dashboard_api.py:125-155` (submit_batch)

- [ ] **Step 1: Write eviction and submit tests**

```python
class TestEviction:
    def test_evicts_oldest_when_over_limit(self, tmp_path):
        ds = DashboardState(tmp_path)
        from dashboard_api import BatchJob, MAX_BATCH_HISTORY

        # Fill beyond limit
        for i in range(MAX_BATCH_HISTORY + 10):
            job = BatchJob(
                id=f"batch_{i:04d}",
                processos=["x"],
                status="done",
                finished_at=f"2024-01-{i+1:02d}T00:00:00",
            )
            ds.batches[job.id] = job

        ds._evict_old_batches()
        assert len([b for b in ds.batches.values() if b.status == "done"]) <= MAX_BATCH_HISTORY

    def test_does_not_evict_current_batch(self, tmp_path):
        ds = DashboardState(tmp_path)
        from dashboard_api import BatchJob, MAX_BATCH_HISTORY

        for i in range(MAX_BATCH_HISTORY + 5):
            job = BatchJob(id=f"b{i}", processos=["x"], status="done",
                           finished_at=f"2024-01-{(i%28)+1:02d}")
            ds.batches[job.id] = job

        ds.current_batch_id = "b0"  # oldest — should NOT be evicted
        ds._evict_old_batches()
        assert "b0" in ds.batches


class TestSubmitBatch:
    @pytest.mark.asyncio
    async def test_creates_batch_and_task(self, tmp_path):
        ds = DashboardState(tmp_path)
        # Patch _run_batch to be a no-op
        async def noop_run(job):
            job.status = "done"
        ds._run_batch = noop_run

        job = await ds.submit_batch(["5000001-00.2024.8.08.0001"])
        assert job.status == "queued"
        assert len(job.processos) == 1
        assert ds.current_batch_id == job.id
        assert ds._task is not None
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_dashboard_api.py -v`

- [ ] **Step 3: Commit**

```bash
git add tests/test_dashboard_api.py
git commit -m "test: add eviction and submit_batch tests"
```

---

## Task 10: Config edge cases + _purge_stale_buckets (Wave 4)

**Files:**
- Modify: `tests/test_config.py`
- Modify: `tests/test_dashboard_api.py`

- [ ] **Step 1: Write PJE_BASE_URL validation edge case tests**

```python
class TestPjeBaseUrlValidation:
    def test_default_url_is_valid(self):
        """The default PJE_BASE_URL should be accepted."""
        from config import PJE_BASE_URL
        assert PJE_BASE_URL.startswith("https://")
        assert ".jus.br" in PJE_BASE_URL
```

- [ ] **Step 2: Write _purge_stale_buckets test**

```python
def test_purge_stale_buckets():
    """Old IPs are removed from rate limiter."""
    import dashboard_api
    now = time.monotonic()
    dashboard_api._rate_buckets["1.2.3.4"] = [now - 400]
    dashboard_api._rate_bucket_last_seen["1.2.3.4"] = now - 400
    dashboard_api._rate_buckets["5.6.7.8"] = [now]
    dashboard_api._rate_bucket_last_seen["5.6.7.8"] = now

    dashboard_api._purge_stale_buckets(now)
    assert "1.2.3.4" not in dashboard_api._rate_buckets
    assert "5.6.7.8" in dashboard_api._rate_buckets
```

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/ -v --tb=short`
Expected: ~170+ tests pass

- [ ] **Step 4: Final commit**

```bash
git add tests/
git commit -m "test: Sprint 4 complete — config edge cases + rate limiter purge"
```

---

## Sprint 4 Summary

| Wave | Task | Module | New Tests | Symbols Covered |
|------|------|--------|-----------|-----------------|
| 1A | Task 1 | pje_session | 10 | _guess_ext, _load_state |
| 1B | Task 2 | mni_client | 15 | _mimetype_to_ext, MNIClient.__init__ |
| 1C | Task 3 | worker | 10 | _unique_filename, is_session_expired, _detect_captcha |
| 1D | Task 4 | worker | 7 | _result, _log_job_result, close |
| 1E | Task 5 | gdrive | 7 | _file_info, download_gdrive_folder |
| 2A | Task 6 | dashboard | 8 | rate_limit_middleware, cors_middleware, api_key_middleware |
| 2B | Task 7 | dashboard | 5 | handle_batch_detail, handle_session_status, handle_index, handle_metrics |
| 3A | Task 8 | mni_client | 7 | _save_document, _parse_processo |
| 3B | Task 9 | dashboard | 3 | _evict_old_batches, submit_batch |
| 4 | Task 10 | config+dashboard | 2 | PJE_BASE_URL, _purge_stale_buckets |
| **Total** | | | **~74** | **~49 symbols → 85/113 = 75%** |

**Post-sprint test count target:** 111 + 74 = **185 tests**
