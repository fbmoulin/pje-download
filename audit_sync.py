"""Audit sync to Railway Postgres — Phase 2 of CNJ 615/2025.

Local /data/audit/audit-YYYY-MM-DD.jsonl remains the source of truth.
This module tails those files, batches completed lines, and appends them
to an `audit_entries` table on a Railway-hosted Postgres as a redundant sink.

Correctness invariant
---------------------
A JSON-L line is **only** considered complete when it ends with "\\n".
A trailing partial write (no terminator) MUST stop cursor advance at the
byte before it — otherwise we lose lines that are still being written.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

try:
    import metrics as _metrics
except ImportError:  # pragma: no cover — only happens in isolated unit tests
    _metrics = None

logger = logging.getLogger("kratos.audit_sync")

_FILE_DATE_RE = re.compile(r"^audit-(\d{4}-\d{2}-\d{2})\.jsonl$")

_INSERT_SQL = """
INSERT INTO audit_entries (
    event_type, processo_numero, fonte, tribunal, status, ts,
    documento_id, documento_tipo, documento_nome,
    tamanho_bytes, checksum_sha256, batch_id, client_ip,
    api_key_hash, erro, duracao_s, raw
) VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17
) ON CONFLICT (ts, event_type, processo_numero, documento_id) DO NOTHING
"""

_URL_PASSWORD_RE = re.compile(r"(://[^:/@?#]+):([^@]+)@")


def _scrub_url(url: str) -> str:
    """Redact the password portion of a URL so it is safe to log."""
    return _URL_PASSWORD_RE.sub(r"\1:***@", url)


def _cursor_path(jsonl_path: Path) -> Path:
    """Return the sidecar cursor path for a JSON-L audit file."""
    return jsonl_path.with_suffix(jsonl_path.suffix + ".cursor")


def _save_cursor(jsonl_path: Path, offset: int) -> None:
    """Atomically persist ``offset`` as the synced position of ``jsonl_path``.

    Write to ``.cursor.tmp``, fsync, then rename. On any error the cursor
    stays untouched — the next tick will retry the same offset (idempotent).
    """
    cursor = _cursor_path(jsonl_path)
    tmp = cursor.with_suffix(cursor.suffix + ".tmp")
    payload = json.dumps(
        {"offset": int(offset), "saved_at": datetime.now(UTC).isoformat()}
    )
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, cursor)


def _load_cursor(jsonl_path: Path) -> int:
    """Return the saved offset for ``jsonl_path``, or 0 on any failure.

    Clamps to filesize if the cursor ever exceeds the current file length
    (e.g. manual truncation, disk corruption) — the next tick will re-sync
    from the start; Postgres dedupe handles duplicates.
    """
    cursor = _cursor_path(jsonl_path)
    if not cursor.exists():
        return 0
    try:
        data = json.loads(cursor.read_text(encoding="utf-8"))
        offset = int(data["offset"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0
    try:
        filesize = jsonl_path.stat().st_size
    except OSError:
        return 0
    if offset < 0 or offset > filesize:
        return 0
    return offset


def _parse_complete_lines(data: bytes) -> tuple[list[dict], int, int]:
    """Parse only \\n-terminated JSON-L lines from ``data``.

    Returns ``(parsed, bytes_consumed, malformed_count)``. ``bytes_consumed``
    stops **before** any trailing partial line so the cursor never advances
    past an unterminated write in flight.
    """
    parsed: list[dict] = []
    malformed = 0
    ptr = 0
    while True:
        nl = data.find(b"\n", ptr)
        if nl == -1:
            break
        raw = data[ptr:nl]
        ptr = nl + 1
        if not raw.strip():
            continue
        try:
            parsed.append(json.loads(raw.decode("utf-8")))
        except (json.JSONDecodeError, UnicodeDecodeError):
            malformed += 1
    return parsed, ptr, malformed


def _parse_file_date(filename: str) -> date | None:
    """Extract the date from ``audit-YYYY-MM-DD.jsonl``; None if malformed."""
    m = _FILE_DATE_RE.match(filename)
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(1))
    except ValueError:
        return None


def _row_to_params(row: dict) -> tuple:
    """Convert a parsed audit entry dict to the positional tuple expected
    by ``_INSERT_SQL`` ($1..$17). Missing fields become ``None``.
    """
    ts_raw = row.get("timestamp")
    ts_value: datetime | None = None
    if ts_raw:
        try:
            ts_value = datetime.fromisoformat(ts_raw)
        except (TypeError, ValueError):
            ts_value = None
    return (
        row.get("event_type"),
        row.get("processo_numero"),
        row.get("fonte"),
        row.get("tribunal"),
        row.get("status"),
        ts_value,
        row.get("documento_id"),
        row.get("documento_tipo"),
        row.get("documento_nome"),
        row.get("tamanho_bytes"),
        row.get("checksum_sha256"),
        row.get("batch_id"),
        row.get("client_ip"),
        row.get("api_key_hash"),
        row.get("erro"),
        row.get("duracao_s"),
        json.dumps(row, ensure_ascii=False),
    )


class AuditSyncer:
    """Background syncer from local JSON-L audit files to Railway Postgres.

    Constructed via :func:`create_syncer`. The pool is lazy — nothing
    connects to Postgres until :meth:`run_forever` starts.
    """

    _MAX_INSERT_ATTEMPTS = 5

    def __init__(
        self,
        *,
        database_url: str,
        audit_dir: Path,
        interval_secs: int,
        batch_size: int,
        catchup_days: int,
        drain_timeout_secs: float,
        auto_migrate: bool,
    ) -> None:
        self.database_url = database_url
        self.audit_dir = audit_dir
        self.interval_secs = interval_secs
        self.batch_size = batch_size
        self.catchup_days = catchup_days
        self.drain_timeout_secs = drain_timeout_secs
        self.auto_migrate = auto_migrate
        self._pool = None
        self._last_error: str | None = None
        self._last_synced_event_ts: datetime | None = None
        self._last_tick_at: datetime | None = None
        self._rows_total = 0
        self.shutdown = asyncio.Event()

    def __repr__(self) -> str:
        return (
            f"AuditSyncer(url={_scrub_url(self.database_url)!r}, "
            f"audit_dir={str(self.audit_dir)!r}, "
            f"interval={self.interval_secs}s, batch={self.batch_size})"
        )

    # ── public lifecycle ───────────────────────────────────────────────

    async def run_forever(self) -> None:
        """Supervisor loop. Never raises out; individual tick exceptions
        are caught, logged, and backed off — local JSON-L keeps the data
        while Postgres recovers.
        """
        while not self.shutdown.is_set():
            t0 = datetime.now(UTC)
            try:
                await self._tick()
                self._last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "audit_sync.tick_failed",
                    extra={"error": self._last_error},
                    exc_info=True,
                )
                if _metrics is not None:
                    _metrics.audit_sync_batches_total.labels(status="failed").inc()
            dt = (datetime.now(UTC) - t0).total_seconds()
            if _metrics is not None:
                _metrics.audit_sync_latency_seconds.observe(dt)
            self._last_tick_at = datetime.now(UTC)
            try:
                await asyncio.wait_for(self.shutdown.wait(), timeout=self.interval_secs)
            except asyncio.TimeoutError:
                pass

    async def close(self) -> None:
        """Release the asyncpg pool (if any). Safe to call many times."""
        pool = self._pool
        self._pool = None
        if pool is not None:
            try:
                await pool.close()
            except Exception:
                logger.warning("audit_sync.pool_close_failed", exc_info=True)

    async def init_schema(
        self,
        sql_path: Path | None = None,
    ) -> None:
        """Apply the idempotent audit_entries DDL. Requires admin role
        privileges — typically run once, then the running DATABASE_URL
        is rotated to an insert-only role.
        """
        await self._ensure_pool()
        path = sql_path or (
            Path(__file__).resolve().parent / "migrations" / "001_audit_entries.sql"
        )
        sql = path.read_text(encoding="utf-8")
        async with self._pool.acquire() as conn:
            await conn.execute(sql)
        logger.info(
            "audit_sync.schema_initialised",
            extra={"migration": str(path)},
        )

    def health_snapshot(self) -> dict:
        """Serialisable status summary for ``/healthz``."""
        lag = None
        if self._last_synced_event_ts is not None:
            lag = (datetime.now(UTC) - self._last_synced_event_ts).total_seconds()
        return {
            "enabled": True,
            "lag_seconds_event_time": lag,
            "last_error": self._last_error,
            "last_tick_at": (
                self._last_tick_at.isoformat() if self._last_tick_at else None
            ),
            "rows_total": self._rows_total,
            "url": _scrub_url(self.database_url),
        }

    # ── internals (overridable in tests) ───────────────────────────────

    async def _ensure_pool(self) -> None:
        """Lazy-create the asyncpg pool, respecting the URL's ``sslmode``.

        - ``sslmode=verify-full`` → strict: use the system CA bundle and
          enforce hostname match (recommended for self-hosted Postgres).
        - ``sslmode=require`` or anything else → delegate to asyncpg,
          which gives you TLS without chain verification. This is what
          managed providers like Railway need because their TCP proxies
          present self-signed chains.
        """
        if self._pool is not None:
            return
        from urllib.parse import parse_qs, urlparse

        import asyncpg

        sslmode = (
            parse_qs(urlparse(self.database_url).query)
            .get("sslmode", [""])[0]
            .lower()
        )
        if sslmode in ("verify-full", "verify-ca"):
            import ssl

            ctx = ssl.create_default_context()
            ctx.check_hostname = sslmode == "verify-full"
            ssl_arg = ctx
        else:
            ssl_arg = None  # asyncpg reads sslmode from URL directly
        self._pool = await asyncpg.create_pool(
            self.database_url, min_size=1, max_size=3, ssl=ssl_arg
        )

    async def _insert_batch(self, rows: list[dict]) -> None:
        """Insert ``rows`` via a single ``executemany`` with dedupe.

        Retries up to ``_MAX_INSERT_ATTEMPTS`` with exponential backoff.
        Raises on final failure so the caller can leave the cursor in place.
        """
        params = [_row_to_params(r) for r in rows]
        last_exc: Exception | None = None
        for attempt in range(self._MAX_INSERT_ATTEMPTS):
            try:
                async with self._pool.acquire() as conn:
                    await conn.executemany(_INSERT_SQL, params)
                if _metrics is not None:
                    _metrics.audit_sync_rows_total.labels(status="success").inc(
                        len(rows)
                    )
                return
            except Exception as exc:  # noqa: BLE001 — retry any DB error
                last_exc = exc
                if attempt == self._MAX_INSERT_ATTEMPTS - 1:
                    break
                delay = min(2**attempt + random.uniform(0, 1), 30)
                if _metrics is not None:
                    _metrics.audit_sync_batches_total.labels(status="retry").inc()
                logger.warning(
                    "audit_sync.insert_retry",
                    extra={"attempt": attempt + 1, "delay_s": delay},
                )
                await asyncio.sleep(delay)
        if _metrics is not None:
            _metrics.audit_sync_rows_total.labels(status="failed").inc(len(rows))
        raise last_exc or RuntimeError("insert_batch: unknown failure")

    # ── tick orchestration ─────────────────────────────────────────────

    async def _tick(self) -> None:
        """One sweep over files within the catchup window."""
        await self._ensure_pool()
        today = date.today()
        oldest_allowed = today - timedelta(days=self.catchup_days)
        files = sorted(self.audit_dir.glob("audit-*.jsonl"))
        for path in files:
            if self.shutdown.is_set():
                return
            file_date = _parse_file_date(path.name)
            if file_date is None or file_date < oldest_allowed:
                continue
            await self._sync_file(path)

    async def _sync_file(self, jsonl_path: Path) -> None:
        """Read pending bytes from ``jsonl_path`` and push them to Postgres."""
        offset = _load_cursor(jsonl_path)
        try:
            filesize = jsonl_path.stat().st_size
        except FileNotFoundError:
            if _metrics is not None:
                _metrics.audit_sync_files_vanished_total.inc()
            logger.info(
                "audit_sync.file_vanished",
                extra={"path": str(jsonl_path)},
            )
            return
        if offset >= filesize:
            return
        max_bytes = self.batch_size * 10 * 2048
        read_size = min(filesize - offset, max_bytes)
        with jsonl_path.open("rb") as fh:
            fh.seek(offset)
            data = fh.read(read_size)

        parsed, consumed, malformed = _parse_complete_lines(data)
        if malformed and _metrics is not None:
            _metrics.audit_sync_malformed_lines_total.inc(malformed)
        if not parsed and consumed == 0:
            return

        for i in range(0, len(parsed), self.batch_size):
            chunk = parsed[i : i + self.batch_size]
            await self._insert_batch(chunk)

        _save_cursor(jsonl_path, offset + consumed)
        self._rows_total += len(parsed)

        # Track newest event timestamp for lag metric
        for row in parsed:
            ts_raw = row.get("timestamp")
            if not ts_raw:
                continue
            try:
                ts = datetime.fromisoformat(ts_raw)
            except (TypeError, ValueError):
                continue
            if self._last_synced_event_ts is None or ts > self._last_synced_event_ts:
                self._last_synced_event_ts = ts


def create_syncer(
    *,
    enabled: bool,
    database_url: str,
    audit_dir: Path,
    interval_secs: int,
    batch_size: int,
    catchup_days: int,
    retention_days: int,
    drain_timeout_secs: float,
    app_env: str,
    auto_migrate: bool = False,
) -> AuditSyncer | None:
    """Factory with validation. Returns ``None`` when sync is disabled or
    unusable (dev-time missing URL). Raises ``RuntimeError`` in production
    if configured but unusable — prevents silent compliance gaps.
    """
    if not enabled:
        return None
    if not database_url:
        msg = (
            "AUDIT_SYNC_ENABLED=true but DATABASE_URL is empty — "
            "cannot start audit syncer"
        )
        if app_env == "production":
            raise RuntimeError(msg)
        logger.warning(msg + " (dev mode: disabling)")
        return None
    if retention_days < catchup_days + 2:
        logger.warning(
            "audit-sync retention window (%d days) is shorter than "
            "catchup window (%d days) + margin (2) — rotated files may "
            "be deleted before sync completes",
            retention_days,
            catchup_days,
        )
    return AuditSyncer(
        database_url=database_url,
        audit_dir=audit_dir,
        interval_secs=interval_secs,
        batch_size=batch_size,
        catchup_days=catchup_days,
        drain_timeout_secs=drain_timeout_secs,
        auto_migrate=auto_migrate,
    )
