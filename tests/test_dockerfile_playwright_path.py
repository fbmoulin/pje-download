"""The worker image must install Chromium where its runtime user can find it.

`playwright install chromium` ran as root (before `USER appuser`) with no
`PLAYWRIGHT_BROWSERS_PATH`, so the browser landed in `/root/.cache/ms-playwright`.
Playwright resolves browsers under `$HOME` when that variable is unset, so the
worker (running as `appuser`, HOME=/home/appuser) looked somewhere else and every
`chromium.launch()` failed with "Executable doesn't exist". Production did not
notice: the MNI path defers the browser, and the Playwright fallback is mocked in
every test.

This is a static check on the Dockerfile text (no docker daemon in CI); it pins
the invariant, not the build.
"""

from __future__ import annotations

import re
from pathlib import Path

DOCKERFILE = Path(__file__).parent.parent / "Dockerfile"


def _worker_stage() -> str:
    text = DOCKERFILE.read_text(encoding="utf-8")
    m = re.search(r"^FROM .* AS worker\s*$(.*?)(?=^FROM |\Z)", text, re.M | re.S)
    assert m, "worker stage not found in Dockerfile"
    return m.group(1)


def test_browsers_path_is_set_to_a_shared_directory():
    stage = _worker_stage()
    m = re.search(r"^ENV\s+PLAYWRIGHT_BROWSERS_PATH=(\S+)\s*$", stage, re.M)
    assert m, "worker stage must set PLAYWRIGHT_BROWSERS_PATH"
    path = m.group(1)
    assert path.startswith("/") and not path.startswith(("/root", "/home")), (
        f"{path!r} is per-user; the root-installed browser would be invisible to appuser"
    )


def test_browsers_path_is_set_before_chromium_is_installed():
    stage = _worker_stage()
    env_at = stage.index("PLAYWRIGHT_BROWSERS_PATH=")
    install_at = stage.index("playwright install chromium")
    assert env_at < install_at, "ENV must precede `playwright install` to apply to it"


def test_the_worker_still_drops_privileges():
    stage = _worker_stage()
    assert re.search(r"^USER appuser\s*$", stage, re.M)
    assert stage.index("playwright install chromium") < stage.rindex("USER appuser")
