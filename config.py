"""Shared configuration loader for pje-download."""

import os
import re
from pathlib import Path


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
