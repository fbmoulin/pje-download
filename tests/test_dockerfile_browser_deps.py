"""The worker image must carry the system libraries Chromium needs, not a hand-kept list.

Measured on the production VPS (2026-10-24): `chromium.launch()` inside the worker
container died with `error while loading shared libraries: libXcomposite.so.1`. The
Dockerfile apt-installed a short, hand-picked list (xvfb, libnss3, libgbm1, ...) that
never covered Playwright's own list for the base distro (libxcomposite1, libxdamage1,
libxfixes3, libxkbcommon0, libxrandr2, libcups2t64, ...). The browser fallback is dormant
in production, so nothing noticed, and every test mocks the browser.

`playwright install-deps chromium` installs the exact list for the installed Playwright
version and the image's distro, so it also follows a Playwright bump (#84). This is a
static check on the Dockerfile text (no docker daemon in CI); it pins the invariant, not
the build. The proof of the build is the launch check in CLAUDE.md after the deploy.
"""

from __future__ import annotations

import re
from pathlib import Path

DOCKERFILE = Path(__file__).parent.parent / "Dockerfile"
INSTALL_DEPS = re.compile(r"playwright\s+install-deps(\s+--\S+)*\s+chromium")


def _worker_stage() -> str:
    text = DOCKERFILE.read_text(encoding="utf-8")
    m = re.search(r"^FROM .* AS worker\s*$(.*?)(?=^FROM |\Z)", text, re.M | re.S)
    assert m, "worker stage not found in Dockerfile"
    return m.group(1)


def test_worker_installs_playwrights_own_system_dependencies():
    assert INSTALL_DEPS.search(_worker_stage()), (
        "the worker stage must run `playwright install-deps chromium`"
    )


def test_deps_are_installed_as_root_after_playwright_exists():
    stage = _worker_stage()
    deps_at = INSTALL_DEPS.search(stage).start()
    assert stage.index("pip install") < deps_at, "the playwright CLI must exist first"
    assert deps_at < stage.rindex("USER appuser"), (
        "apt needs root: run before USER appuser"
    )


def test_apt_lists_are_cleaned_in_the_same_layer():
    stage = _worker_stage()
    # A RUN instruction is its first line plus every line continued with a backslash.
    runs = re.findall(r"^RUN\b(?:[^\n]*\\\n)*[^\n]*", stage, re.M)
    run = next((r for r in runs if INSTALL_DEPS.search(r)), None)
    assert run, "install-deps must be part of a RUN instruction"
    assert "rm -rf /var/lib/apt/lists/*" in run, (
        "install-deps runs apt-get update; clean the lists in the same RUN to keep the layer small"
    )


def test_build_identity_arg_is_still_the_last_thing_in_the_stage():
    # An ARG above the expensive layers turns every deploy into a multi-minute rebuild.
    stage = _worker_stage()
    assert stage.index("playwright install-deps") < stage.index("ARG BUILD_SHA")
