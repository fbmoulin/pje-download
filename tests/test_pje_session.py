"""Tests for pje_session module — pure functions and mocked Playwright."""

from __future__ import annotations

import json
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
