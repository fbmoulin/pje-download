"""Static pin on the production-deploy guard in .github/workflows/deploy.yml.

deploy.yml is triggered by `workflow_run` of CI, which runs in the *base* repo with
production secrets. ci.yml also runs on `pull_request`, and every fork's default
branch is named `master`, so a guard of only `head_branch == 'master'` would let a
fork PR that passes CI be checked out and rsynced to the VPS. Nothing exercises
deploy.yml before merge, so this test is the only thing that stops the guard from
being edited away.
"""

from __future__ import annotations

import re
from pathlib import Path

DEPLOY_YML = Path(__file__).parent.parent / ".github" / "workflows" / "deploy.yml"


def _job_if_expression() -> str:
    text = DEPLOY_YML.read_text(encoding="utf-8")
    m = re.search(r"^    if: >\n((?:      .*\n)+)", text, re.M)
    assert m, "deploy job `if: >` block not found in deploy.yml"
    return m.group(1)


def test_guard_requires_head_commit_to_live_in_this_repository():
    assert (
        "github.event.workflow_run.head_repository.full_name == github.repository"
        in _job_if_expression()
    )


def test_guard_only_accepts_push_or_manual_ci_runs():
    expr = _job_if_expression()
    assert "github.event.workflow_run.event == 'push'" in expr
    assert "github.event.workflow_run.event == 'workflow_dispatch'" in expr


def test_guard_never_accepts_pull_request_runs():
    assert "pull_request" not in _job_if_expression()


def test_guard_still_requires_green_ci_on_master():
    expr = _job_if_expression()
    assert "github.event.workflow_run.conclusion == 'success'" in expr
    assert "github.event.workflow_run.head_branch == 'master'" in expr
