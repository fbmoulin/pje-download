"""Duração por processo e por lote, legível sem Prometheus.

O worker mede o job (``durationS`` no resultado), o dashboard grava em
``duracao_s`` por processo e resume o lote em ``timing`` no ``_report.json``:

    jq '.timing, [.processos[].duracao_s]' _report.json

Antes disto, ``duracao_s`` era sempre ``None`` e a coluna da tela ficava vazia.
"""

from __future__ import annotations

import math

import pytest

from file_utils import duration_stats, valid_duration

# ───────────────────────── valid_duration ─────────────────────────


class TestValidDuration:
    @pytest.mark.parametrize(
        "value,expected", [(0, 0.0), (1, 1.0), (12.34, 12.34), (0.0, 0.0)]
    )
    def test_accepts_non_negative_finite_numbers(self, value, expected):
        assert valid_duration(value) == expected

    @pytest.mark.parametrize(
        "value",
        [None, True, False, "12", "", [], {}, -0.5, -1, math.nan, math.inf, -math.inf],
    )
    def test_rejects_everything_else(self, value):
        assert valid_duration(value) is None

    @pytest.mark.parametrize("sign,exp", [(1, 309), (1, 400), (-1, 400)])
    def test_huge_int_is_rejected_not_an_overflow_error(self, sign, exp):
        """json.loads aceita inteiros enormes; math.isfinite(int) levanta OverflowError."""
        value = sign * 10**exp
        assert valid_duration(value) is None


# ───────────────────────── duration_stats ─────────────────────────


class TestDurationStats:
    def test_empty(self):
        assert duration_stats([]) == {
            "count": 0,
            "p50": None,
            "p95": None,
            "max": None,
            "sum": 0.0,
        }

    def test_single_value_is_every_percentile(self):
        s = duration_stats([7.5])
        assert (s["count"], s["p50"], s["p95"], s["max"], s["sum"]) == (
            1,
            7.5,
            7.5,
            7.5,
            7.5,
        )

    def test_nearest_rank_percentiles(self):
        values = list(range(1, 101))  # 1..100
        s = duration_stats(values)
        assert s["p50"] == 50 and s["p95"] == 95 and s["max"] == 100
        assert s["sum"] == 5050

    def test_p95_equals_max_below_twenty_samples(self):
        s = duration_stats([1, 2, 3, 4, 5, 6, 7, 8, 9, 100])
        assert s["p95"] == s["max"] == 100

    def test_integer_rank_has_no_float_error(self):
        """ceil(0.95 * 20) = 19, mas 0.95 * 20 em ponto flutuante pode dar 19.000000000000004."""
        values = list(range(1, 21))
        assert duration_stats(values)["p95"] == 19

    def test_order_does_not_matter_and_ties_are_fine(self):
        assert duration_stats([5, 1, 5, 5, 3])["p50"] == 5

    def test_ignores_invalid_entries(self):
        s = duration_stats([2.0, None, True, "x", math.nan, math.inf, -1, 4.0])
        assert s["count"] == 2 and s["max"] == 4.0 and s["sum"] == 6.0

    def test_sum_that_would_overflow_stays_finite(self):
        """1e308 + 1e308 = inf, que json.dumps grava como Infinity (invalido para jq)."""
        s = duration_stats([1e308, 1e308])
        assert all(math.isfinite(s[k]) for k in ("p50", "p95", "max", "sum"))
        json.loads(json.dumps(s), parse_constant=pytest.fail)

    def test_huge_int_entry_is_ignored(self):
        s = duration_stats([10**400, 3.0])
        assert s["count"] == 1 and s["sum"] == 3.0

    def test_results_are_rounded_to_two_decimals(self):
        s = duration_stats([0.123456, 0.2])
        assert s["sum"] == 0.32 and s["p50"] == 0.12


# ───────────────────────── worker mede o job ─────────────────────────

import asyncio  # noqa: E402
import json  # noqa: E402
from unittest.mock import AsyncMock, MagicMock, patch  # noqa: E402

from tests.test_worker_publish_error_containment import _load_worker_module  # noqa: E402


def _one_job_worker(download):
    """Worker que recebe exatamente um job e depois pede desligamento."""
    w = _load_worker_module()
    worker = w.PJeSessionWorker()
    worker.redis = AsyncMock()
    worker.mni_client = None
    shutdown = asyncio.Event()
    delivered: list[str] = []

    async def blmove(*args, **kwargs):
        if delivered:
            shutdown.set()
            return None
        delivered.append("J-1")
        return json.dumps(
            {"jobId": "J-1", "numeroProcesso": "PROC-0001", "batchId": "B-1"}
        )

    worker.redis.blmove = blmove
    worker.download_process = download
    worker.is_session_expired = MagicMock(return_value=False)
    worker._publish_result = AsyncMock()
    return w, worker, shutdown


def _ok_result(job):
    return {
        "jobId": job["jobId"],
        "numeroProcesso": job["numeroProcesso"],
        "status": "success",
        "arquivosDownloaded": [],
    }


class TestWorkerMeasuresTheJob:
    @pytest.mark.asyncio
    async def test_result_carries_duration_in_seconds(self):
        async def download(job):
            return _ok_result(job)

        w, worker, shutdown = _one_job_worker(download)
        clock = iter([100.0, 112.5])
        with (
            patch.object(w, "_monotonic", lambda: next(clock)),
            patch.object(w, "log", MagicMock()),
        ):
            await asyncio.wait_for(worker.consume_queue(shutdown), timeout=5)

        (published,), _ = worker._publish_result.call_args
        assert published["durationS"] == 12.5
        assert published["batchId"] == "B-1"  # o que já existia continua

    @pytest.mark.asyncio
    async def test_duration_is_rounded_to_two_decimals(self):
        async def download(job):
            return _ok_result(job)

        w, worker, shutdown = _one_job_worker(download)
        clock = iter([100.0, 100.126])
        with (
            patch.object(w, "_monotonic", lambda: next(clock)),
            patch.object(w, "log", MagicMock()),
        ):
            await asyncio.wait_for(worker.consume_queue(shutdown), timeout=5)
        (published,), _ = worker._publish_result.call_args
        assert published["durationS"] == 0.13

    @pytest.mark.asyncio
    async def test_a_job_that_raises_publishes_nothing_and_has_no_duration(self):
        async def download(job):
            raise RuntimeError("boom")

        w, worker, shutdown = _one_job_worker(download)
        with patch.object(w, "log", MagicMock()):
            await asyncio.wait_for(worker.consume_queue(shutdown), timeout=5)
        worker._publish_result.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancellation_still_propagates(self):
        async def download(job):
            raise asyncio.CancelledError

        w, worker, shutdown = _one_job_worker(download)
        with patch.object(w, "log", MagicMock()), pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(worker.consume_queue(shutdown), timeout=5)

    def test_monotonic_is_a_module_level_alias_not_the_global_clock(self):
        """Os testes trocam ``worker._monotonic``; patchar ``time.monotonic`` global
        quebraria o relógio do asyncio."""
        w = _load_worker_module()
        import time

        assert w._monotonic is time.monotonic


# ───────────────────────── dashboard grava duracao_s ─────────────────────────


import dashboard_api  # noqa: E402


def _state_and_job(tmp_path, processos=("p1", "p2")):
    state = dashboard_api.DashboardState(tmp_path)
    job = dashboard_api.BatchJob(
        id="b1",
        processos=list(processos),
        output_dir=str(tmp_path / "b1"),
    )
    job.progress = state._build_initial_progress(job)
    return state, job


def _result(numero="p1", **over):
    base = {
        "jobId": "J",
        "numeroProcesso": numero,
        "status": "success",
        "arquivosDownloaded": [],
        "errorMessage": None,
    }
    base.update(over)
    return base


class TestDashboardStoresTheDuration:
    def test_duration_from_the_worker_is_stored(self, tmp_path):
        state, job = _state_and_job(tmp_path)
        state._apply_result(job, _result(durationS=12.5))
        assert job.progress["processos"]["p1"]["duracao_s"] == 12.5

    def test_legacy_worker_result_without_duration_gives_none(self, tmp_path):
        state, job = _state_and_job(tmp_path)
        state._apply_result(job, _result())  # payload de um worker antigo
        assert job.progress["processos"]["p1"]["duracao_s"] is None

    @pytest.mark.parametrize("bad", ["12", True, -1, math.nan, math.inf, None, [], {}])
    def test_invalid_duration_is_dropped_not_stored(self, tmp_path, bad):
        state, job = _state_and_job(tmp_path)
        state._apply_result(job, _result(durationS=bad))
        assert job.progress["processos"]["p1"]["duracao_s"] is None

    @pytest.mark.parametrize("exp", [309, 400])
    def test_huge_int_duration_is_dropped_and_does_not_raise(self, tmp_path, exp):
        state, job = _state_and_job(tmp_path)
        state._apply_result(job, _result(durationS=10**exp))
        assert job.progress["processos"]["p1"]["duracao_s"] is None

    def test_failed_result_keeps_its_duration_too(self, tmp_path):
        state, job = _state_and_job(tmp_path)
        state._apply_result(
            job, _result(status="failed", errorMessage="x", durationS=0.4)
        )
        entry = job.progress["processos"]["p1"]
        assert entry["status"] == "failed" and entry["duracao_s"] == 0.4

    def test_a_late_progress_event_does_not_erase_it(self, tmp_path):
        state, job = _state_and_job(tmp_path)
        state._apply_result(job, _result(durationS=3.2))
        state._apply_progress_event(
            job,
            {
                "eventType": "progress",
                "numeroProcesso": "p1",
                "phase": "mni_metadata",
                "phase_detail": "tardio",
            },
        )
        assert job.progress["processos"]["p1"]["duracao_s"] == 3.2

    def test_failing_the_pending_ones_keeps_the_finished_duration(self, tmp_path):
        state, job = _state_and_job(tmp_path)
        state._apply_result(job, _result("p1", durationS=3.0))
        state._fail_remaining_processes(job, {"p1", "p2"}, "Worker timeout")
        procs = job.progress["processos"]
        assert procs["p1"]["duracao_s"] == 3.0 and procs["p1"]["status"] == "done"
        assert procs["p2"]["status"] == "failed" and procs["p2"]["duracao_s"] is None

    def test_resumed_progress_file_keeps_the_duration(self, tmp_path):
        state, job = _state_and_job(tmp_path)
        state._apply_result(job, _result("p1", durationS=8.75))
        state._persist_active_batch(job)
        state._persist_progress(job)
        restored = dashboard_api.DashboardState(tmp_path)
        restored._load_active_batch()
        assert restored.batches["b1"].progress["processos"]["p1"]["duracao_s"] == 8.75


# ───────────────────────── timing do lote ─────────────────────────

from tests.test_dashboard_api import FakeRedis  # noqa: E402

TIMING_KEYS = {
    "wall_s",
    "processos",
    "with_duration",
    "sum_s",
    "p50_s",
    "p95_s",
    "max_s",
    "seconds_per_processo",
}


def _timed_job(
    tmp_path,
    durations,
    started="2026-10-24T10:00:00+00:00",
    finished="2026-10-24T10:02:00+00:00",
):
    state, job = _state_and_job(
        tmp_path, processos=[f"p{i}" for i in range(len(durations))]
    )
    for i, d in enumerate(durations):
        state._apply_result(job, _result(f"p{i}", durationS=d))
    job.started_at, job.finished_at = started, finished
    return job


class TestBatchTiming:
    def test_summarises_wall_time_and_durations(self, tmp_path):
        job = _timed_job(tmp_path, [10.0, 20.0, 30.0, 40.0])
        t = dashboard_api.batch_timing(job)
        assert set(t) == TIMING_KEYS
        assert t["wall_s"] == 120.0 and t["processos"] == 4
        assert t["with_duration"] == 4 and t["sum_s"] == 100.0
        assert t["p50_s"] == 20.0 and t["p95_s"] == 40.0 and t["max_s"] == 40.0
        assert t["seconds_per_processo"] == 30.0

    def test_wall_time_includes_idle_so_it_differs_from_the_sum(self, tmp_path):
        t = dashboard_api.batch_timing(_timed_job(tmp_path, [1.0, 1.0]))
        assert t["sum_s"] == 2.0 and t["seconds_per_processo"] == 60.0

    @pytest.mark.parametrize(
        "started,finished",
        [
            (None, "2026-10-24T10:02:00+00:00"),
            ("2026-10-24T10:00:00+00:00", None),
            ("2026-10-24T10:00:00+00:00", ""),
            ("lixo", "2026-10-24T10:02:00+00:00"),
            ("2026-10-24T10:00:00", "2026-10-24T10:02:00+00:00"),  # naive x aware
            (
                "2026-10-24T10:05:00+00:00",
                "2026-10-24T10:02:00+00:00",
            ),  # relógio voltou
        ],
    )
    def test_unusable_timestamps_give_null_wall_time_not_a_crash(
        self, tmp_path, started, finished
    ):
        t = dashboard_api.batch_timing(_timed_job(tmp_path, [5.0], started, finished))
        assert t["wall_s"] is None and t["seconds_per_processo"] is None
        assert t["with_duration"] == 1  # as durações continuam valendo

    def test_no_durations(self, tmp_path):
        t = dashboard_api.batch_timing(_timed_job(tmp_path, [None, None]))
        assert t["with_duration"] == 0 and t["p50_s"] is None and t["sum_s"] == 0.0

    def test_empty_batch_does_not_divide_by_zero(self, tmp_path):
        state, job = _state_and_job(tmp_path, processos=[])
        job.started_at, job.finished_at = (
            "2026-10-24T10:00:00+00:00",
            "2026-10-24T10:01:00+00:00",
        )
        t = dashboard_api.batch_timing(job)
        assert t["processos"] == 0 and t["seconds_per_processo"] is None


class _AnsweringRedis(FakeRedis):
    """Worker que responde cada job com um status fixo e uma duração."""

    def __init__(self, status="success", duration=2.0):
        super().__init__()
        self.status, self.duration = status, duration

    async def rpush(self, key, *values):
        queue = self.queues.setdefault(key, [])
        queue.extend(values)
        if key == "kratos:pje:jobs":
            for raw in values:
                payload = json.loads(raw)
                self.queues.setdefault(payload["replyQueue"], []).append(
                    json.dumps(
                        {
                            "jobId": payload["jobId"],
                            "batchId": payload["batchId"],
                            "numeroProcesso": payload["numeroProcesso"],
                            "status": self.status,
                            "arquivosDownloaded": [],
                            "errorMessage": None
                            if self.status == "success"
                            else "falhou",
                            "durationS": self.duration,
                        }
                    )
                )
        return len(queue)


def _report(tmp_path, job):
    return json.loads((tmp_path / job.id / "_report.json").read_text())


async def _run(tmp_path, redis_stub, processos=("p1", "p2", "p3")):
    state = dashboard_api.DashboardState(tmp_path)
    job = dashboard_api.BatchJob(
        id="run1",
        processos=list(processos),
        status="queued",
        output_dir=str(tmp_path / "run1"),
    )
    state.batches[job.id] = job
    state.get_redis = AsyncMock(return_value=redis_stub)
    return state, job


class TestReportCarriesTiming:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "worker_status,final",
        [
            ("success", "done"),
            ("partial_success", "partial"),
            ("failed", "failed"),
        ],
    )
    async def test_every_final_status_writes_timing(
        self, tmp_path, worker_status, final
    ):
        state, job = await _run(tmp_path, _AnsweringRedis(worker_status, duration=4.0))
        await state._run_batch(job)
        assert job.status == final
        timing = _report(tmp_path, job)["timing"]
        assert set(timing) == TIMING_KEYS
        assert timing["with_duration"] == 3 and timing["sum_s"] == 12.0
        assert timing["max_s"] == 4.0 and timing["processos"] == 3
        assert timing["wall_s"] is not None and timing["wall_s"] >= 0
        procs = _report(tmp_path, job)["processos"]
        assert all(p["duracao_s"] == 4.0 for p in procs.values())

    @pytest.mark.asyncio
    async def test_timeout_path_writes_timing_without_durations(self, tmp_path):
        silent = FakeRedis()

        async def rpush(key, *values):
            silent.queues.setdefault(key, []).extend(values)
            return 1

        silent.rpush = rpush
        state, job = await _run(tmp_path, silent)
        with patch("dashboard_api.RESULT_WAIT_TIMEOUT_SECS", -1):
            await state._run_batch(job)
        assert job.status == "failed"
        timing = _report(tmp_path, job)["timing"]
        assert timing["with_duration"] == 0 and timing["p95_s"] is None

    @pytest.mark.asyncio
    async def test_unexpected_exception_path_writes_timing(self, tmp_path):
        broken = _AnsweringRedis()
        broken.blpop = AsyncMock(side_effect=ValueError("boom"))
        state, job = await _run(tmp_path, broken)
        await state._run_batch(job)
        assert job.status == "failed"
        assert set(_report(tmp_path, job)["timing"]) == TIMING_KEYS

    def test_a_stale_timing_from_a_reloaded_report_is_overwritten(self, tmp_path):
        """_load_history carrega o relatório inteiro como progresso, então um lote
        refinalizado traz o ``timing`` antigo: o ``_finalize_batch`` tem de sobrescrever."""
        from datetime import UTC, datetime, timedelta

        state, job = _state_and_job(tmp_path, processos=["p1", "p2"])
        state._apply_result(job, _result("p1", durationS=2.0))
        state._apply_result(job, _result("p2", durationS=3.0))
        job.started_at = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
        job.progress["timing"] = {"wall_s": 99999, "processos": 1, "sum_s": 777}
        state._finalize_batch(job)
        timing = _report(tmp_path, job)["timing"]
        assert timing["wall_s"] < 1000 and timing["processos"] == 2
        assert timing["sum_s"] == 5.0

    @pytest.mark.asyncio
    async def test_progress_file_also_carries_timing(self, tmp_path):
        state, job = await _run(tmp_path, _AnsweringRedis())
        await state._run_batch(job)
        progress = json.loads((tmp_path / job.id / "_progress.json").read_text())
        assert set(progress["timing"]) == TIMING_KEYS


# ───────────────────────── UI: fmtDuration ─────────────────────────

import shutil  # noqa: E402

from tests.test_dashboard_js_escaping import _run as _js_run  # noqa: E402

DASH = "—"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
class TestFmtDuration:
    """A coluna de duração passa a ter valores de verdade; o formatador não pode
    mostrar "1m 60s" nem "60.0s"."""

    @pytest.mark.parametrize(
        "expr,expected",
        [
            ("null", DASH),
            ("undefined", DASH),
            ("0", DASH),
            ("-3", DASH),
            ("'5'", DASH),
            ("NaN", DASH),
            ("Infinity", DASH),
            ("0.5", "0.5s"),
            ("12.34", "12.3s"),
            ("59.94", "59.9s"),
            ("59.96", "1m 0s"),
            ("60", "1m 0s"),
            ("119.6", "2m 0s"),
            ("125", "2m 5s"),
            ("3599.6", "60m 0s"),
        ],
    )
    def test_formats(self, expr, expected):
        assert _js_run(f"fmtDuration({expr})", "fmtDuration") == expected
