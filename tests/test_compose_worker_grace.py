"""The worker gets time to finish the job it is running before the container is killed.

Docker's default stop timeout is 10 s: SIGTERM, then SIGKILL. The worker's SIGTERM
handler only stops the loop *between* jobs, so a job longer than 10 s died mid-way on
every deploy. `stop_grace_period` lets it finish and publish its result; a job longer
than the grace period is still killed, and then the processing-list redelivery
(tests/test_worker_job_redelivery.py) brings it back on the next boot.

Measured 2026-10-17 on production batch reports: 5-8 s per processo (4 batches). The
grace period is 60 s; the spec caps it at 300 s because the deploy's restart step
waits for it (premortem 2026-10-17, finding 5).

Spec: docs/specs/2026-10-16-job-perdido-no-deploy.md (part A, Task 2). Static checks:
the compose file and deploy.yml are not exercised by any test run.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRACE_FLOOR_SECS = 30  # several times the measured job duration
GRACE_CAP_SECS = 300  # spec D2 ceiling
RESTART_STEP_FLOOR_MIN = (
    15  # rebuild (~2.5 min) + grace + health wait (2 min), with slack
)


def _service_block(compose: str, name: str) -> str:
    match = re.search(rf"^  {name}:\n(.*?)(?=^  \S|\Z)", compose, re.M | re.S)
    assert match, f"service {name!r} not found in docker-compose.yml"
    return match.group(1)


def _seconds(value: str) -> int:
    total = 0
    for amount, unit in re.findall(r"(\d+)\s*([hms])", value):
        total += int(amount) * {"h": 3600, "m": 60, "s": 1}[unit]
    return total


def test_worker_declares_a_stop_grace_period_within_bounds():
    worker = _service_block((ROOT / "docker-compose.yml").read_text(), "worker")
    match = re.search(r"^\s+stop_grace_period:\s*(\S+)", worker, re.M)
    assert match, (
        "worker has no stop_grace_period: Docker kills it 10 s after SIGTERM, "
        "mid-job, on every deploy"
    )
    grace = _seconds(match.group(1))
    assert GRACE_FLOOR_SECS <= grace <= GRACE_CAP_SECS, grace


def test_restart_step_outlasts_build_grace_and_health_wait():
    deploy = (ROOT / ".github" / "workflows" / "deploy.yml").read_text()
    step = re.search(
        r"- name: Restart services\n(.*?)(?=\n      - name:|\Z)", deploy, re.S
    )
    assert step, "Restart services step not found in deploy.yml"
    match = re.search(r"^\s+command_timeout:\s*(\S+)", step.group(1), re.M)
    assert match, (
        "Restart services relies on appleboy/ssh-action's 10 min default; with a "
        "rebuild, the worker's grace period and the health wait it can run out"
    )
    assert _seconds(match.group(1)) >= RESTART_STEP_FLOOR_MIN * 60, match.group(1)
