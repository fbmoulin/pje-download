"""The images must install the versions this repo pins — all of them.

Why a Dockerfile test at all: nothing else can see this. The suite runs against
`requirements.txt`, so a green CI says what the *pinned* set does and nothing
about what a container actually installs. The dashboard target used to install a
hand-written list of bare package names with no version specifier, which meant a
rebuild silently shipped whatever PyPI served that day — measured 2026-09-20:
redis 8.1.0, structlog 26.1.0 (a major) and aiohttp 3.14.3 against pins of
8.0.0 / 25.5.0 / 3.14.1, with the worker on the pinned set in the same deploy.

That is the same class as the redis-py 8.0.0 `socket_timeout` regression
(PR #32), whose stated defence is the pin — a defence one of the two images was
not receiving. These tests fail if a second, hand-maintained dependency list
ever comes back.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DOCKERFILE = _REPO_ROOT / "Dockerfile"
_REQUIREMENTS = _REPO_ROOT / "requirements.txt"

# Mirrors the exclusion in the dashboard target. Kept here as a literal, not
# imported from anywhere, so a silent edit to the Dockerfile trips the tests
# rather than being rubber-stamped by a shared constant.
_DASHBOARD_EXCLUDES = re.compile(r"^playwright[=<>~!]")

_PIN_RE = re.compile(r"^(?P<name>[A-Za-z0-9_.\-]+)(?:\[[^\]]+\])?(?P<spec>[=<>~!].+)$")


def _requirement_lines() -> list[str]:
    return [
        line.strip()
        for line in _REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _strip_comments(body: str) -> str:
    """Drop ``#`` comment lines.

    Necessary because these targets are heavily commented and the comments
    quote the very commands under test (e.g. "`pip install -r requirements.txt`
    plus `playwright install chromium`"), which a naive scan reads as a package
    named ``plus``.
    """
    return "\n".join(
        line for line in body.splitlines() if not line.lstrip().startswith("#")
    )


def _pip_install_statements(target: str) -> list[str]:
    """Every ``pip install`` command in a stage, with continuations joined.

    Joining ``\\``-continuations FIRST is the whole point. A scan that matches
    only up to the first newline sees `pip install --no-cache-dir \\` and finds
    no package names at all — so a test built on it passes against a wrapped,
    hand-written package list while claiming to forbid exactly that. (Observed:
    it did.)
    """
    body = _strip_comments(_target_body(target))
    joined = re.sub(r"\\\s*\n\s*", " ", body)
    statements = []
    for logical_line in joined.splitlines():
        for segment in logical_line.split("&&"):
            segment = segment.strip().removeprefix("RUN ").strip()
            if segment.startswith("pip install"):
                statements.append(segment)
    return statements


def _target_body(target: str) -> str:
    """Return the Dockerfile text belonging to one build stage."""
    text = _DOCKERFILE.read_text(encoding="utf-8")
    starts = [
        (m.start(), m.group(1))
        for m in re.finditer(r"^FROM\s+\S+\s+AS\s+(\S+)", text, re.MULTILINE)
    ]
    for i, (pos, name) in enumerate(starts):
        if name == target:
            end = starts[i + 1][0] if i + 1 < len(starts) else len(text)
            return text[pos:end]
    raise AssertionError(f"no build stage named {target!r} in the Dockerfile")


class TestEveryRequirementIsPinnedOrBounded:
    def test_requirements_carry_version_specifiers(self):
        """A bare name in requirements.txt would defeat both images at once."""
        unbounded = [line for line in _requirement_lines() if not _PIN_RE.match(line)]
        assert unbounded == [], (
            f"requirements.txt entries without a version specifier: {unbounded}"
        )


class TestImagesInstallFromRequirements:
    @pytest.mark.parametrize("target", ["dashboard", "worker"])
    def test_target_installs_from_requirements_txt(self, target: str):
        """Both images must derive versions from requirements.txt.

        This is the regression that matters: the dashboard target previously ran
        `pip install aiohttp prometheus_client structlog ...` with no reference to
        requirements.txt and no version on any package.
        """
        installs = _pip_install_statements(target)
        assert installs, f"{target} target runs no pip install"
        joined = "\n".join(installs)
        assert "requirements" in joined, (
            f"the {target} target installs packages without referencing "
            f"requirements.txt — versions would come from PyPI's latest, not "
            f"from this repo's pins. Found:\n{joined}"
        )

    @pytest.mark.parametrize("target", ["dashboard", "worker"])
    def test_target_names_no_packages_inline(self, target: str):
        """No hand-written package list in either target.

        A list of names beside `-r requirements.txt` is how the two sources drift
        back apart, which is exactly the bug this file guards.
        """
        for stmt in _pip_install_statements(target):
            args = stmt.replace("pip install", "", 1)
            # Drop flags and their values, then anything that is a path/req file.
            tokens = [t for t in args.split() if t not in {"&&", "\\"}]
            leftovers = []
            skip_next = False
            for tok in tokens:
                if skip_next:
                    skip_next = False
                    continue
                if tok in {"-r", "-c", "--constraint", "--requirement"}:
                    skip_next = True
                    continue
                if tok.startswith("-"):
                    continue
                leftovers.append(tok)
            assert leftovers == [], (
                f"the {target} target names packages inline: {leftovers}. "
                f"Every version must come from requirements.txt."
            )


class TestDashboardHealthcheckIsPublic:
    """The image's own HEALTHCHECK must probe a path the API-key middleware
    leaves open, or the container can never report healthy in production.

    Sprint 8 put every ``/api/*`` route behind ``X-API-Key``; the Dockerfile
    kept probing ``/api/status`` and 401-looped. ``docker-compose.yml`` was
    fixed (461a789) but the bare image was not. This ties the probe to the
    middleware's *own* public-path lists, so the two cannot drift again.
    """

    _CURL_URL = re.compile(r"HEALTHCHECK[^\n]*\\\n\s*CMD\s+curl\s+\S+\s+(\S+)")

    def test_dashboard_probe_path_is_exempt_from_api_key(self):
        import dashboard_api

        body = _strip_comments(_target_body("dashboard"))
        m = self._CURL_URL.search(body)
        assert m, "dashboard target has no `HEALTHCHECK ... CMD curl <url>`"
        path = re.sub(r"^https?://[^/]+", "", m.group(1))
        public = path in dashboard_api._AUTH_PUBLIC_EXACT or path.startswith(
            dashboard_api._AUTH_PUBLIC_PREFIXES
        )
        assert public, (
            f"dashboard HEALTHCHECK probes {path!r}, which api_key_middleware "
            f"gates behind X-API-Key — the container would 401-loop and never "
            f"turn healthy. Public paths: {dashboard_api._AUTH_PUBLIC_EXACT} "
            f"+ prefixes {dashboard_api._AUTH_PUBLIC_PREFIXES}"
        )


class TestDashboardExclusionLosesNoPin:
    def test_only_playwright_is_excluded(self):
        """The dashboard drops playwright (139 MB, never imported there) and
        nothing else. If this list grows, a pinned runtime dep is going missing."""
        excluded = [ln for ln in _requirement_lines() if _DASHBOARD_EXCLUDES.match(ln)]
        assert [ln.split("=")[0] for ln in excluded] == ["playwright"], (
            f"dashboard image is dropping more than playwright: {excluded}"
        )

    def test_dashboard_set_keeps_every_other_pin_verbatim(self):
        """Derivation must not rewrite a specifier — only omit playwright."""
        all_lines = _requirement_lines()
        derived = [ln for ln in all_lines if not _DASHBOARD_EXCLUDES.match(ln)]
        assert derived == [ln for ln in all_lines if not ln.startswith("playwright")]
        assert len(derived) == len(all_lines) - 1
        for line in derived:
            assert _PIN_RE.match(line), f"derived dashboard dep unpinned: {line}"

    def test_exclusion_regex_matches_the_dockerfile(self):
        """The Dockerfile's grep and this file's regex must stay the same rule."""
        body = _target_body("dashboard")
        assert "grep -vE '^playwright[=<>~!]' requirements.txt" in body, (
            "the dashboard target's exclusion no longer matches _DASHBOARD_EXCLUDES; "
            "update both or the tests stop describing the image"
        )

    def test_dashboard_does_not_need_playwright_at_import(self):
        """Justifies the exclusion: dropping playwright must change no behaviour.

        `pje_session` imports playwright lazily inside functions, so the dashboard
        imports cleanly without it — which is already true of the deployed image.
        """
        import sys

        class _Blocker:
            def find_module(self, name, path=None):
                if name == "playwright" or name.startswith("playwright."):
                    return self

            def load_module(self, name):
                raise ImportError("playwright absent (dashboard image)")

        blocker = _Blocker()
        removed = [m for m in list(sys.modules) if m.startswith("playwright")]
        saved = {m: sys.modules.pop(m) for m in removed}
        sys.meta_path.insert(0, blocker)
        try:
            import importlib

            for name in ("dashboard_api", "audit_sync", "metrics", "protocol"):
                sys.modules.pop(name, None)
                importlib.import_module(name)
        finally:
            sys.meta_path.remove(blocker)
            sys.modules.update(saved)
