"""Tests for gdrive_downloader — extract_folder_id, is_processo_antigo, regex fixes."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gdrive_downloader import (
    _file_info,
    _try_gdown,
    _try_playwright_download,
    _try_requests_parse,
    download_gdrive_folder,
    extract_folder_id,
    is_processo_antigo,
)


# ─────────────────────────────────────────────
# extract_folder_id
# ─────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _patch_asyncio_to_thread(monkeypatch):
    """Make thread-offloaded unit tests deterministic in this sandbox."""

    async def _fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _fake_to_thread)


class TestExtractFolderId:
    def test_standard_folders_url(self):
        url = "https://drive.google.com/drive/folders/1a2B3c4D5e6F7g8H9i0J1k2L3m4N5o6P"
        assert extract_folder_id(url) == "1a2B3c4D5e6F7g8H9i0J1k2L3m4N5o6P"

    def test_url_with_usp_sharing(self):
        url = "https://drive.google.com/drive/folders/AbCdEfGhIjKlMnOpQrStUvWxYz1234?usp=sharing"
        fid = extract_folder_id(url)
        assert fid == "AbCdEfGhIjKlMnOpQrStUvWxYz1234"

    def test_url_with_user_prefix(self):
        url = "https://drive.google.com/drive/u/0/folders/MY_FOLDER_ID"
        assert extract_folder_id(url) == "MY_FOLDER_ID"

    def test_open_id_format(self):
        url = "https://drive.google.com/open?id=FOLDER_XYZ"
        assert extract_folder_id(url) == "FOLDER_XYZ"

    def test_folderview_format(self):
        url = "https://drive.google.com/folderview?id=FOLDER_ABC"
        assert extract_folder_id(url) == "FOLDER_ABC"

    def test_invalid_url_returns_none(self):
        assert extract_folder_id("https://example.com/not-gdrive") is None

    def test_empty_string_returns_none(self):
        assert extract_folder_id("") is None


# ─────────────────────────────────────────────
# is_processo_antigo
# ─────────────────────────────────────────────


class TestIsProcessoAntigo:
    def test_starts_with_5_is_moderno(self):
        assert is_processo_antigo("5008407-35.2024.8.08.0012") is False

    def test_starts_with_0_is_antigo(self):
        assert is_processo_antigo("0126923-56.2011.8.08.0012") is True

    def test_starts_with_1_is_antigo(self):
        assert is_processo_antigo("1234567-89.2024.8.08.0001") is True

    def test_empty_string(self):
        assert is_processo_antigo("") is False

    def test_whitespace_only(self):
        assert is_processo_antigo("   ") is False

    def test_year_2012_is_antigo_even_starting_with_5(self):
        """Secondary rule: year < 2013 forces antigo, even if starts with 5."""
        assert is_processo_antigo("5000001-01.2012.8.08.0001") is True

    def test_year_2013_starting_with_5_is_moderno(self):
        assert is_processo_antigo("5000001-01.2013.8.08.0001") is False

    def test_year_2024_starting_with_5_is_moderno(self):
        assert is_processo_antigo("5008407-35.2024.8.08.0012") is False

    def test_no_year_in_number_falls_back_to_prefix(self):
        # No CNJ format match — falls back to starts-with-5 rule only
        assert is_processo_antigo("5XXXXX") is False
        assert is_processo_antigo("0XXXXX") is True


# ─────────────────────────────────────────────
# JS file ID regex (gap #7 fix)
# ─────────────────────────────────────────────


class TestFileIdRegex:
    PATTERN = re.compile(r'\["([a-zA-Z0-9_-]{28,44})"')

    def test_accepts_28_char_id(self):
        text = '["' + "A" * 28 + '"'
        assert self.PATTERN.search(text) is not None

    def test_accepts_44_char_id(self):
        text = '["' + "A" * 44 + '"'
        assert self.PATTERN.search(text) is not None

    def test_rejects_27_char_id(self):
        text = '["' + "A" * 27 + '"'
        assert self.PATTERN.search(text) is None

    def test_rejects_45_char_id(self):
        text = '["' + "A" * 45 + '"'
        assert self.PATTERN.search(text) is None

    def test_rejects_short_strings(self):
        text = '["short"'
        assert self.PATTERN.search(text) is None


# ─────────────────────────────────────────────
# Confirm token fallback (gap #6 fix)
# ─────────────────────────────────────────────


class TestConfirmTokenFallback:
    def test_old_style_token_extracted(self):
        html = '<a href="?confirm=abc123XYZ&id=FILE">Download</a>'
        match = re.search(r"confirm=([a-zA-Z0-9_-]+)", html)
        token = match.group(1) if match else "t"
        assert token == "abc123XYZ"

    def test_no_token_falls_back_to_t(self):
        html = "<html>Please wait...</html>"
        match = re.search(r"confirm=([a-zA-Z0-9_-]+)", html)
        token = match.group(1) if match else "t"
        assert token == "t"

    def test_modern_confirm_t_extracted(self):
        html = '<a href="?confirm=t&id=FILE">Download</a>'
        match = re.search(r"confirm=([a-zA-Z0-9_-]+)", html)
        token = match.group(1) if match else "t"
        assert token == "t"


# ─────────────────────────────────────────────
# _file_info
# ─────────────────────────────────────────────


class TestFileInfo:
    def test_returns_correct_structure(self, tmp_path):
        f = tmp_path / "test.pdf"
        f.write_bytes(b"PDF content here")
        info = _file_info(f)
        assert info["nome"] == "test.pdf"
        assert info["tipo"] == "pdf"
        assert info["tamanhoBytes"] == 16
        assert info["fonte"] == "google_drive"
        assert len(info["checksum"]) == 64

    def test_no_extension_returns_bin(self, tmp_path):
        f = tmp_path / "noext"
        f.write_bytes(b"data")
        info = _file_info(f)
        assert info["tipo"] == "bin"

    def test_checksum_is_deterministic(self, tmp_path):
        f = tmp_path / "same.pdf"
        f.write_bytes(b"same content")
        assert _file_info(f)["checksum"] == _file_info(f)["checksum"]


# ─────────────────────────────────────────────
# download_gdrive_folder orchestration
# ─────────────────────────────────────────────


class TestDownloadGdriveFolderOrchestration:
    @pytest.mark.asyncio
    async def test_invalid_url_returns_empty(self, tmp_path):
        result = await download_gdrive_folder(
            "https://example.com/not-gdrive", tmp_path
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_gdown_strategy_success(self, tmp_path):
        expected = [{"nome": "doc.pdf", "fonte": "google_drive"}]
        with patch("gdrive_downloader._try_gdown", return_value=expected):
            result = await download_gdrive_folder(
                "https://drive.google.com/drive/folders/ABC123",
                tmp_path,
                strategy="gdown",
            )
        assert result == expected

    @pytest.mark.asyncio
    async def test_auto_fallback_to_requests(self, tmp_path):
        expected = [{"nome": "doc.pdf", "fonte": "google_drive"}]
        with (
            patch("gdrive_downloader._try_gdown", return_value=None),
            patch("gdrive_downloader._try_requests_parse", return_value=expected),
        ):
            result = await download_gdrive_folder(
                "https://drive.google.com/drive/folders/ABC123",
                tmp_path,
            )
        assert result == expected

    @pytest.mark.asyncio
    async def test_all_strategies_fail_returns_empty(self, tmp_path):
        with (
            patch("gdrive_downloader._try_gdown", return_value=None),
            patch("gdrive_downloader._try_requests_parse", return_value=None),
            patch("gdrive_downloader._try_playwright_download", return_value=None),
        ):
            result = await download_gdrive_folder(
                "https://drive.google.com/drive/folders/ABC123",
                tmp_path,
            )
        assert result == []


# ─────────────────────────────────────────────
# _try_gdown
# ─────────────────────────────────────────────


class TestTryGdown:
    @pytest.mark.asyncio
    async def test_gdown_success(self, tmp_path):
        """_try_gdown returns file list when gdown succeeds."""
        # Create real files that gdown would produce
        f1 = tmp_path / "doc1.pdf"
        f1.write_bytes(b"PDF content 1")
        f2 = tmp_path / "doc2.pdf"
        f2.write_bytes(b"PDF content 2")

        mock_gdown = MagicMock()
        mock_gdown.download_folder = MagicMock(return_value=[str(f1), str(f2)])

        with (
            patch.dict("sys.modules", {"gdown": mock_gdown}),
            patch("gdrive_downloader.metrics"),
        ):
            result = await _try_gdown(
                "https://drive.google.com/drive/folders/ABC123", tmp_path
            )

        assert result is not None
        assert len(result) == 2
        assert result[0]["nome"] == "doc1.pdf"
        assert result[1]["nome"] == "doc2.pdf"
        assert result[0]["fonte"] == "google_drive"

    @pytest.mark.asyncio
    async def test_gdown_not_installed(self, tmp_path):
        """_try_gdown returns None when gdown not installed."""
        with patch.dict("sys.modules", {"gdown": None}):
            result = await _try_gdown(
                "https://drive.google.com/drive/folders/ABC123", tmp_path
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_gdown_failure(self, tmp_path):
        """_try_gdown returns None on download failure."""
        mock_gdown = MagicMock()
        mock_gdown.download_folder = MagicMock(
            side_effect=RuntimeError("Network error")
        )

        with (
            patch.dict("sys.modules", {"gdown": mock_gdown}),
            patch("gdrive_downloader.metrics"),
        ):
            result = await _try_gdown(
                "https://drive.google.com/drive/folders/ABC123", tmp_path
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_gdown_no_files_downloaded(self, tmp_path):
        """_try_gdown returns None when gdown returns empty list."""
        mock_gdown = MagicMock()
        mock_gdown.download_folder = MagicMock(return_value=[])

        with (
            patch.dict("sys.modules", {"gdown": mock_gdown}),
            patch("gdrive_downloader.metrics"),
        ):
            result = await _try_gdown(
                "https://drive.google.com/drive/folders/ABC123", tmp_path
            )
        assert result is None


# ─────────────────────────────────────────────
# _try_requests_parse
# ─────────────────────────────────────────────


def _make_folder_html(file_entries: list[tuple[str, str]]) -> str:
    """Build mock HTML for a GDrive folder page with file links."""
    html = "<html><body>"
    for fid, fname in file_entries:
        html += f'<a href="/file/d/{fid}/view">{fname}</a>'
    html += "</body></html>"
    return html


def _make_mock_session(folder_html: str, file_content: bytes = b"PDF_CONTENT"):
    """Build a mock requests.Session for folder page + file downloads."""
    session = MagicMock()

    # Folder page response
    folder_resp = MagicMock()
    folder_resp.status_code = 200
    folder_resp.text = folder_html

    # File download response (streaming)
    file_resp = MagicMock()
    file_resp.status_code = 200
    file_resp.headers = {
        "Content-Disposition": 'attachment; filename="test.pdf"',
        "Content-Type": "application/pdf",
    }
    file_resp.iter_content = MagicMock(return_value=[file_content])

    session.get = MagicMock(side_effect=[folder_resp, file_resp])
    session.headers = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    return session


class TestTryRequestsParse:
    @pytest.mark.asyncio
    async def test_success_with_files(self, tmp_path):
        """_try_requests_parse downloads files from GDrive folder."""
        folder_html = _make_folder_html([("A" * 33, "documento.pdf")])
        session = _make_mock_session(folder_html, b"PDF_CONTENT_HERE")

        with (
            patch("requests.Session", return_value=session),
            patch("gdrive_downloader.metrics"),
            patch("gdrive_downloader.audit"),
        ):
            result = await _try_requests_parse("FOLDER_ID_123", tmp_path)

        assert result is not None
        assert len(result) == 1
        assert result[0]["fonte"] == "google_drive"
        assert result[0]["tamanhoBytes"] > 0

    @pytest.mark.asyncio
    async def test_empty_folder(self, tmp_path):
        """Returns None for folder with no files."""
        folder_html = "<html><body>No files here</body></html>"
        session = MagicMock()
        folder_resp = MagicMock()
        folder_resp.status_code = 200
        folder_resp.text = folder_html
        session.get = MagicMock(return_value=folder_resp)
        session.headers = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)

        with (
            patch("requests.Session", return_value=session),
            patch("gdrive_downloader.metrics"),
        ):
            result = await _try_requests_parse("FOLDER_ID_123", tmp_path)

        assert result is None

    @pytest.mark.asyncio
    async def test_http_error(self, tmp_path):
        """Returns None on HTTP error."""
        session = MagicMock()
        error_resp = MagicMock()
        error_resp.status_code = 403
        session.get = MagicMock(return_value=error_resp)
        session.headers = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)

        with (
            patch("requests.Session", return_value=session),
            patch("gdrive_downloader.metrics"),
        ):
            result = await _try_requests_parse("FOLDER_ID_123", tmp_path)

        assert result is None

    @pytest.mark.asyncio
    async def test_stream_timeout(self, tmp_path):
        """Handles timeout during file streaming."""
        folder_html = _make_folder_html([("B" * 33, "bigfile.pdf")])

        session = MagicMock()
        folder_resp = MagicMock()
        folder_resp.status_code = 200
        folder_resp.text = folder_html

        # File download response that hangs
        file_resp = MagicMock()
        file_resp.status_code = 200
        file_resp.headers = {
            "Content-Disposition": 'filename="bigfile.pdf"',
            "Content-Type": "application/pdf",
        }

        # Simulate a very slow iter_content that causes timeout
        def _slow_iter(*a, **kw):
            import time

            time.sleep(5)
            return iter([b"data"])

        file_resp.iter_content = _slow_iter

        session.get = MagicMock(side_effect=[folder_resp, file_resp])
        session.headers = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)

        async def _timeout_wait_for(coro, *, timeout=None):
            """Force TimeoutError for the streaming wait_for call."""
            # Consume the coroutine to avoid warnings
            try:
                coro.close()
            except AttributeError:
                pass
            raise asyncio.TimeoutError()

        with (
            patch("requests.Session", return_value=session),
            patch("gdrive_downloader.metrics"),
            patch("gdrive_downloader.audit"),
            patch("asyncio.wait_for", side_effect=_timeout_wait_for),
        ):
            result = await _try_requests_parse("FOLDER_ID_123", tmp_path)

        # Timeout causes file to be skipped, so no files returned
        assert result is None

    @pytest.mark.asyncio
    async def test_audit_called_per_file(self, tmp_path):
        """audit.log_access called for each downloaded file."""
        folder_html = _make_folder_html([("C" * 33, "audit_test.pdf")])
        session = _make_mock_session(folder_html, b"AUDIT_TEST_CONTENT")

        with (
            patch("requests.Session", return_value=session),
            patch("gdrive_downloader.metrics"),
            patch("audit.log_access") as mock_log_access,
        ):
            result = await _try_requests_parse("FOLDER_ID_123", tmp_path)

        assert result is not None
        mock_log_access.assert_called_once()
        entry = mock_log_access.call_args[0][0]
        assert entry.event_type == "document_saved"
        assert entry.fonte == "google_drive"
        assert entry.status == "success"
        assert entry.tamanho_bytes > 0
        assert entry.checksum_sha256 is not None


# ─────────────────────────────────────────────
# _try_playwright_download
# ─────────────────────────────────────────────


def _build_pw_mock(output_dir: Path, filenames: list[str]):
    """Build full async_playwright mock chain for GDrive folder download."""
    # Create file links with hrefs
    links = []
    for i, fname in enumerate(filenames):
        link = AsyncMock()
        fid = f"FILE_ID_{i:03d}_{'X' * 24}"
        link.get_attribute = AsyncMock(return_value=f"/file/d/{fid}/view")
        links.append(link)

    # Main page mock (folder listing)
    page = AsyncMock()
    page.goto = AsyncMock()
    page.locator = MagicMock()  # Playwright locator() is sync

    file_links_locator = MagicMock()
    file_links_locator.all = AsyncMock(return_value=links)
    page.locator.return_value = file_links_locator

    # Build download pages with expect_download
    dl_pages = []
    for i, fname in enumerate(filenames):
        # Use MagicMock base so .locator() stays sync (Playwright convention)
        dl_page = MagicMock()
        dl_page.goto = AsyncMock()
        dl_page.close = AsyncMock()

        download = AsyncMock()
        download.suggested_filename = fname
        download.save_as = AsyncMock(
            side_effect=lambda path, content=b"PW_CONTENT", _fname=fname: Path(
                path
            ).write_bytes(content)
        )

        # expect_download as async context manager:
        # `async with dl_page.expect_download(...) as dl_info:`
        # then `download = await dl_info.value`
        # dl_info.value must be awaitable and resolve to download
        async def _value_coro():
            return download

        dl_info = MagicMock()
        dl_info.value = _value_coro()

        dl_cm = AsyncMock()
        dl_cm.__aenter__ = AsyncMock(return_value=dl_info)
        dl_cm.__aexit__ = AsyncMock(return_value=False)

        dl_page.expect_download = MagicMock(return_value=dl_cm)
        dl_pages.append(dl_page)

    # Context mock
    context = AsyncMock()
    page_calls = [page] + dl_pages
    context.new_page = AsyncMock(side_effect=page_calls)

    # Browser mock
    browser = AsyncMock()
    browser.new_context = AsyncMock(return_value=context)
    browser.close = AsyncMock()

    # Playwright mock
    pw = AsyncMock()
    pw.chromium = AsyncMock()
    pw.chromium.launch = AsyncMock(return_value=browser)

    # async_playwright() context manager
    pw_cm = AsyncMock()
    pw_cm.__aenter__ = AsyncMock(return_value=pw)
    pw_cm.__aexit__ = AsyncMock(return_value=False)

    return pw_cm


class TestTryPlaywrightDownload:
    @pytest.mark.asyncio
    async def test_success_download(self, tmp_path):
        """Downloads files via Playwright browser."""
        pw_cm = _build_pw_mock(tmp_path, ["report.pdf"])

        with (
            patch("playwright.async_api.async_playwright", return_value=pw_cm),
            patch("gdrive_downloader.metrics"),
            patch("gdrive_downloader.audit"),
        ):
            result = await _try_playwright_download(
                "https://drive.google.com/drive/folders/ABC123", tmp_path
            )

        assert result is not None
        assert len(result) == 1
        assert result[0]["nome"] == "report.pdf"
        assert result[0]["fonte"] == "google_drive"

    @pytest.mark.asyncio
    async def test_timeout(self, tmp_path):
        """Handles download timeout gracefully."""
        # Build a mock where expect_download raises TimeoutError
        link = AsyncMock()
        link.get_attribute = AsyncMock(
            return_value="/file/d/FILE_ID_TIMEOUT_XXXXXXXXXX/view"
        )

        page = MagicMock()
        page.goto = AsyncMock()
        file_links_loc = MagicMock()
        file_links_loc.all = AsyncMock(return_value=[link])
        page.locator = MagicMock(return_value=file_links_loc)

        dl_page = MagicMock()
        # expect_download raises TimeoutError
        dl_cm = AsyncMock()
        dl_cm.__aenter__ = AsyncMock(side_effect=TimeoutError("Download timed out"))
        dl_cm.__aexit__ = AsyncMock(return_value=False)
        dl_page.expect_download = MagicMock(return_value=dl_cm)
        dl_page.goto = AsyncMock()
        dl_page.close = AsyncMock()

        # Confirm button path also fails
        btn_loc = MagicMock()
        btn_loc.count = AsyncMock(return_value=0)
        dl_page.locator = MagicMock(return_value=btn_loc)

        context = MagicMock()
        context.new_page = AsyncMock(side_effect=[page, dl_page])

        browser = MagicMock()
        browser.new_context = AsyncMock(return_value=context)
        browser.close = AsyncMock()

        pw = MagicMock()
        pw.chromium = MagicMock()
        pw.chromium.launch = AsyncMock(return_value=browser)

        pw_cm = MagicMock()
        pw_cm.__aenter__ = AsyncMock(return_value=pw)
        pw_cm.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("playwright.async_api.async_playwright", return_value=pw_cm),
            patch("gdrive_downloader.metrics"),
            patch("gdrive_downloader.audit"),
        ):
            result = await _try_playwright_download(
                "https://drive.google.com/drive/folders/ABC123", tmp_path
            )

        # No files downloaded due to timeout — returns None
        assert result is None

    @pytest.mark.asyncio
    async def test_audit_called(self, tmp_path):
        """audit.log_access called after Playwright download."""
        pw_cm = _build_pw_mock(tmp_path, ["audited.pdf"])

        with (
            patch("playwright.async_api.async_playwright", return_value=pw_cm),
            patch("gdrive_downloader.metrics"),
            patch("audit.log_access") as mock_log_access,
        ):
            result = await _try_playwright_download(
                "https://drive.google.com/drive/folders/ABC123", tmp_path
            )

        assert result is not None
        mock_log_access.assert_called_once()
        entry = mock_log_access.call_args[0][0]
        assert entry.event_type == "document_saved"
        assert entry.fonte == "google_drive"
        assert entry.status == "success"

    @pytest.mark.asyncio
    async def test_playwright_not_installed(self, tmp_path):
        """Returns None when playwright is not installed."""
        with patch.dict("sys.modules", {"playwright.async_api": None}):
            result = await _try_playwright_download(
                "https://drive.google.com/drive/folders/ABC123", tmp_path
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_no_file_links_found(self, tmp_path):
        """Returns None when page has no file links."""
        page = MagicMock()
        page.goto = AsyncMock()
        empty_loc = MagicMock()
        empty_loc.all = AsyncMock(return_value=[])
        page.locator = MagicMock(return_value=empty_loc)

        context = MagicMock()
        context.new_page = AsyncMock(return_value=page)

        browser = MagicMock()
        browser.new_context = AsyncMock(return_value=context)
        browser.close = AsyncMock()

        pw = MagicMock()
        pw.chromium = MagicMock()
        pw.chromium.launch = AsyncMock(return_value=browser)

        pw_cm = MagicMock()
        pw_cm.__aenter__ = AsyncMock(return_value=pw)
        pw_cm.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("playwright.async_api.async_playwright", return_value=pw_cm),
            patch("gdrive_downloader.metrics"),
        ):
            result = await _try_playwright_download(
                "https://drive.google.com/drive/folders/ABC123", tmp_path
            )

        assert result is None
