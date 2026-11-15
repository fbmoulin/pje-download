"""Retention of finished batches' downloaded files (spec 2026-11-15-retencao-downloads).

The purge is irreversible, so these tests pin what must NOT be touched as hard as
what must go. Batch folders are built under tmp_path with `os.utime`.
"""

import json
import os
from datetime import UTC, datetime, timedelta

import pytest

from retention import RetentionResult, purge_old_downloads

NOW = 1_800_000_000.0
DAY = 86400.0
OLD = NOW - 20 * DAY  # older than the 14-day window used below
RECENT = NOW - 2 * DAY
DAYS = 14


def _set_time(path, ts):
    os.utime(path, (ts, ts), follow_symlinks=False)


def _iso(ts):
    return datetime.fromtimestamp(ts, UTC).isoformat()


def make_batch(
    base,
    name="20260101_120000_abc123",
    *,
    status="done",
    report_ts=OLD,
    completed_ts=None,
    files_ts=OLD,
    report=None,
):
    """A batch folder with one process folder (2 files) and a `_report.json`."""
    batch = base / name
    proc = batch / "proc1"
    proc.mkdir(parents=True)
    for fname in ("a.pdf", "b.html"):
        f = proc / fname
        f.write_bytes(b"x" * 100)
        _set_time(f, files_ts)
    if report is None:
        report = {
            "status": status,
            "completed_at": _iso(report_ts if completed_ts is None else completed_ts),
        }
    rep = batch / "_report.json"
    rep.write_text(json.dumps(report))
    _set_time(rep, report_ts)
    return batch


def run(base, **kw):
    kw.setdefault("max_days", DAYS)
    kw.setdefault("now", NOW)
    kw.setdefault("dry_run", False)
    return purge_old_downloads(base, **kw)


def test_purges_old_process_folder_and_keeps_the_report(tmp_path):
    batch = make_batch(tmp_path)

    result = run(tmp_path)

    assert isinstance(result, RetentionResult)
    assert not (batch / "proc1").exists()
    assert (batch / "_report.json").exists()
    assert result.purged == 1 and result.candidates == 1
    assert result.bytes_freed == 200 and result.errors == 0


@pytest.mark.parametrize("status", ["done", "failed", "partial"])
def test_every_terminal_status_is_purged(tmp_path, status):
    batch = make_batch(tmp_path, status=status)
    run(tmp_path)
    assert not (batch / "proc1").exists()


@pytest.mark.parametrize("status", ["queued", "running"])
def test_active_batches_are_untouched(tmp_path, status):
    """Mutation guard: dropping the terminal-status gate must break this test."""
    batch = make_batch(tmp_path, status=status)

    result = run(tmp_path)

    assert (batch / "proc1" / "a.pdf").exists()
    assert result.skipped_active == 1 and result.purged == 0


def test_recent_batch_is_untouched(tmp_path):
    batch = make_batch(tmp_path, report_ts=RECENT, files_ts=RECENT)
    assert run(tmp_path).purged == 0
    assert (batch / "proc1").exists()


def test_batch_without_a_report_is_untouched(tmp_path):
    batch = make_batch(tmp_path)
    (batch / "_report.json").unlink()
    assert run(tmp_path).purged == 0
    assert (batch / "proc1").exists()


@pytest.mark.parametrize(
    "content",
    ["{not json", "[]", '"done"', "null", '{"status": ["done"]}', '{"status": {}}'],
)
def test_unreadable_or_odd_reports_never_raise_and_never_purge(tmp_path, content):
    """`status` as a list/dict is unhashable: the check must live inside the try."""
    batch = make_batch(tmp_path)
    (batch / "_report.json").write_text(content)
    _set_time(batch / "_report.json", OLD)

    result = run(tmp_path)

    assert (batch / "proc1").exists()
    assert result.purged == 0


def test_unknown_status_is_untouched(tmp_path):
    batch = make_batch(tmp_path, status="cancelled-by-mystery")
    assert run(tmp_path).purged == 0
    assert (batch / "proc1").exists()


@pytest.mark.parametrize(
    "name", ["_logs", "20251115_x", "20260101_120000_ABC123", "notabatch", "2026"]
)
def test_directories_outside_the_batch_pattern_are_left_alone(tmp_path, name):
    other = tmp_path / name
    (other / "sub").mkdir(parents=True)
    (other / "sub" / "f.pdf").write_bytes(b"x")
    _set_time(other / "sub" / "f.pdf", OLD)
    (other / "_report.json").write_text('{"status": "done"}')
    _set_time(other / "_report.json", OLD)

    result = run(tmp_path)

    assert (other / "sub" / "f.pdf").exists()
    assert result.unmatched_dirs == 1


def test_batch_name_with_trailing_newline_does_not_match(tmp_path):
    other = tmp_path / "20260101_120000_abc123\n"
    try:
        (other / "proc1").mkdir(parents=True)
    except OSError:
        pytest.skip("filesystem rejects newline in names")
    (other / "proc1" / "a.pdf").write_bytes(b"x")
    (other / "_report.json").write_text('{"status": "done"}')
    _set_time(other / "_report.json", OLD)
    _set_time(other / "proc1" / "a.pdf", OLD)

    run(tmp_path)

    assert (other / "proc1" / "a.pdf").exists()


def test_underscore_files_stay_but_an_underscore_process_folder_goes(tmp_path):
    batch = make_batch(tmp_path)
    sneaky = batch / "_x"
    sneaky.mkdir()
    (sneaky / "case.pdf").write_bytes(b"x" * 10)
    _set_time(sneaky / "case.pdf", OLD)
    (batch / "_progress.json").write_text("{}")
    _set_time(batch / "_progress.json", OLD)

    run(tmp_path)

    assert not sneaky.exists()
    assert (batch / "_progress.json").exists()
    assert (batch / "_report.json").exists()


def test_loose_non_underscore_files_in_the_batch_root_are_removed(tmp_path):
    batch = make_batch(tmp_path)
    loose = batch / "loose.pdf"
    loose.write_bytes(b"x")
    _set_time(loose, OLD)

    run(tmp_path)

    assert not loose.exists()


def test_protected_batch_is_untouched(tmp_path):
    batch = make_batch(tmp_path, name="20260101_120000_abc123")

    result = run(tmp_path, protect=frozenset({"20260101_120000_abc123"}))

    assert (batch / "proc1").exists()
    assert result.skipped_active == 1 and result.purged == 0


def test_a_fresh_file_inside_the_batch_vetoes_the_purge(tmp_path):
    """A worker still writing after the report was finalized (timeout path)."""
    batch = make_batch(tmp_path)
    late = batch / "proc1" / "late.pdf"
    late.write_bytes(b"x")
    _set_time(late, RECENT)

    result = run(tmp_path)

    assert (batch / "proc1" / "a.pdf").exists()
    assert result.purged == 0


def test_future_dated_report_is_not_old(tmp_path):
    """A forward clock jump or odd mtime must never look like a reason to delete."""
    batch = make_batch(tmp_path, report_ts=NOW + 5 * DAY)
    assert run(tmp_path).purged == 0
    assert (batch / "proc1").exists()


def test_both_ages_must_be_old_report_mtime_recent(tmp_path):
    batch = make_batch(tmp_path, report_ts=RECENT, completed_ts=OLD)
    assert run(tmp_path).purged == 0
    assert (batch / "proc1").exists()


def test_both_ages_must_be_old_completed_at_recent(tmp_path):
    batch = make_batch(tmp_path, report_ts=OLD, completed_ts=RECENT)
    assert run(tmp_path).purged == 0
    assert (batch / "proc1").exists()


def test_unparseable_completed_at_falls_back_to_the_mtime(tmp_path):
    batch = make_batch(tmp_path, report={"status": "done", "completed_at": "ontem"})
    _set_time(batch / "_report.json", OLD)

    run(tmp_path)

    assert not (batch / "proc1").exists()


def test_symlinked_batch_is_never_followed(tmp_path):
    outside = tmp_path / "outside"
    make_batch(outside, name="payload")  # not a batch name, but holds data
    base = tmp_path / "base"
    base.mkdir()
    (base / "20260101_120000_abc123").symlink_to(outside / "payload")

    run(base)

    assert (outside / "payload" / "proc1" / "a.pdf").exists()


def test_symlink_inside_a_batch_is_removed_as_a_link_not_followed(tmp_path):
    target = tmp_path / "precious"
    target.mkdir()
    (target / "keep.txt").write_text("keep")
    batch = make_batch(tmp_path / "base")
    (batch / "link").symlink_to(target, target_is_directory=True)

    run(tmp_path / "base")

    assert (target / "keep.txt").exists()
    assert not (batch / "link").exists() and not (batch / "link").is_symlink()


def test_second_pass_is_a_no_op(tmp_path):
    make_batch(tmp_path)
    run(tmp_path)

    second = run(tmp_path)

    assert second.candidates == 0 and second.purged == 0 and second.bytes_freed == 0


def test_dry_run_deletes_nothing_and_writes_no_marker(tmp_path):
    batch = make_batch(tmp_path)

    result = run(tmp_path, dry_run=True)

    assert (batch / "proc1" / "a.pdf").exists()
    assert not (batch / "_purged.json").exists()
    assert result.candidates == 1 and result.bytes_freed == 200


def test_purge_writes_a_marker_with_the_bytes_freed(tmp_path):
    batch = make_batch(tmp_path)

    run(tmp_path)

    marker = json.loads((batch / "_purged.json").read_text())
    assert marker["bytes_freed"] == 200 and "purged_at" in marker
    assert marker["state"] == "done"


@pytest.mark.parametrize("days", [0, -1, -30])
def test_non_positive_window_does_nothing(tmp_path, days):
    batch = make_batch(tmp_path)
    result = run(tmp_path, max_days=days)
    assert (batch / "proc1").exists() and result.candidates == 0


def test_missing_base_dir_is_a_no_op(tmp_path):
    result = run(tmp_path / "nope")
    assert result.candidates == 0 and result.errors == 0


def test_an_unremovable_batch_is_counted_and_does_not_stop_the_others(
    tmp_path, monkeypatch
):
    bad = make_batch(tmp_path, name="20260101_120000_aaaaaa")
    good = make_batch(tmp_path, name="20260102_120000_bbbbbb")
    import retention

    real = retention.shutil.rmtree

    def flaky(path, *a, **kw):
        if "aaaaaa" in str(path):
            raise PermissionError("root-owned file")
        return real(path, *a, **kw)

    monkeypatch.setattr(retention.shutil, "rmtree", flaky)

    result = run(tmp_path)

    assert result.errors == 1 and result.error_type == "PermissionError"
    # The marker is written BEFORE removing: a purge cut short leaves "started".
    assert (bad / "proc1").exists()
    assert json.loads((bad / "_purged.json").read_text())["state"] == "started"
    assert not (good / "proc1").exists() and result.purged == 1


def test_result_never_carries_file_or_process_names(tmp_path):
    make_batch(tmp_path)
    text = repr(run(tmp_path))
    assert "proc1" not in text and "a.pdf" not in text and "b.html" not in text


def test_details_list_each_candidate_with_id_status_age_and_bytes(tmp_path):
    make_batch(tmp_path, name="20260101_120000_abc123", status="partial")

    result = run(tmp_path, dry_run=True)

    assert result.details == [
        {
            "batch_id": "20260101_120000_abc123",
            "status": "partial",
            "age_days": 20.0,
            "bytes": 200,
        }
    ]


def test_default_is_dry_run_so_a_bare_call_deletes_nothing(tmp_path):
    """The last line of defence: nobody passing no flag may delete anything."""
    batch = make_batch(tmp_path)

    result = purge_old_downloads(tmp_path, DAYS, now=NOW)

    assert (batch / "proc1" / "a.pdf").exists()
    assert result.candidates == 1 and result.purged == 0


def test_report_exactly_at_the_cutoff_is_kept(tmp_path):
    """Boundary: `older than N days` is strict; equal is not old."""
    cutoff = NOW - DAYS * DAY
    batch = make_batch(tmp_path, report_ts=cutoff, completed_ts=cutoff)

    assert run(tmp_path).purged == 0
    assert (batch / "proc1").exists()


def test_completed_at_exactly_at_the_cutoff_is_kept(tmp_path):
    batch = make_batch(tmp_path, report_ts=OLD, completed_ts=NOW - DAYS * DAY)
    assert run(tmp_path).purged == 0
    assert (batch / "proc1").exists()


def test_file_exactly_at_the_cutoff_vetoes(tmp_path):
    batch = make_batch(tmp_path, files_ts=NOW - DAYS * DAY)
    assert run(tmp_path).purged == 0
    assert (batch / "proc1").exists()


def test_a_fresh_loose_file_at_the_batch_root_vetoes_too(tmp_path):
    batch = make_batch(tmp_path)
    loose = batch / "loose.pdf"
    loose.write_bytes(b"x")
    _set_time(loose, RECENT)

    assert run(tmp_path).purged == 0
    assert (batch / "proc1" / "a.pdf").exists()


def test_a_symlinked_report_is_refused(tmp_path):
    real = tmp_path / "elsewhere.json"
    real.write_text('{"status": "done"}')
    _set_time(real, OLD)
    batch = make_batch(tmp_path / "base")
    (batch / "_report.json").unlink()
    (batch / "_report.json").symlink_to(real)

    assert run(tmp_path / "base").purged == 0
    assert (batch / "proc1").exists()


def test_an_oversized_report_is_treated_as_unreadable(tmp_path, monkeypatch):
    import retention

    batch = make_batch(tmp_path)
    monkeypatch.setattr(retention, "MAX_REPORT_BYTES", 5)

    assert run(tmp_path).purged == 0
    assert (batch / "proc1").exists()


def test_retention_result_defaults():
    r = RetentionResult()
    assert (r.candidates, r.purged, r.bytes_freed, r.errors) == (0, 0, 0, 0)
    assert r.skipped_active == 0 and r.unmatched_dirs == 0 and r.error_type is None
    assert timedelta(days=1).total_seconds() == DAY


def test_report_mtime_exactly_at_the_cutoff_is_kept_even_if_completed_at_is_old(
    tmp_path,
):
    """Isolates the mtime boundary: `completed_at` alone would allow the purge."""
    batch = make_batch(tmp_path, report_ts=NOW - DAYS * DAY, completed_ts=OLD)

    assert run(tmp_path).purged == 0
    assert (batch / "proc1").exists()


def test_bytes_do_not_follow_links_inside_a_process_folder(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "big.bin").write_bytes(b"x" * 1000)
    batch = make_batch(tmp_path / "base")
    (batch / "proc1" / "inner_link").symlink_to(outside, target_is_directory=True)

    result = run(tmp_path / "base", dry_run=True)

    assert (
        result.bytes_freed == 200
    )  # the 2 real files, not the 1000 bytes behind the link
