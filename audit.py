"""CNJ 615/2025 audit trail — append-only JSON-L log for document access."""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

logger = logging.getLogger("kratos.audit")

_lock = threading.Lock()


@dataclass
class AuditEntry:
    """Structured record of a document access event."""

    # Required
    event_type: str  # document_saved | batch_started | batch_completed | session_login
    processo_numero: str
    fonte: str  # mni_soap | pje_api | pje_browser | google_drive | batch
    tribunal: str
    status: str  # success | error | duplicate_skipped

    # Auto-filled
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    # Document context (optional)
    documento_id: str | None = None
    documento_tipo: str | None = None
    documento_nome: str | None = None

    # Integrity (optional)
    tamanho_bytes: int | None = None
    checksum_sha256: str | None = None

    # Request context (optional)
    batch_id: str | None = None
    client_ip: str | None = None
    api_key_hash: str | None = None

    # Outcome (optional)
    erro: str | None = None
    duracao_s: float | None = None


def get_audit_dir() -> Path:
    """Return audit log directory from env or default. Creates if needed."""
    d = Path(os.getenv("AUDIT_LOG_DIR", "/data/audit"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_access(entry: AuditEntry) -> None:
    """Append entry as JSON line to daily audit file. Thread-safe. Never raises."""
    try:
        audit_dir = get_audit_dir()
        path = audit_dir / f"audit-{date.today()}.jsonl"
        line = json.dumps(dataclasses.asdict(entry), ensure_ascii=False) + "\n"

        with _lock:
            if not path.exists():
                fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                with os.fdopen(fd, "a", encoding="utf-8") as f:
                    f.write(line)
            else:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(line)
    except Exception:
        logger.warning("audit.log_access.failed", exc_info=True)


def rotate_logs(max_days: int = 90) -> int:
    """Delete audit files older than max_days. Returns count deleted."""
    cutoff = date.today() - timedelta(days=max_days)
    deleted = 0
    audit_dir = get_audit_dir()

    with _lock:
        for p in audit_dir.glob("audit-*.jsonl"):
            try:
                file_date = date.fromisoformat(p.stem.removeprefix("audit-"))
                if file_date < cutoff:
                    p.unlink()
                    # Remove cursor sidecar too (audit P2 — sem isso os
                    # .cursor orfaos acumulam em /data/audit sem uso).
                    cursor = p.with_suffix(p.suffix + ".cursor")
                    try:
                        cursor.unlink()
                    except FileNotFoundError:
                        pass
                    deleted += 1
            except (ValueError, OSError):
                continue
    return deleted
