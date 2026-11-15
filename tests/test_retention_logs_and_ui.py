"""`_logs` retention and the 'files removed' flag in /api/history (follow-up of #113/#114)."""

import json
import os

import pytest
from aiohttp.test_utils import TestClient, TestServer

import dashboard_api
from retention import purge_old_logs

NOW = 1_800_000_000.0
DAY = 86400.0


def _touch(path, ts, data=b"{}"):
    path.write_bytes(data)
    os.utime(path, (ts, ts), follow_symlinks=False)


class TestPurgeOldLogs:
    def test_removes_only_old_json_files(self, tmp_path):
        logs = tmp_path / "_logs"
        logs.mkdir()
        _touch(logs / "old.json", NOW - 20 * DAY, b"x" * 10)
        _touch(logs / "new.json", NOW - 2 * DAY)
        res = purge_old_logs(tmp_path, 14, now=NOW, dry_run=False)
        assert not (logs / "old.json").exists()
        assert (logs / "new.json").exists()
        assert (res.candidates, res.purged, res.bytes_freed) == (1, 1, 10)

    def test_dry_run_deletes_nothing(self, tmp_path):
        logs = tmp_path / "_logs"
        logs.mkdir()
        _touch(logs / "old.json", NOW - 20 * DAY)
        res = purge_old_logs(tmp_path, 14, now=NOW, dry_run=True)
        assert (logs / "old.json").exists()
        assert (res.candidates, res.purged) == (1, 0)

    def test_disabled_below_one_day_and_missing_dir(self, tmp_path):
        assert purge_old_logs(tmp_path, 0, now=NOW, dry_run=False).candidates == 0
        assert purge_old_logs(tmp_path, 14, now=NOW, dry_run=False).candidates == 0

    def test_ignores_other_names_directories_and_symlinks(self, tmp_path):
        logs = tmp_path / "_logs"
        logs.mkdir()
        _touch(logs / "keep.txt", NOW - 20 * DAY)
        (logs / "sub.json").mkdir()
        outside = tmp_path / "outside.json"
        _touch(outside, NOW - 20 * DAY)
        (logs / "link.json").symlink_to(outside)
        res = purge_old_logs(tmp_path, 14, now=NOW, dry_run=False)
        assert res.candidates == 0
        assert (logs / "keep.txt").exists() and (logs / "sub.json").is_dir()
        assert outside.exists()

    def test_logs_dir_symlink_is_not_followed(self, tmp_path):
        target = tmp_path / "elsewhere"
        target.mkdir()
        _touch(target / "old.json", NOW - 20 * DAY)
        (tmp_path / "_logs").symlink_to(target, target_is_directory=True)
        res = purge_old_logs(tmp_path, 14, now=NOW, dry_run=False)
        assert res.candidates == 0
        assert (target / "old.json").exists()

    def test_future_mtime_is_not_old(self, tmp_path):
        logs = tmp_path / "_logs"
        logs.mkdir()
        _touch(logs / "future.json", NOW + 5 * DAY)
        assert purge_old_logs(tmp_path, 14, now=NOW, dry_run=False).candidates == 0

    def test_file_exactly_at_the_cutoff_is_kept(self, tmp_path):
        logs = tmp_path / "_logs"
        logs.mkdir()
        _touch(logs / "edge.json", NOW - 14 * DAY)
        assert purge_old_logs(tmp_path, 14, now=NOW, dry_run=False).candidates == 0

    def test_one_failure_does_not_stop_the_rest(self, tmp_path, monkeypatch):
        logs = tmp_path / "_logs"
        logs.mkdir()
        _touch(logs / "a.json", NOW - 20 * DAY)
        _touch(logs / "b.json", NOW - 20 * DAY)
        real = os.unlink
        calls = []

        def flaky(p, *a, **k):
            calls.append(p)
            if len(calls) == 1:
                raise PermissionError("no")
            return real(p, *a, **k)

        monkeypatch.setattr(os, "unlink", flaky)
        res = purge_old_logs(tmp_path, 14, now=NOW, dry_run=False)
        assert (res.errors, res.purged, res.error_type) == (1, 1, "PermissionError")


@pytest.mark.asyncio
async def test_history_flags_purged_batches(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "DASHBOARD_API_KEY", "")
    for name, purged in (
        ("20260101_120000_aaaaaa", True),
        ("20260102_120000_bbbbbb", False),
    ):
        d = tmp_path / name
        d.mkdir()
        (d / "_report.json").write_text(
            json.dumps(
                {
                    "status": "done",
                    "processos": {"p": {"docs_baixados": 3, "tamanho_bytes": 9}},
                }
            ),
            encoding="utf-8",
        )
        if purged:
            (d / "_purged.json").write_text('{"state": "done"}', encoding="utf-8")
    async with TestClient(TestServer(dashboard_api.create_app(tmp_path))) as client:
        resp = await client.get("/api/history")
        rows = {r["batch_id"]: r for r in await resp.json()}
    assert rows["20260101_120000_aaaaaa"]["purged"] is True
    assert rows["20260102_120000_bbbbbb"]["purged"] is False
