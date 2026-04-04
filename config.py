"""Shared configuration and validation for pje-download."""

import os
import re
from pathlib import Path

# CNJ process number format: NNNNNNN-DD.YYYY.J.TR.OOOO
CNJ_PATTERN = re.compile(r"^\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}$")


def load_env() -> None:
    """Load .env from kratos-master config (Windows or relative path)."""
    candidates = [
        Path(r"C:\projetos-2026\kratos-master\config\.env"),
        Path(__file__).resolve().parent.parent / "kratos-master" / "config" / ".env",
        Path(__file__).resolve().parent / ".env",
    ]
    for env_path in candidates:
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    val = re.split(r"\s+#\s", val, maxsplit=1)[0].strip()
                    os.environ.setdefault(key.strip(), val)
            return


def is_valid_processo(numero: str) -> bool:
    """Validate CNJ process number format."""
    return bool(CNJ_PATTERN.match(numero.strip()))


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
SESSION_TIMEOUT_MINUTES = int(os.getenv("SESSION_TIMEOUT_MINUTES", "60"))
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
MAX_DOCS_PER_SESSION = int(os.getenv("MAX_DOCS_PER_SESSION", "50"))
DOWNLOAD_DELAY_SECS = float(os.getenv("DOWNLOAD_DELAY_SECS", "1.5"))
CONCURRENT_DOWNLOADS = int(os.getenv("CONCURRENT_DOWNLOADS", "3"))
HEALTH_PORT = int(os.getenv("HEALTH_PORT", "8006"))
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

# Dashboard
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8007"))
DASHBOARD_API_KEY = os.getenv("DASHBOARD_API_KEY", "")
