"""Static checks for the /refine-prompt skill (prompt-gate spec, Task 3).

The skill must stay read-only: it rewrites a prompt and never executes the task.
Stdlib only — the YAML frontmatter is parsed by hand (no PyYAML dependency).
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = REPO_ROOT / ".claude" / "skills" / "refine-prompt" / "SKILL.md"

REQUIRED_TOOLS = {"Read", "Glob", "Grep"}
FORBIDDEN_TOOLS = {"Edit", "Write", "Bash", "NotebookEdit", "*"}
TEMPLATE_HEADINGS = ("Objetivo", "Alvo", "Definition of Done", "Fora de escopo")


def _split_skill() -> tuple[dict[str, str], str]:
    text = SKILL_PATH.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines and lines[0].strip() == "---", "SKILL.md must start with '---'"
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        pytest.fail("frontmatter is not closed by a second '---' line")
    meta: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        assert sep, f"malformed frontmatter line: {line!r}"
        meta[key.strip()] = value.strip().strip("\"'")
    body = "\n".join(lines[end + 1 :])
    return meta, body


def _allowed_tools(meta: dict[str, str]) -> set[str]:
    raw = meta.get("allowed-tools", "").strip("[]")
    return {t.strip().strip("\"'") for t in raw.split(",") if t.strip()}


def test_skill_file_exists():
    assert SKILL_PATH.is_file(), f"missing {SKILL_PATH}"


def test_frontmatter_name_and_description():
    meta, _ = _split_skill()
    assert meta.get("name") == "refine-prompt"
    assert meta.get("description"), "description must be non-empty"


def test_allowed_tools_are_read_only():
    meta, _ = _split_skill()
    assert "allowed-tools" in meta, "allowed-tools must be declared"
    tools = _allowed_tools(meta)
    assert REQUIRED_TOOLS <= tools, f"missing read tools: {REQUIRED_TOOLS - tools}"
    # A tool spec like "Bash(git:*)" still grants Bash: compare the bare name.
    bare = {t.split("(", 1)[0].strip() for t in tools}
    assert not (bare & FORBIDDEN_TOOLS), f"write/exec tools: {bare & FORBIDDEN_TOOLS}"
    assert not any("*" in t for t in tools), "wildcards are not allowed"


@pytest.mark.parametrize("heading", TEMPLATE_HEADINGS)
def test_body_has_template_heading(heading):
    _, body = _split_skill()
    assert heading in body


def test_body_forbids_executing_the_task():
    _, body = _split_skill()
    low = body.lower()
    assert "não execute" in low or "nunca execute" in low
    assert "não edite" in low or "nunca edite" in low
