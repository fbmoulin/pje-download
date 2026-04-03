"""Tests for gdrive_downloader — extract_folder_id, is_processo_antigo, regex fixes."""

from __future__ import annotations

import re


from gdrive_downloader import extract_folder_id, is_processo_antigo


# ─────────────────────────────────────────────
# extract_folder_id
# ─────────────────────────────────────────────


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
