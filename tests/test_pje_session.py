"""Tests for pje_session module — pure functions and mocked Playwright."""

from __future__ import annotations

import asyncio
import hashlib
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestSafeFilename:
    def test_strips_special_chars(self):
        from config import sanitize_filename

        assert (
            sanitize_filename("doc:name/with\\bad*chars", maxlen=120)
            == "doc_name_with_bad_chars"
        )

    def test_length_limited(self):
        from config import sanitize_filename

        long_name = "a" * 200
        assert len(sanitize_filename(long_name, maxlen=120)) <= 120

    def test_empty_returns_empty(self):
        from config import sanitize_filename

        assert sanitize_filename("", maxlen=120) == ""


class TestUniquePath:
    def test_no_collision(self, tmp_path):
        from config import unique_path

        p = tmp_path / "file.pdf"
        assert unique_path(p) == p

    def test_collision_adds_suffix(self, tmp_path):
        from config import unique_path

        p = tmp_path / "file.pdf"
        p.write_bytes(b"existing")
        result = unique_path(p)
        assert result == tmp_path / "file_1.pdf"

    def test_multiple_collisions(self, tmp_path):
        from config import unique_path

        p = tmp_path / "file.pdf"
        p.write_bytes(b"existing")
        (tmp_path / "file_1.pdf").write_bytes(b"existing")
        result = unique_path(p)
        assert result == tmp_path / "file_2.pdf"


class TestGuessExt:
    def test_pdf_content_type(self):
        from pje_session import _guess_ext

        assert _guess_ext("application/pdf", "doc") == ".pdf"

    def test_html_content_type(self):
        from pje_session import _guess_ext

        assert _guess_ext("text/html", "doc") == ".html"

    def test_name_has_extension_returns_empty(self):
        from pje_session import _guess_ext

        assert _guess_ext("application/pdf", "doc.pdf") == ""

    def test_unknown_type(self):
        from pje_session import _guess_ext

        assert _guess_ext("application/octet-stream", "doc") == ".bin"

    def test_none_content_type(self):
        from pje_session import _guess_ext

        assert _guess_ext("", "doc") == ".bin"

    def test_xml_content_type(self):
        from pje_session import _guess_ext

        assert _guess_ext("application/xml", "data") == ".xml"

    def test_empty_content_type_noext_name(self):
        from pje_session import _guess_ext

        assert _guess_ext("", "noext") == ".bin"

    def test_name_with_dot_ignores_content_type(self):
        from pje_session import _guess_ext

        assert _guess_ext("text/html", "file.html") == ""


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


class TestInteractiveLogin:
    @pytest.mark.asyncio
    async def test_success_saves_session(self, tmp_path):
        from unittest.mock import AsyncMock, patch

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

        with patch("playwright.async_api.async_playwright", return_value=mock_pw_ctx):
            from pje_session import interactive_login

            result = await interactive_login(session_file=sf)

        assert result is True
        assert sf.exists()
        mock_browser.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_success_creates_parent_dir(self, tmp_path):
        from unittest.mock import AsyncMock, patch

        sf = tmp_path / "nested" / "session.json"

        mock_page = AsyncMock()
        mock_page.url = "https://pje.tjes.jus.br/pje/painel.seam"
        mock_page.wait_for_url = AsyncMock()
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

        with patch("playwright.async_api.async_playwright", return_value=mock_pw_ctx):
            from pje_session import interactive_login

            result = await interactive_login(session_file=sf)

        assert result is True
        assert sf.parent.exists()
        assert sf.exists()
        mock_browser.close.assert_awaited_once()

    @pytest.mark.asyncio
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

        with patch("playwright.async_api.async_playwright", return_value=mock_pw_ctx):
            from pje_session import interactive_login

            result = await interactive_login(session_file=sf)

        assert result is False
        assert not sf.exists()
        mock_browser.close.assert_awaited_once()


class TestPJeSessionClientLoadState:
    def test_missing_file_raises_fnf(self, tmp_path):
        from pje_session import PJeSessionClient

        client = PJeSessionClient(session_file=tmp_path / "nonexistent.json")
        with pytest.raises(FileNotFoundError, match="Sessão não encontrada"):
            client._load_state()

    def test_corrupt_json_raises(self, tmp_path):
        from pje_session import PJeSessionClient

        f = tmp_path / "session.json"
        f.write_text("not valid json")
        client = PJeSessionClient(session_file=f)
        with pytest.raises(Exception):
            client._load_state()

    def test_valid_json_returns_dict(self, tmp_path):
        from pje_session import PJeSessionClient

        f = tmp_path / "session.json"
        f.write_text('{"cookies": [], "origins": []}')
        client = PJeSessionClient(session_file=f)
        state = client._load_state()
        assert isinstance(state, dict)
        assert "cookies" in state


# ─────────────────────────────────────────────
# Playwright mock helper
# ─────────────────────────────────────────────


def _mock_playwright_chain():
    """Build full Playwright mock: playwright -> browser -> context -> page."""
    page = AsyncMock()
    page.url = "https://pje.tjes.jus.br/pje/painel.seam"
    page.goto = AsyncMock()
    page.content = AsyncMock(return_value="<html></html>")
    page.wait_for_url = AsyncMock()
    page.wait_for_load_state = AsyncMock()
    page.fill = AsyncMock()
    page.click = AsyncMock()
    page.close = AsyncMock()

    # page.keyboard
    page.keyboard = AsyncMock()
    page.keyboard.press = AsyncMock()

    # page.request for API calls
    api_response = AsyncMock()
    api_response.ok = True
    api_response.status = 200
    api_response.json = AsyncMock(
        return_value=[{"id": "DOC1", "nome": "sentenca.pdf", "tipo": "sentenca"}]
    )
    api_response.body = AsyncMock(return_value=b"PDF_CONTENT")
    api_response.headers = {"content-type": "application/pdf"}
    page.request = AsyncMock()
    page.request.get = AsyncMock(return_value=api_response)

    # Download mock
    download = AsyncMock()
    download.suggested_filename = "documento.pdf"
    download.save_as = AsyncMock()

    ctx = AsyncMock()
    ctx.new_page = AsyncMock(return_value=page)
    ctx.storage_state = AsyncMock(
        return_value={
            "cookies": [{"name": "JSESSIONID", "value": "abc"}],
            "origins": [],
        }
    )

    browser = AsyncMock()
    browser.new_context = AsyncMock(return_value=ctx)
    browser.close = AsyncMock()

    pw = AsyncMock()
    pw.chromium.launch = AsyncMock(return_value=browser)

    return pw, browser, ctx, page, download, api_response


def _make_pw_context_manager(pw):
    """Wrap a mock playwright object in an async context manager."""
    pw_ctx = AsyncMock()
    pw_ctx.__aenter__ = AsyncMock(return_value=pw)
    pw_ctx.__aexit__ = AsyncMock(return_value=False)
    return pw_ctx


def _make_client(tmp_path):
    """Create a PJeSessionClient with a fake session file."""
    from pje_session import PJeSessionClient

    sf = tmp_path / "session.json"
    sf.write_text(json.dumps({"cookies": [], "origins": []}))
    return PJeSessionClient(session_file=sf)


# ─────────────────────────────────────────────
# Audit instrumentation tests — _try_api
# ─────────────────────────────────────────────


class TestTryApiAudit:
    @pytest.mark.asyncio
    async def test_audit_called_on_api_save(self, tmp_path):
        """_try_api() calls audit.log_access with document_saved."""
        import audit as real_audit

        pw, browser, ctx, page, download, api_resp = _mock_playwright_chain()
        client = _make_client(tmp_path)
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with patch("pje_session.audit") as mock_audit:
            mock_audit.AuditEntry = real_audit.AuditEntry
            result = await client._try_api(
                ctx, "5000001-02.2024.8.08.0001", output_dir, True
            )

        assert len(result) == 1
        mock_audit.log_access.assert_called_once()
        entry = mock_audit.log_access.call_args[0][0]
        assert entry.event_type == "document_saved"
        assert entry.fonte == "pje_api"
        assert entry.processo_numero == "5000001-02.2024.8.08.0001"
        assert entry.status == "success"

    @pytest.mark.asyncio
    async def test_audit_includes_checksum(self, tmp_path):
        """_try_api() includes SHA256 checksum of content."""
        import audit as real_audit

        pw, browser, ctx, page, download, api_resp = _mock_playwright_chain()
        client = _make_client(tmp_path)
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with patch("pje_session.audit") as mock_audit:
            mock_audit.AuditEntry = real_audit.AuditEntry
            await client._try_api(ctx, "5000001-02.2024.8.08.0001", output_dir, True)

        entry = mock_audit.log_access.call_args[0][0]
        expected_hash = hashlib.sha256(b"PDF_CONTENT").hexdigest()
        assert entry.checksum_sha256 == expected_hash
        assert entry.tamanho_bytes == len(b"PDF_CONTENT")

    @pytest.mark.asyncio
    async def test_try_api_can_download_only_annexes(self, tmp_path):
        """_try_api() can skip principais during annex complement."""
        pw, browser, ctx, page, download, api_resp = _mock_playwright_chain()
        api_resp.json = AsyncMock(
            return_value=[
                {"id": "DOC1", "nome": "principal.pdf", "tipo": "sentenca"},
                {"id": "DOC2", "nome": "anexo.pdf", "tipo": "anexo"},
            ]
        )

        principal_resp = AsyncMock()
        principal_resp.ok = True
        principal_resp.body = AsyncMock(return_value=b"PRINCIPAL")
        principal_resp.headers = {"content-type": "application/pdf"}

        annex_resp = AsyncMock()
        annex_resp.ok = True
        annex_resp.body = AsyncMock(return_value=b"ANNEX")
        annex_resp.headers = {"content-type": "application/pdf"}

        page.request.get = AsyncMock(side_effect=[api_resp, annex_resp])

        client = _make_client(tmp_path)
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with patch("pje_session.audit"):
            result = await client._try_api(
                ctx,
                "5000001-02.2024.8.08.0001",
                output_dir,
                include_anexos=True,
                include_principais=False,
            )

        assert len(result) == 1
        assert result[0]["nome"].startswith("anexo")
        assert result[0]["checksum"] == hashlib.sha256(b"ANNEX").hexdigest()


# ─────────────────────────────────────────────
# Audit instrumentation tests — _try_browser
# ─────────────────────────────────────────────


def _setup_browser_download_mocks(page, download, output_dir, file_content=b"CONTENT"):
    """Configure page mocks for browser download path."""
    # Locator chain
    link_mock = AsyncMock()
    link_mock.count = AsyncMock(return_value=1)
    link_mock.click = AsyncMock()
    page.locator = MagicMock(return_value=link_mock)
    link_mock.first = link_mock

    # expect_download async context manager
    # The code does: async with page.expect_download(...) as dl_info:
    #                    ...click...
    #                dl = await dl_info.value
    # dl_info is the result of __aenter__, and dl_info.value must be awaitable
    dl_event = MagicMock()
    # Make .value an awaitable that returns the download mock
    future = asyncio.get_event_loop().create_future()
    future.set_result(download)
    dl_event.value = future

    dl_cm = AsyncMock()
    dl_cm.__aenter__ = AsyncMock(return_value=dl_event)
    dl_cm.__aexit__ = AsyncMock(return_value=False)
    page.expect_download = MagicMock(return_value=dl_cm)

    # save_as writes the file to disk
    async def fake_save_as(dest):
        dest.write_bytes(file_content)

    download.save_as = fake_save_as


class TestTryBrowserAudit:
    @pytest.mark.asyncio
    async def test_audit_called_on_browser_save(self, tmp_path):
        """_try_browser() calls audit.log_access with document_saved."""
        import audit as real_audit

        pw, browser, ctx, page, download, api_resp = _mock_playwright_chain()
        client = _make_client(tmp_path)
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        _setup_browser_download_mocks(page, download, output_dir, b"BROWSER_PDF")

        with patch("pje_session.audit") as mock_audit:
            mock_audit.AuditEntry = real_audit.AuditEntry
            result = await client._try_browser(
                ctx, "5000001-02.2024.8.08.0001", output_dir
            )

        assert len(result) >= 1
        mock_audit.log_access.assert_called_once()
        entry = mock_audit.log_access.call_args[0][0]
        assert entry.event_type == "document_saved"
        assert entry.fonte == "pje_browser"
        assert entry.status == "success"

    @pytest.mark.asyncio
    async def test_audit_no_checksum_for_browser(self, tmp_path):
        """_try_browser() sets checksum_sha256=None."""
        import audit as real_audit

        pw, browser, ctx, page, download, api_resp = _mock_playwright_chain()
        client = _make_client(tmp_path)
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        _setup_browser_download_mocks(page, download, output_dir, b"CONTENT")

        with patch("pje_session.audit") as mock_audit:
            mock_audit.AuditEntry = real_audit.AuditEntry
            await client._try_browser(ctx, "5000001-02.2024.8.08.0001", output_dir)

        entry = mock_audit.log_access.call_args[0][0]
        assert entry.checksum_sha256 is None


# ─────────────────────────────────────────────
# Playwright smoke tests
# ─────────────────────────────────────────────


class TestPlaywrightSmoke:
    @pytest.mark.asyncio
    async def test_is_valid_with_active_session(self, tmp_path):
        """is_valid() returns True when page lands on painel.seam."""
        pw, browser, ctx, page, download, api_resp = _mock_playwright_chain()
        page.url = "https://pje.tjes.jus.br/pje/painel.seam"
        pw_ctx = _make_pw_context_manager(pw)

        client = _make_client(tmp_path)
        with patch("playwright.async_api.async_playwright", return_value=pw_ctx):
            result = await client.is_valid()

        assert result is True
        browser.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_is_valid_expired_session(self, tmp_path):
        """is_valid() returns False when page stays on login."""
        pw, browser, ctx, page, download, api_resp = _mock_playwright_chain()
        page.url = "https://sso.cloud.pje.jus.br/auth/login"
        pw_ctx = _make_pw_context_manager(pw)

        client = _make_client(tmp_path)
        with patch("playwright.async_api.async_playwright", return_value=pw_ctx):
            result = await client.is_valid()

        assert result is False

    @pytest.mark.asyncio
    async def test_download_processo_api_path(self, tmp_path):
        """download_processo() tries API first."""
        pw, browser, ctx, page, download, api_resp = _mock_playwright_chain()
        pw_ctx = _make_pw_context_manager(pw)
        client = _make_client(tmp_path)
        output_dir = tmp_path / "downloads"

        with (
            patch("playwright.async_api.async_playwright", return_value=pw_ctx),
            patch("pje_session.audit"),
        ):
            result = await client.download_processo(
                "5000001-02.2024.8.08.0001", output_dir
            )

        assert len(result) == 1
        assert result[0]["fonte"] == "pje_api"

    @pytest.mark.asyncio
    async def test_download_processo_browser_fallback(self, tmp_path):
        """download_processo() falls back to browser when API fails."""
        pw, browser, ctx, page, download, api_resp = _mock_playwright_chain()
        pw_ctx = _make_pw_context_manager(pw)

        # Make API return auth failure so fallback triggers
        api_resp.status = 403
        api_resp.ok = False
        page.request.get = AsyncMock(return_value=api_resp)

        client = _make_client(tmp_path)
        output_dir = tmp_path / "downloads"
        _setup_browser_download_mocks(page, download, output_dir, b"BROWSER_PDF")

        with (
            patch("playwright.async_api.async_playwright", return_value=pw_ctx),
            patch("pje_session.audit"),
        ):
            result = await client.download_processo(
                "5000001-02.2024.8.08.0001", output_dir
            )

        # API failed (403), browser fallback attempted. Both paths completed.
        assert len(result) == 1
        assert result[0]["fonte"] == "pje_browser"
        browser.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_download_processo_annex_only_skips_browser_fallback(self, tmp_path):
        """Annex-only mode avoids browser fallback to prevent redownloading principals."""
        pw, browser, ctx, page, download, api_resp = _mock_playwright_chain()
        pw_ctx = _make_pw_context_manager(pw)

        api_resp.status = 403
        api_resp.ok = False
        page.request.get = AsyncMock(return_value=api_resp)

        client = _make_client(tmp_path)
        output_dir = tmp_path / "downloads"

        with (
            patch("playwright.async_api.async_playwright", return_value=pw_ctx),
            patch("pje_session.audit"),
            patch.object(
                client, "_try_browser", new_callable=AsyncMock
            ) as mock_browser,
        ):
            result = await client.download_processo(
                "5000001-02.2024.8.08.0001",
                output_dir,
                include_anexos=True,
                include_principais=False,
            )

        assert result == []
        mock_browser.assert_not_awaited()
        browser.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_interactive_login_saves_session(self, tmp_path):
        """interactive_login() calls storage_state and saves to file."""
        pw, browser, ctx, page, download, api_resp = _mock_playwright_chain()
        page.url = "https://pje.tjes.jus.br/pje/painel.seam"
        pw_ctx = _make_pw_context_manager(pw)

        sf = tmp_path / "session.json"
        with patch("playwright.async_api.async_playwright", return_value=pw_ctx):
            from pje_session import interactive_login

            result = await interactive_login(session_file=sf)

        assert result is True
        assert sf.exists()
        saved = json.loads(sf.read_text())
        assert "cookies" in saved
