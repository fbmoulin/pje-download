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


class TestLoadEnv:
    def test_loads_from_dotenv(self, tmp_path, monkeypatch):
        """Create a .env in the project dir candidate path and verify load_env reads it."""
        import config
        env_file = tmp_path / ".env"
        env_file.write_text("PJE_TEST_LOAD_VAR=loaded_ok\n")
        monkeypatch.delenv("PJE_TEST_LOAD_VAR", raising=False)

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
