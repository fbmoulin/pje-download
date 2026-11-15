"""The absolute per-batch ceiling grows with the number of processos.

After #109 a TJES processo with many annexes takes ~120-143 s, so a fixed 3600 s
ceiling failed every processo past ~27 of a healthy batch. The effective ceiling is
`min(CAP, max(FLOOR, n * SECS_PER_PROCESSO))`; `BATCH_MAX_DURATION_SECS` stays the
floor, and the reply-queue TTL is derived from the CAP (the largest ceiling).
"""

from __future__ import annotations

import importlib
from unittest.mock import AsyncMock, patch

import pytest

import config
import dashboard_api
from tests.test_batch_timeouts_and_redis_blips import (
    JOBS,
    _job,
    _silent_worker_redis,
    _state,
)


def _ceiling(n, floor=3600, per=300, cap=86400):
    with (
        patch("dashboard_api.BATCH_MAX_DURATION_SECS", floor),
        patch("dashboard_api.BATCH_SECS_PER_PROCESSO", per),
        patch("dashboard_api.BATCH_MAX_DURATION_CAP_SECS", cap),
    ):
        return dashboard_api._batch_ceiling_secs(n)


class TestCeilingFormula:
    def test_one_processo_keeps_the_floor(self):
        assert _ceiling(1) == 3600

    def test_many_processos_exceed_the_floor(self):
        assert _ceiling(100) == 30000

    def test_more_than_27_slow_processos_fit(self):
        # the #109 case: 40 processos at ~143 s = 5720 s > old fixed 3600 s
        assert _ceiling(40) >= 40 * 143

    def test_cap_is_respected(self):
        assert _ceiling(500) == 86400

    def test_zero_processos_keeps_the_floor(self):
        assert _ceiling(0) == 3600


class TestPollLoopUsesTheScaledCeiling:
    @pytest.mark.asyncio
    async def test_not_failed_just_past_the_floor_when_n_times_300_is_larger(
        self, tmp_path
    ):
        job = _job(tmp_path, "scaled")  # 3 processos -> ceiling 900 s, floor 10 s
        ds = _state(tmp_path, job)
        redis_stub = _silent_worker_redis()
        ds.get_redis = AsyncMock(return_value=redis_stub)
        clock = {"now": 0.0}
        seen = []

        async def blpop(key, timeout=0):
            seen.append(clock["now"])
            clock["now"] += 11  # floor + 1 s per poll
            if len(seen) >= 3:
                raise RuntimeError("stop the loop")
            return None

        redis_stub.blpop = blpop
        with (
            patch("dashboard_api.BATCH_MAX_DURATION_SECS", 10),
            patch("dashboard_api.BATCH_SECS_PER_PROCESSO", 300),
            patch("dashboard_api.RESULT_WAIT_TIMEOUT_SECS", 10**6),
            patch.object(dashboard_api.time, "monotonic", lambda: clock["now"]),
        ):
            await ds._run_batch(job)

        # three polls happened past the 10 s floor, so no absolute timeout fired
        assert len(seen) == 3
        assert "absolute timeout" not in (job.error or "").lower()

    @pytest.mark.asyncio
    async def test_absolute_timeout_still_fires_and_reports_the_effective_ceiling(
        self, tmp_path
    ):
        job = _job(tmp_path, "cap")
        ds = _state(tmp_path, job)
        redis_stub = _silent_worker_redis()
        ds.get_redis = AsyncMock(return_value=redis_stub)
        with (
            patch("dashboard_api.BATCH_MAX_DURATION_SECS", -1),
            patch("dashboard_api.BATCH_MAX_DURATION_CAP_SECS", -1),
        ):
            await ds._run_batch(job)
        assert job.status == "failed"
        assert "absolute timeout (-1s)" in job.error.lower()
        assert redis_stub.queues[JOBS] == []

    @pytest.mark.asyncio
    async def test_resume_sizes_the_ceiling_from_the_processos_still_pending(
        self, tmp_path
    ):
        """resume_active_batch re-enters with enqueue_jobs=False; `state.pending`
        already excludes finished processos, so the ceiling is sized from those."""
        job = _job(tmp_path, "resume")
        ds = _state(tmp_path, job)
        seen = []
        real = dashboard_api._batch_ceiling_secs

        def spy(n):
            seen.append(n)
            return real(n)

        state = dashboard_api.BatchPollState(
            pending={job.processos[0]},
            last_result_at=0.0,
            serialized_payloads={},
            reply_queue="q",
        )
        with (
            patch.object(dashboard_api, "_batch_ceiling_secs", spy),
            patch("dashboard_api.BATCH_MAX_DURATION_SECS", -1),
            patch("dashboard_api.BATCH_MAX_DURATION_CAP_SECS", -1),
        ):
            await ds._poll_results_loop(job, _silent_worker_redis(), state)
        assert seen[0] == 1


class TestConfigConstants:
    @staticmethod
    def _reload(monkeypatch, **env):
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        return importlib.reload(config)

    def test_defaults(self):
        assert config.BATCH_MAX_DURATION_SECS == 3600
        assert config.BATCH_SECS_PER_PROCESSO == 300
        assert config.BATCH_MAX_DURATION_CAP_SECS == 86400

    def test_default_ttl_exceeds_the_cap(self):
        assert config.REDIS_RESULT_QUEUE_TTL_SECS > config.BATCH_MAX_DURATION_CAP_SECS

    def test_env_overrides(self, monkeypatch):
        cfg = self._reload(
            monkeypatch,
            BATCH_SECS_PER_PROCESSO="60",
            BATCH_MAX_DURATION_CAP_SECS="7200",
        )
        assert cfg.BATCH_SECS_PER_PROCESSO == 60
        assert cfg.BATCH_MAX_DURATION_CAP_SECS == 7200
        assert cfg.REDIS_RESULT_QUEUE_TTL_SECS > 7200

    def test_cap_never_below_the_floor(self, monkeypatch):
        cfg = self._reload(
            monkeypatch,
            BATCH_MAX_DURATION_SECS="7200",
            BATCH_MAX_DURATION_CAP_SECS="60",
        )
        assert cfg.BATCH_MAX_DURATION_CAP_SECS == 7200

    def test_ttl_must_exceed_the_cap_not_just_the_floor(self, monkeypatch):
        with pytest.raises(ValueError, match="REDIS_RESULT_QUEUE_TTL_SECS"):
            self._reload(
                monkeypatch,
                BATCH_MAX_DURATION_CAP_SECS="86400",
                REDIS_RESULT_QUEUE_TTL_SECS="43200",
            )
