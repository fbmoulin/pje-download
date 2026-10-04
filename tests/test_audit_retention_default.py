"""The audit-trail retention default is 120 days, and every place that states it agrees.

Four places carry the number: ``config.AUDIT_LOG_RETENTION_DAYS`` (what the dashboard
passes to ``rotate_logs`` at startup), ``audit.rotate_logs``' own default,
``docker-compose.yml`` and the ``.env`` that ``deploy.yml`` writes on the VPS. The
last one is what production actually uses, so a drift there is silent.
"""

from __future__ import annotations

import inspect
import os
import shutil
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

from audit import rotate_logs

ROOT = Path(__file__).parent.parent
RETENTION = 120


def test_config_default_is_120(tmp_path):
    # Import a COPY of config.py: config.load_env() reads a .env next to the module,
    # and a developer's local .env must not change what this test measures.
    shutil.copy(ROOT / "config.py", tmp_path / "config.py")
    env = {k: v for k, v in os.environ.items() if k != "AUDIT_LOG_RETENTION_DAYS"}
    env["PYTHONPATH"] = str(tmp_path)
    out = subprocess.run(
        [sys.executable, "-c", "import config; print(config.AUDIT_LOG_RETENTION_DAYS)"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
        check=True,
    )
    assert out.stdout.strip() == str(RETENTION)


def test_rotate_logs_default_is_120():
    assert inspect.signature(rotate_logs).parameters["max_days"].default == RETENTION


def test_compose_default_is_120():
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert f"${{AUDIT_LOG_RETENTION_DAYS:-{RETENTION}}}" in text


def test_deploy_writes_120_to_the_vps_env():
    text = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    assert f"AUDIT_LOG_RETENTION_DAYS={RETENTION}" in text


def test_default_window_keeps_100_day_old_logs_and_drops_130_day_old(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AUDIT_LOG_DIR", str(tmp_path))
    keep = tmp_path / f"audit-{date.today() - timedelta(days=100)}.jsonl"
    drop = tmp_path / f"audit-{date.today() - timedelta(days=130)}.jsonl"
    keep.write_text("{}\n")
    drop.write_text("{}\n")

    deleted = rotate_logs()

    assert deleted == 1
    assert keep.exists(), "100 days old is inside a 120-day window"
    assert not drop.exists()
