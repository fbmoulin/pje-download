"""Static pin: the production .env written by deploy.yml must be owner-only (0600).

The file holds MNI_PASSWORD, REDIS_PASSWORD and DASHBOARD_API_KEY. A plain
`{ ... } > file` creates it with the umask of the SSH session (typically 0644 ->
world-readable), and `>` never changes the mode of a file that already exists. So
two things are needed and both are pinned here:

* `umask 077` (or an `install -m 600` creation) BEFORE the write, so the file never
  exists readable, even for a moment;
* an explicit `chmod 600` AFTER the write, to fix a pre-existing file with an old mode.

Nothing exercises deploy.yml before merge; only the deploy itself proves it runs.
"""

from __future__ import annotations

import re
from pathlib import Path

DEPLOY_YML = Path(__file__).parent.parent / ".github" / "workflows" / "deploy.yml"
ENV_PATH = "/opt/pje-download/.env"
STEP_NAME = "Write .env on VPS"


def _script() -> str:
    """Body of the step's `script: |` block (no PyYAML dependency: slice the text)."""
    text = DEPLOY_YML.read_text(encoding="utf-8")
    m = re.search(
        rf"^      - name: {re.escape(STEP_NAME)}\n(?P<step>(?:(?!      - name: ).*\n)*)",
        text,
        re.M,
    )
    assert m, f"step {STEP_NAME!r} not found in deploy.yml"
    s = re.search(
        r"^          script: \|\n(?P<body>(?:            .*\n|\n)+)",
        m.group("step"),
        re.M,
    )
    assert s, f"`script: |` block not found in step {STEP_NAME!r}"
    return s.group("body")


def _lines() -> list[str]:
    return _script().splitlines()


def _write_index(lines: list[str]) -> int:
    idx = [
        i
        for i, ln in enumerate(lines)
        if re.search(rf">\s*{re.escape(ENV_PATH)}\s*$", ln)
    ]
    assert len(idx) == 1, f"expected exactly one redirect into {ENV_PATH}, got {idx}"
    return idx[0]


def test_env_is_created_owner_only_before_it_is_written():
    lines = _lines()
    write = _write_index(lines)
    before = lines[:write]
    has_umask = any(re.match(r"\s*(\(\s*)?umask\s+0?77\b", ln) for ln in before)
    has_install = any(
        re.search(rf"install\s+-m\s*0?600\b.*{re.escape(ENV_PATH)}", ln)
        for ln in before
    )
    assert has_umask or has_install, (
        "the .env must be created with umask 077 or `install -m 600` before the write"
    )


def test_env_gets_explicit_chmod_600_after_the_write():
    lines = _lines()
    write = _write_index(lines)
    after = lines[write + 1 :]
    assert any(
        re.match(rf"\s*chmod\s+0?600\s+{re.escape(ENV_PATH)}\s*$", ln) for ln in after
    ), f"no `chmod 600 {ENV_PATH}` after the write"
