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

# Audit sync — CNJ 615/2025 Phase 2 (Railway Postgres redundancy).
# Default disabled. Dashboard syncs local /data/audit/*.jsonl to audit_entries.
DATABASE_URL = os.getenv("DATABASE_URL", "")
AUDIT_SYNC_ENABLED = os.getenv("AUDIT_SYNC_ENABLED", "false").lower() == "true"
AUDIT_SYNC_INTERVAL_SECS = int(os.getenv("AUDIT_SYNC_INTERVAL_SECS", "300"))
AUDIT_SYNC_BATCH_SIZE = int(os.getenv("AUDIT_SYNC_BATCH_SIZE", "100"))
AUDIT_SYNC_CATCHUP_DAYS = int(os.getenv("AUDIT_SYNC_CATCHUP_DAYS", "7"))
AUDIT_SYNC_AUTO_MIGRATE = (
    os.getenv("AUDIT_SYNC_AUTO_MIGRATE", "false").lower() == "true"
)
AUDIT_SYNC_DRAIN_TIMEOUT_SECS = float(os.getenv("AUDIT_SYNC_DRAIN_TIMEOUT_SECS", "5.0"))

# ─────────────────────────────────────────────
# Runtime timeouts & thresholds (Sprint 2 Q4 — extracted from inline literals)
# ─────────────────────────────────────────────
# Previously hardcoded inside worker.py / dashboard_api.py. Exposing as env-
# configurable constants lets ops retune without a code deploy during tribunal
# slowness windows or Redis degradation events. Names chosen to make their
# meaning self-evident on a Grafana alert page.

# Playwright per-download timeouts (ms). The "full download" button triggers
# a bulk ZIP download which can take minutes on large processes; individual
# doc downloads should be snappy. Both are hard caps: a miss here bubbles up
# as a failed document, not a hung worker.
PLAYWRIGHT_FULL_DOWNLOAD_TIMEOUT_MS = int(
    os.getenv("PLAYWRIGHT_FULL_DOWNLOAD_TIMEOUT_MS", "300000")
)  # 5 minutes
PLAYWRIGHT_INDIVIDUAL_DOWNLOAD_TIMEOUT_MS = int(
    os.getenv("PLAYWRIGHT_INDIVIDUAL_DOWNLOAD_TIMEOUT_MS", "30000")
)  # 30 seconds

# Redis queue-consumer tuning. BLPOP timeout is the max per-iteration wait;
# CIRCUIT_THRESHOLD is the # of consecutive BLPOP errors before the worker
# marks itself unreachable (triggers PjeWorkerCircuitBreakerOpen alert via
# the /health 503 path).
REDIS_BLPOP_TIMEOUT_SECS = int(os.getenv("REDIS_BLPOP_TIMEOUT_SECS", "5"))
REDIS_CIRCUIT_THRESHOLD = int(os.getenv("REDIS_CIRCUIT_THRESHOLD", "20"))

# MNI health is polled lazily inside /health responses. Cache TTL prevents a
# slow MNI SOAP probe from blocking the orchestrator — short cache means a
# tribunal outage becomes visible in ~30 s; long cache hides intermittents.
MNI_HEALTH_CACHE_TTL_SECS = int(os.getenv("MNI_HEALTH_CACHE_TTL_SECS", "30"))

# Dashboard result-polling. RESULT_WAIT_TIMEOUT_SECS is the max time a single
# process can occupy the queue before the orchestrator times it out and marks
# it failed (prevents a hung worker from blocking the whole batch).
# RESULT_POLL_BLPOP_TIMEOUT_SECS is per-iteration BLPOP inside the poll loop.
RESULT_WAIT_TIMEOUT_SECS = int(os.getenv("RESULT_WAIT_TIMEOUT_SECS", "360"))
RESULT_POLL_BLPOP_TIMEOUT_SECS = int(os.getenv("RESULT_POLL_BLPOP_TIMEOUT_SECS", "5"))

# Redis socket read deadline. MUST exceed every blocking command issued on the
# connection, or that command can never complete normally.
#
# redis-py 8.0.0 (Dependabot #24, 4da8899) flipped AbstractConnection's
# socket_timeout default from None to 5 — silently, and exactly onto the 5s both
# BLPOP sites above use. A BLPOP whose timeout >= socket_timeout ALWAYS loses the
# race: the read deadline fires before the server's nil reply lands, so
# read_response raises TimeoutError instead of returning None (redis/asyncio/
# connection.py:778). Measured in prod: BLPOP(3)->None@3.02s, BLPOP(5)->raise@5.01s,
# BLPOP(8)->raise@5.01s. Every empty-queue poll raised; the worker's circuit
# breaker tripped to redis_unreachable and the dashboard failed batches whose
# files were already on disk.
#
# Derived (not hardcoded) so raising either BLPOP timeout lifts this in lockstep.
# Never set this to None: an unbounded read means a dead TCP connection hangs
# forever and the circuit breaker never trips.
REDIS_SOCKET_TIMEOUT_MARGIN_SECS = float(
    os.getenv("REDIS_SOCKET_TIMEOUT_MARGIN_SECS", "10")
)
REDIS_MAX_BLOCKING_TIMEOUT_SECS = max(
    REDIS_BLPOP_TIMEOUT_SECS, RESULT_POLL_BLPOP_TIMEOUT_SECS
)
REDIS_SOCKET_TIMEOUT_SECS = float(
    os.getenv(
        "REDIS_SOCKET_TIMEOUT_SECS",
        str(REDIS_MAX_BLOCKING_TIMEOUT_SECS + REDIS_SOCKET_TIMEOUT_MARGIN_SECS),
    )
)

# Per-batch absolute timeout ceiling — prevents a stuck poll loop from running forever (H2).
BATCH_MAX_DURATION_SECS = int(os.getenv("BATCH_MAX_DURATION_SECS", "3600"))

# TTL for `kratos:pje:results:<batch_id>` reply queues.
#
# The dashboard deletes the reply queue in the `finally` of _run_batch, but that
# runs in-process: a container restart / redeploy while a batch is still polling
# skips it entirely and — with no TTL — the key lives forever. Prod 2026-07-18
# had 4 such orphans (`ttl=-1`, 48 undrained messages) stranded when wedged
# batches were redeployed out from under the poll loop.
#
# The worker refreshes this TTL on every write, so the window only starts
# decaying after the LAST message; an abandoned queue self-cleans, a live one
# never expires. MUST exceed BATCH_MAX_DURATION_SECS — a shorter TTL would drop
# a live batch's undrained results and resurrect the "batch failed but the files
# are on disk" symptom. Derived so raising the batch ceiling lifts it in lockstep.
REDIS_RESULT_QUEUE_TTL_MARGIN_SECS = int(
    os.getenv("REDIS_RESULT_QUEUE_TTL_MARGIN_SECS", "1800")
)

# The TTL must outlive a CRASH, not merely a batch. `resume_active_batch` re-enters
# `_run_batch(enqueue_jobs=False)`, and `_enqueue_batch` then skips both the queue
# delete and the job re-publish — so resume *depends on the undrained reply queue
# still existing*. Nothing re-arms the TTL while the dashboard is down: the window
# decays from the worker's LAST WRITE. An outage longer than the TTL therefore
# loses every undrained result and re-queues nothing, and the batch is marked
# failed with its files already on disk.
#
# Sizing this to the batch ceiling alone would trade an unbounded leak for silent
# loss across any overnight incident. The floor below is what makes the fix a net
# win rather than a swap of one failure for another; the leak stays bounded because
# the key still expires within a day.
REDIS_RESULT_QUEUE_TTL_FLOOR_SECS = int(
    os.getenv("REDIS_RESULT_QUEUE_TTL_FLOOR_SECS", str(24 * 3600))
)
REDIS_RESULT_QUEUE_TTL_SECS = int(
    os.getenv(
        "REDIS_RESULT_QUEUE_TTL_SECS",
        str(
            max(
                BATCH_MAX_DURATION_SECS + REDIS_RESULT_QUEUE_TTL_MARGIN_SECS,
                REDIS_RESULT_QUEUE_TTL_FLOOR_SECS,
            )
        ),
    )
)

# The derivation above is only the DEFAULT — both sides are independently
# env-overridable, so a deploy can set REDIS_RESULT_QUEUE_TTL_SECS explicitly (or
# raise BATCH_MAX_DURATION_SECS and forget the TTL) and quietly invert the
# relationship. The test suite would not notice: it imports these with their
# defaults. Fail fast at import instead, because the failure this guards against
# is silent — a queue expiring mid-batch drops undrained results and the batch is
# reported failed with its files sitting on disk.
if REDIS_RESULT_QUEUE_TTL_SECS <= BATCH_MAX_DURATION_SECS:
    raise ValueError(
        f"REDIS_RESULT_QUEUE_TTL_SECS ({REDIS_RESULT_QUEUE_TTL_SECS}s) must exceed "
        f"BATCH_MAX_DURATION_SECS ({BATCH_MAX_DURATION_SECS}s): a reply queue that "
        f"expires before its batch can finish silently discards undrained results."
    )

# Disk free-space floor in MB — worker returns 503 / marks disk "low" below this (M7).
DISK_LOW_THRESHOLD_MB = int(os.getenv("DISK_LOW_THRESHOLD_MB", "2000"))
