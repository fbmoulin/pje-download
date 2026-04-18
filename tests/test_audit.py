"""Tests for audit.py — CNJ 615/2025 audit trail."""

import json
import stat
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from audit import AuditEntry, get_audit_dir, log_access, rotate_logs


def _make_entry(**overrides) -> AuditEntry:
    defaults = {
        "event_type": "document_saved",
        "processo_numero": "0001234-56.2024.8.08.0001",
        "fonte": "mni_soap",
        "tribunal": "TJES",
        "status": "success",
    }
    defaults.update(overrides)
    return AuditEntry(**defaults)


class TestLogAccess:
    def test_creates_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AUDIT_LOG_DIR", str(tmp_path))
        entry = _make_entry()
        log_access(entry)

        files = list(tmp_path.glob("audit-*.jsonl"))
        assert len(files) == 1
        assert files[0].name == f"audit-{date.today()}.jsonl"

    def test_appends_not_overwrites(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AUDIT_LOG_DIR", str(tmp_path))
        log_access(_make_entry(documento_id="DOC1"))
        log_access(_make_entry(documento_id="DOC2"))

        path = tmp_path / f"audit-{date.today()}.jsonl"
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["documento_id"] == "DOC1"
        assert json.loads(lines[1])["documento_id"] == "DOC2"

    def test_daily_filename(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AUDIT_LOG_DIR", str(tmp_path))
        log_access(_make_entry())

        today = date.today().isoformat()
        assert (tmp_path / f"audit-{today}.jsonl").exists()

    @pytest.mark.skipif(sys.platform == "win32", reason="Unix permissions")
    def test_file_permissions_0600(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AUDIT_LOG_DIR", str(tmp_path))
        log_access(_make_entry())

        path = tmp_path / f"audit-{date.today()}.jsonl"
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600

    def test_thread_safety(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AUDIT_LOG_DIR", str(tmp_path))
        errors = []

        def write_entry(i):
            try:
                log_access(_make_entry(documento_id=f"DOC{i}"))
            except Exception as exc:
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=10) as pool:
            list(pool.map(write_entry, range(20)))

        assert errors == []
        path = tmp_path / f"audit-{date.today()}.jsonl"
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 20

    def test_schema_fields_serialized(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AUDIT_LOG_DIR", str(tmp_path))
        entry = _make_entry(
            documento_id="DOC1",
            documento_tipo="sentenca",
            documento_nome="decisao.pdf",
            tamanho_bytes=1024,
            checksum_sha256="abc123",
            batch_id="B001",
            client_ip="10.0.0.1",
            api_key_hash="hash16chars",
            erro=None,
            duracao_s=1.5,
        )
        log_access(entry)

        path = tmp_path / f"audit-{date.today()}.jsonl"
        data = json.loads(path.read_text(encoding="utf-8").strip())
        assert data["event_type"] == "document_saved"
        assert data["processo_numero"] == "0001234-56.2024.8.08.0001"
        assert data["documento_id"] == "DOC1"
        assert data["tamanho_bytes"] == 1024
        assert data["duracao_s"] == 1.5
        assert data["erro"] is None
        assert "timestamp" in data

    def test_never_raises_on_bad_path(self, monkeypatch):
        monkeypatch.setenv("AUDIT_LOG_DIR", "/nonexistent/path/that/should/fail")
        # Must not raise — audit failures must not break downloads
        log_access(_make_entry())

    def test_utf8_content(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AUDIT_LOG_DIR", str(tmp_path))
        log_access(_make_entry(documento_nome="decisão_açúcar.pdf"))

        path = tmp_path / f"audit-{date.today()}.jsonl"
        data = json.loads(path.read_text(encoding="utf-8").strip())
        assert data["documento_nome"] == "decisão_açúcar.pdf"


class TestRotateLogs:
    def test_deletes_old_files(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AUDIT_LOG_DIR", str(tmp_path))
        old_date = date.today() - timedelta(days=100)
        old_file = tmp_path / f"audit-{old_date}.jsonl"
        old_file.write_text("{}\n")

        deleted = rotate_logs(max_days=90)
        assert deleted == 1
        assert not old_file.exists()

    def test_removes_orphan_cursor_sidecar(self, tmp_path, monkeypatch):
        """Audit P2: rotate_logs deletava apenas o .jsonl, deixando o
        .cursor orfao em /data/audit. Ao longo de anos isso acumula
        lixo; o cleanup do sidecar e necessario quando o arquivo
        original foi removido.
        """
        monkeypatch.setenv("AUDIT_LOG_DIR", str(tmp_path))
        old_date = date.today() - timedelta(days=100)
        old_file = tmp_path / f"audit-{old_date}.jsonl"
        old_cursor = tmp_path / f"audit-{old_date}.jsonl.cursor"
        old_file.write_text("{}\n")
        old_cursor.write_text('{"offset": 3}')

        rotate_logs(max_days=90)

        assert not old_file.exists()
        assert not old_cursor.exists(), (
            "cursor sidecar orfao nao foi removido junto com o .jsonl"
        )

    def test_preserves_cursor_for_recent_file(self, tmp_path, monkeypatch):
        """Regression: cursor de arquivo recente NAO pode ser removido."""
        monkeypatch.setenv("AUDIT_LOG_DIR", str(tmp_path))
        recent_date = date.today() - timedelta(days=10)
        recent_file = tmp_path / f"audit-{recent_date}.jsonl"
        recent_cursor = tmp_path / f"audit-{recent_date}.jsonl.cursor"
        recent_file.write_text("{}\n")
        recent_cursor.write_text('{"offset": 3}')

        rotate_logs(max_days=90)

        assert recent_file.exists()
        assert recent_cursor.exists()

    def test_keeps_recent_files(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AUDIT_LOG_DIR", str(tmp_path))
        recent_date = date.today() - timedelta(days=10)
        recent_file = tmp_path / f"audit-{recent_date}.jsonl"
        recent_file.write_text("{}\n")

        deleted = rotate_logs(max_days=90)
        assert deleted == 0
        assert recent_file.exists()

    def test_keeps_today(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AUDIT_LOG_DIR", str(tmp_path))
        today_file = tmp_path / f"audit-{date.today()}.jsonl"
        today_file.write_text("{}\n")

        deleted = rotate_logs(max_days=0)
        assert deleted == 0
        assert today_file.exists()

    def test_ignores_non_audit_files(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AUDIT_LOG_DIR", str(tmp_path))
        other = tmp_path / "audit-notadate.jsonl"
        other.write_text("{}\n")

        deleted = rotate_logs(max_days=0)
        assert deleted == 0
        assert other.exists()


class TestGetAuditDir:
    def test_default(self, monkeypatch):
        monkeypatch.delenv("AUDIT_LOG_DIR", raising=False)
        with patch.object(Path, "mkdir"):
            assert get_audit_dir() == Path("/data/audit")

    def test_custom(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AUDIT_LOG_DIR", str(tmp_path / "custom"))
        d = get_audit_dir()
        assert d == tmp_path / "custom"
        assert d.is_dir()
