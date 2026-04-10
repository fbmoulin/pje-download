"""Shared configuration and validation for pje-download."""

import os
import re
import hashlib
from pathlib import Path

# CNJ process number format: NNNNNNN-DD.YYYY.J.TR.OOOO
CNJ_PATTERN = re.compile(r"^\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}$")


def load_env() -> None:
    """Load environment defaults from an explicit file or the repo-local `.env`."""
    candidates: list[Path] = []
    explicit_env = os.getenv("PJE_DOWNLOAD_ENV_FILE", "").strip()
    if explicit_env:
        candidates.append(Path(explicit_env).expanduser())
    candidates.append(Path(__file__).resolve().parent / ".env")

    for env_path in candidates:
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    val = re.split(r"\s+#\s", val, maxsplit=1)[0].strip()
                    os.environ.setdefault(key.strip(), val)
            return


load_env()


def is_valid_processo(numero: str) -> bool:
    """Validate CNJ process number format."""
    return bool(CNJ_PATTERN.match(numero.strip()))


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


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> tuple[str, int]:
    """Return SHA256 and size for a file using streaming reads."""
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as fh:
        while chunk := fh.read(chunk_size):
            digest.update(chunk)
            total += len(chunk)
    return digest.hexdigest(), total


# ─────────────────────────────────────────────
# Centralized defaults (all env-configurable)
# ─────────────────────────────────────────────

# PJe / Worker
_pje_url = os.getenv("PJE_BASE_URL", "https://pje.tjes.jus.br/pje")
if _pje_url != "https://pje.tjes.jus.br/pje" and (
    not _pje_url.startswith("https://") or ".jus.br" not in _pje_url
):
    raise ValueError(f"PJE_BASE_URL must be HTTPS .jus.br URL, got: {_pje_url}")
PJE_BASE_URL = _pje_url
SESSION_STATE_PATH = Path(os.getenv("SESSION_STATE_PATH", "/data/pje-session.json"))
DOWNLOAD_BASE_DIR = Path(os.getenv("DOWNLOAD_BASE_DIR", "/data/downloads"))
AUDIT_LOG_DIR = Path(os.getenv("AUDIT_LOG_DIR", "/data/audit"))
AUDIT_LOG_RETENTION_DAYS = int(os.getenv("AUDIT_LOG_RETENTION_DAYS", "90"))
SESSION_TIMEOUT_MINUTES = int(os.getenv("SESSION_TIMEOUT_MINUTES", "60"))
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
MAX_DOCS_PER_SESSION = int(os.getenv("MAX_DOCS_PER_SESSION", "50"))
DOWNLOAD_DELAY_SECS = float(os.getenv("DOWNLOAD_DELAY_SECS", "1.5"))
CONCURRENT_DOWNLOADS = int(os.getenv("CONCURRENT_DOWNLOADS", "3"))
HEALTH_PORT = int(os.getenv("HEALTH_PORT", "8006"))
HEALTH_BIND_HOST = os.getenv("HEALTH_BIND_HOST", "127.0.0.1")
WORKER_HEALTH_HOST = os.getenv("WORKER_HEALTH_HOST", "localhost")
MNI_ENABLED = os.getenv("MNI_ENABLED", "true").lower() == "true"

# MNI Client
MNI_USERNAME = os.getenv("MNI_USERNAME", "")
MNI_PASSWORD = os.getenv("MNI_PASSWORD", "")
MNI_TRIBUNAL = os.getenv("MNI_TRIBUNAL", "TJES")
MNI_TIMEOUT = int(os.getenv("MNI_TIMEOUT", "60"))
# Proxy for MNI SOAP calls (optional — needed when VPS IP is blocked by tribunal)
# Format: http://user:pass@host:port  or  socks5://user:pass@host:port
MNI_PROXY = os.getenv("MNI_PROXY", "")

# Batch Downloader
BATCH_SIZE_DEFAULT = int(os.getenv("MNI_BATCH_SIZE", "5"))
BATCH_DELAY_DEFAULT = float(os.getenv("BATCH_DELAY_SECS", "2.0"))

# Dashboard / Runtime
APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8007"))
DASHBOARD_API_KEY = os.getenv("DASHBOARD_API_KEY", "")
# Forwarded headers are ignored unless explicitly enabled behind a trusted proxy.
TRUST_X_FORWARDED_FOR = os.getenv("TRUST_X_FORWARDED_FOR", "false").lower() == "true"
