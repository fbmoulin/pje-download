"""Retention of downloaded case files (spec docs/specs/2026-11-15-retencao-downloads.md).

Deletes the downloaded files of FINISHED dashboard batches once they are older than
N days. The deletion is irreversible, so everything here is conservative:

* only `<base>/<batch id>/` folders whose id matches the dashboard's format;
* only batches whose `_report.json` exists, is a JSON object and has a terminal status;
* a batch is old only if BOTH the report mtime and its `completed_at` are older than N days;
* a single recent file inside a batch vetoes the whole batch (a worker may still be writing);
* regular files at the batch root whose name starts with ``_`` are kept (`_report.json`,
  `_progress.json`, `_purged.json`); every directory goes, whatever its name;
* symlinks are never followed.

Nothing here logs or returns file or process names. ``/data/audit`` is not touched.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

BATCH_ID = re.compile(r"\d{8}_\d{6}_[0-9a-f]{6}", re.ASCII)
TERMINAL_STATUSES = frozenset({"done", "failed", "partial"})
ACTIVE_STATUSES = frozenset({"queued", "running"})
KEEP_PREFIX = "_"
MARKER = "_purged.json"
DAY_SECS = 86400.0
MAX_REPORT_BYTES = 5 * 1024 * 1024


@dataclass
class RetentionResult:
    candidates: int = 0
    purged: int = 0
    bytes_freed: int = 0
    errors: int = 0
    skipped_active: int = 0
    unmatched_dirs: int = 0
    error_type: str | None = None
    # One entry per candidate batch: batch id (timestamp + hex), status, age, bytes.
    # Never a file or process name.
    details: list[dict] = field(default_factory=list)


def _read_report(batch: Path) -> dict | None:
    report = batch / "_report.json"
    if report.is_symlink() or not report.is_file():
        return None
    try:
        if report.stat().st_size > MAX_REPORT_BYTES:
            return None
        data = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _completed_ts(report: dict) -> float | None:
    raw = report.get("completed_at")
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def _old_enough(batch: Path, report: dict, now: float, cutoff: float) -> bool:
    mtime = (batch / "_report.json").stat().st_mtime
    if mtime > now or mtime >= cutoff:
        return False
    completed = _completed_ts(report)
    if completed is not None and (completed > now or completed >= cutoff):
        return False
    return True


def _purgeable(batch: Path) -> list[Path]:
    """Top-level entries to delete: every directory, and non-`_` files/links."""
    out = []
    for entry in batch.iterdir():
        if entry.is_symlink():
            out.append(entry)
        elif entry.is_dir():
            out.append(entry)
        elif not entry.name.startswith(KEEP_PREFIX):
            out.append(entry)
    return out


def _has_fresh_file(targets: list[Path], cutoff: float) -> bool:
    for target in targets:
        if target.is_symlink() or not target.is_dir():
            if target.lstat().st_mtime >= cutoff:
                return True
            continue
        for root, _dirs, files in os.walk(target, followlinks=False):
            for fname in files:
                if os.lstat(os.path.join(root, fname)).st_mtime >= cutoff:
                    return True
    return False


def _size(targets: list[Path]) -> int:
    total = 0
    for target in targets:
        if target.is_symlink() or not target.is_dir():
            total += target.lstat().st_size
            continue
        for root, _dirs, files in os.walk(target, followlinks=False):
            for fname in files:
                total += os.lstat(os.path.join(root, fname)).st_size
    return total


def _remove(target: Path) -> None:
    if target.is_symlink() or not target.is_dir():
        target.unlink()
    else:
        shutil.rmtree(target)


def _write_marker(batch: Path, state: str, freed: int) -> None:
    (batch / MARKER).write_text(
        json.dumps(
            {
                "state": state,
                "purged_at": datetime.now(UTC).isoformat(),
                "bytes_freed": freed,
            }
        ),
        encoding="utf-8",
    )


def purge_old_downloads(
    base_dir: Path,
    max_days: int,
    protect: frozenset[str] | set[str] = frozenset(),
    *,
    now: float | None = None,
    dry_run: bool = True,
) -> RetentionResult:
    """Delete the downloaded files of finished batches older than ``max_days``.

    ``max_days < 1`` disables the purge. With ``dry_run`` nothing is deleted and no
    marker is written; the result still counts what would go.
    """
    result = RetentionResult()
    if max_days < 1:
        return result
    base = Path(base_dir)
    if not base.is_dir():
        return result
    now = datetime.now(UTC).timestamp() if now is None else now
    cutoff = now - max_days * DAY_SECS
    base_real = base.resolve()

    for entry in sorted(base.iterdir()):
        try:
            if entry.is_symlink() or not entry.is_dir():
                continue
            if not BATCH_ID.fullmatch(entry.name):
                result.unmatched_dirs += 1
                continue
            if not entry.resolve().is_relative_to(base_real):
                continue
            report = _read_report(entry)
            if report is None:
                continue
            status = report.get("status")
            if not isinstance(status, str):
                continue
            if status in ACTIVE_STATUSES or (
                status in TERMINAL_STATUSES and entry.name in protect
            ):
                result.skipped_active += 1
                continue
            if status not in TERMINAL_STATUSES:
                continue
            if not _old_enough(entry, report, now, cutoff):
                continue
            targets = _purgeable(entry)
            if not targets or _has_fresh_file(targets, cutoff):
                continue
            result.candidates += 1
            freed = _size(targets)
            result.details.append(
                {
                    "batch_id": entry.name,
                    "status": status,
                    "age_days": round(
                        (now - (entry / "_report.json").stat().st_mtime) / DAY_SECS, 1
                    ),
                    "bytes": freed,
                }
            )
            if dry_run:
                result.bytes_freed += freed
                continue
            # Marker BEFORE the first removal: a pass cut short (deploy, SIGKILL) leaves
            # "started" as evidence that the policy began purging this batch.
            _write_marker(entry, "started", freed)
            for target in targets:
                _remove(target)
            _write_marker(entry, "done", freed)
            result.purged += 1
            result.bytes_freed += freed
        except Exception as exc:  # one bad batch must never stop the others
            result.errors += 1
            if result.error_type is None:
                result.error_type = type(exc).__name__
    return result
