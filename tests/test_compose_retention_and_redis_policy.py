"""Static checks on docker-compose.yml: the retention switches reach the container and the
Redis eviction policy never drops the job queue.

The dashboard service has no `env_file`, so a variable absent from its `environment:` block
never reaches the process, whatever the VPS `.env` says. The retention kill switch
(`DOWNLOAD_RETENTION_DRY_RUN=true`, documented in CLAUDE.md) was therefore dead.
`kratos:pje:jobs` has no TTL and is the only copy of the queued jobs, so `allkeys-*`
policies may evict it silently; `volatile-lru` only touches keys with a TTL.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")


def _service_block(name: str) -> str:
    match = re.search(rf"^  {name}:\n(.*?)(?=^  \S|\Z)", COMPOSE, re.M | re.S)
    assert match, f"service {name!r} not found"
    return match.group(1)


def test_dashboard_receives_the_retention_switches():
    block = _service_block("dashboard")
    for var in (
        "DOWNLOAD_RETENTION_DAYS",
        "DOWNLOAD_RETENTION_DRY_RUN",
        "DOWNLOAD_RETENTION_INTERVAL_SECS",
    ):
        assert re.search(rf"^\s+{var}:\s*\$\{{{var}:-", block, re.M), var


def test_compose_defaults_match_config_defaults():
    block = _service_block("dashboard")
    assert "${DOWNLOAD_RETENTION_DAYS:-14}" in block
    assert "${DOWNLOAD_RETENTION_DRY_RUN:-false}" in block
    assert "${DOWNLOAD_RETENTION_INTERVAL_SECS:-86400}" in block


def test_redis_never_evicts_keys_without_ttl():
    policy = re.search(r"--maxmemory-policy\s+(\S+)", _service_block("redis"))
    assert policy, "maxmemory-policy not set"
    assert policy.group(1) in {"volatile-lru", "volatile-ttl", "noeviction"}
