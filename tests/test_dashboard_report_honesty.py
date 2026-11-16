"""O relatório do dashboard não pode esconder o que aconteceu (auditoria 2026-11-16).

A2  o resultado final do worker apagava o ``total_docs`` esperado (N/N mesmo parcial).
M4  um processo ``running`` num lote que estoura timeout virava 0/0.
Teto o teto absoluto usava ``time.monotonic()`` local: a retomada o reiniciava.
Audit rotação de logs sem cópia (sync desligado) agora é visível.
include_anexos  só booleano estrito.

Números de processo são montados em tempo de execução (gitleaks).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
import structlog
from aiohttp.test_utils import TestClient, TestServer

import dashboard_api
from dashboard_api import APP_CTX_KEY, BatchJob, create_app
from tests.test_batch_timeouts_and_redis_blips import _silent_worker_redis


@pytest.fixture(autouse=True)
def _no_api_key(monkeypatch):
    # Outros testes recarregam ``config`` com uma chave; aqui o dev mode isola a ordem.
    import config

    monkeypatch.setattr(config, "DASHBOARD_API_KEY", "")


def _num(seq: int) -> str:
    return "{:07d}-{:02d}.{}.{}.{:02d}.{:04d}".format(seq, 1, 2024, 8, 8, 1)


def _state_and_job(tmp_path, n=2):
    state = dashboard_api.DashboardState(tmp_path)
    job = BatchJob(
        id="b1",
        processos=[_num(i) for i in range(1, n + 1)],
        output_dir=str(tmp_path / "b1"),
    )
    job.progress = state._build_initial_progress(job)
    return state, job


def _files(n):
    return [{"nome": f"d{i}.pdf", "tamanhoBytes": 10} for i in range(n)]


def _progress(state, job, numero, **ev):
    state._apply_progress_event(
        job, {"eventType": "progress", "numeroProcesso": numero, **ev}
    )


# ───────────────────────────── A2 ─────────────────────────────


class TestFinalResultKeepsTheExpectedTotal:
    def test_partial_keeps_expected_total_above_downloaded(self, tmp_path):
        state, job = _state_and_job(tmp_path)
        n = job.processos[0]
        _progress(state, job, n, status="running", total_docs=81, docs_baixados=40)
        state._apply_result(
            job,
            {
                "numeroProcesso": n,
                "status": "partial_success",
                "arquivosDownloaded": _files(69),
                "errorMessage": "faltam 12",
            },
        )
        p = job.progress["processos"][n]
        assert (p["docs_baixados"], p["total_docs"]) == (69, 81)

    def test_failed_keeps_expected_total(self, tmp_path):
        state, job = _state_and_job(tmp_path)
        n = job.processos[0]
        _progress(state, job, n, status="running", total_docs=30, docs_baixados=5)
        state._apply_result(
            job,
            {
                "numeroProcesso": n,
                "status": "failed",
                "arquivosDownloaded": _files(5),
                "errorMessage": "x",
            },
        )
        p = job.progress["processos"][n]
        assert (p["docs_baixados"], p["total_docs"]) == (5, 30)

    def test_done_stays_n_over_n_even_if_progress_had_a_larger_total(self, tmp_path):
        """Mutation guard: o ramo ``done`` não pode herdar o esperado do progresso."""
        state, job = _state_and_job(tmp_path)
        n = job.processos[0]
        _progress(state, job, n, status="running", total_docs=81, docs_baixados=69)
        state._apply_result(
            job,
            {
                "numeroProcesso": n,
                "status": "success",
                "arquivosDownloaded": _files(69),
            },
        )
        p = job.progress["processos"][n]
        assert (p["docs_baixados"], p["total_docs"]) == (69, 69)

    def test_partial_with_no_prior_progress_does_not_invent_a_total(self, tmp_path):
        state, job = _state_and_job(tmp_path)
        n = job.processos[0]
        state._apply_result(
            job,
            {
                "numeroProcesso": n,
                "status": "partial_success",
                "arquivosDownloaded": _files(3),
                "errorMessage": "e",
            },
        )
        p = job.progress["processos"][n]
        assert (p["docs_baixados"], p["total_docs"]) == (3, 3)

    def test_partial_total_never_below_downloaded(self, tmp_path):
        state, job = _state_and_job(tmp_path)
        n = job.processos[0]
        _progress(state, job, n, status="running", total_docs=2)
        state._apply_result(
            job,
            {
                "numeroProcesso": n,
                "status": "partial_success",
                "arquivosDownloaded": _files(5),
                "errorMessage": "e",
            },
        )
        assert job.progress["processos"][n]["total_docs"] == 5


# ───────────────────────────── M4 ─────────────────────────────


class TestTimeoutKeepsRealProgress:
    def test_running_process_keeps_counters_when_failed_by_timeout(self, tmp_path):
        state, job = _state_and_job(tmp_path)
        n = job.processos[0]
        _progress(
            state,
            job,
            n,
            status="running",
            total_docs=81,
            docs_baixados=40,
            tamanho_bytes=1234,
        )
        state._fail_remaining_processes(job, {n}, "Worker timeout")
        p = job.progress["processos"][n]
        assert p["status"] == "failed" and p["erro"] == "Worker timeout"
        assert (p["total_docs"], p["docs_baixados"], p["tamanho_bytes"]) == (
            81,
            40,
            1234,
        )

    def test_never_started_process_is_still_zero(self, tmp_path):
        state, job = _state_and_job(tmp_path)
        n = job.processos[1]
        state._fail_remaining_processes(job, {n}, "Worker timeout")
        p = job.progress["processos"][n]
        assert (p["total_docs"], p["docs_baixados"], p["tamanho_bytes"]) == (0, 0, 0)

    def test_process_unknown_to_progress_is_zero(self, tmp_path):
        state, job = _state_and_job(tmp_path)
        job.progress["processos"] = {}
        state._fail_remaining_processes(job, {job.processos[0]}, "x")
        assert job.progress["processos"][job.processos[0]]["docs_baixados"] == 0


# ───────────────────────────── relógio do teto ─────────────────────────────


class TestElapsedSinceStart:
    def test_none_and_garbage_are_zero(self):
        assert dashboard_api._elapsed_since_start_secs(None) == 0.0
        assert dashboard_api._elapsed_since_start_secs("não é data") == 0.0

    def test_future_is_zero_never_negative(self):
        future = (datetime.now(UTC) + timedelta(hours=5)).isoformat()
        assert dashboard_api._elapsed_since_start_secs(future) == 0.0

    def test_past_is_wall_clock_elapsed(self):
        past = (datetime.now(UTC) - timedelta(seconds=500)).isoformat()
        assert 499 <= dashboard_api._elapsed_since_start_secs(past) <= 520

    def test_naive_timestamp_is_treated_as_utc(self):
        past = (datetime.now(UTC) - timedelta(seconds=500)).replace(tzinfo=None)
        assert 499 <= dashboard_api._elapsed_since_start_secs(past.isoformat()) <= 520


class TestResumeDoesNotRestartTheCeiling:
    @staticmethod
    def _state(job):
        return dashboard_api.BatchPollState(
            pending={job.processos[0]},
            last_result_at=0.0,
            serialized_payloads={},
            reply_queue="q",
        )

    @pytest.mark.asyncio
    async def test_batch_started_long_ago_hits_the_ceiling_on_resume(self, tmp_path):
        ds, job = _state_and_job(tmp_path, n=3)
        job.started_at = (datetime.now(UTC) - timedelta(seconds=5000)).isoformat()
        redis_stub = _silent_worker_redis()

        async def blpop(key, timeout=0):
            await asyncio.sleep(0)  # yield, so wait_for can cancel a regression
            return None

        redis_stub.blpop = blpop
        poll = self._state(job)
        with (
            patch("dashboard_api.BATCH_MAX_DURATION_SECS", 3600),
            patch("dashboard_api.BATCH_SECS_PER_PROCESSO", 10),
            patch("dashboard_api.RESULT_WAIT_TIMEOUT_SECS", 10**6),
        ):
            await asyncio.wait_for(
                ds._poll_results_loop(job, redis_stub, poll), timeout=3
            )
        assert poll.timed_out is True
        assert "absolute timeout" in job.error.lower()

    @pytest.mark.asyncio
    async def test_fresh_batch_is_not_cut_short(self, tmp_path):
        ds, job = _state_and_job(tmp_path, n=3)
        job.started_at = datetime.now(UTC).isoformat()
        redis_stub = _silent_worker_redis()
        calls = {"n": 0}

        async def blpop(key, timeout=0):
            calls["n"] += 1
            if calls["n"] >= 3:
                raise RuntimeError("stop")
            return None

        redis_stub.blpop = blpop
        poll = self._state(job)
        with (
            patch("dashboard_api.BATCH_MAX_DURATION_SECS", 3600),
            patch("dashboard_api.RESULT_WAIT_TIMEOUT_SECS", 10**6),
            pytest.raises(RuntimeError),
        ):
            await ds._poll_results_loop(job, redis_stub, poll)
        assert poll.timed_out is False

    @pytest.mark.asyncio
    async def test_future_started_at_does_not_make_a_negative_ceiling(self, tmp_path):
        ds, job = _state_and_job(tmp_path, n=3)
        job.started_at = (datetime.now(UTC) + timedelta(days=2)).isoformat()
        redis_stub = _silent_worker_redis()
        calls = {"n": 0}

        async def blpop(key, timeout=0):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise RuntimeError("stop")
            return None

        redis_stub.blpop = blpop
        poll = self._state(job)
        with (
            patch("dashboard_api.BATCH_MAX_DURATION_SECS", 3600),
            patch("dashboard_api.RESULT_WAIT_TIMEOUT_SECS", 10**6),
            pytest.raises(RuntimeError),
        ):
            await ds._poll_results_loop(job, redis_stub, poll)
        assert poll.timed_out is False


# ───────────────────────────── audit ─────────────────────────────


@pytest.mark.asyncio
async def test_status_reports_audit_sync_and_retention(tmp_path):
    async with TestClient(TestServer(create_app(tmp_path))) as client:
        with (
            patch("dashboard_api.AUDIT_SYNC_ENABLED", False),
            patch("dashboard_api.AUDIT_LOG_RETENTION_DAYS", 120),
            patch(
                "dashboard_api._fetch_worker_health",
                AsyncMock(return_value={"status": "healthy"}),
            ),
        ):
            resp = await client.get("/api/status")
            body = await resp.json()
    assert body["audit"] == {"sync_enabled": False, "retention_days": 120}


class TestRotationVisibility:
    def _run_startup(self, deleted, sync_enabled):
        with (
            patch("audit.rotate_logs", return_value=deleted),
            patch("dashboard_api.AUDIT_SYNC_ENABLED", sync_enabled),
            structlog.testing.capture_logs() as logs,
        ):
            dashboard_api._rotate_audit_logs_on_startup()
        return [e for e in logs if e["event"] == "dashboard.audit.rotation_complete"]

    def test_deleted_without_sync_is_a_warning(self):
        (entry,) = self._run_startup(3, False)
        assert entry["log_level"] == "warning"
        assert entry["deleted"] == 3 and entry["sync_enabled"] is False

    def test_deleted_with_sync_stays_info(self):
        (entry,) = self._run_startup(3, True)
        assert entry["log_level"] == "info"

    @pytest.mark.asyncio
    async def test_periodic_rotation_also_warns_without_sync(self):
        sleeps = {"n": 0}

        async def fake_sleep(_):
            sleeps["n"] += 1
            if sleeps["n"] > 1:
                raise asyncio.CancelledError

        with (
            patch("audit.rotate_logs", return_value=2),
            patch("dashboard_api.AUDIT_SYNC_ENABLED", False),
            patch("dashboard_api.asyncio.sleep", fake_sleep),
            structlog.testing.capture_logs() as logs,
            pytest.raises(asyncio.CancelledError),
        ):
            await dashboard_api._audit_rotation_loop(1)
        (entry,) = [
            e for e in logs if e["event"] == "dashboard.audit.rotation_complete"
        ]
        assert entry["log_level"] == "warning" and entry["periodic"] is True


# ───────────────────────────── include_anexos ─────────────────────────────


async def _post(tmp_path, payload):
    async with TestClient(TestServer(create_app(tmp_path))) as client:
        seen = []

        async def fake_submit(ps, include_anexos=True, gdrive_map=None, **kwargs):
            seen.append(include_anexos)
            return BatchJob(id="fake", processos=ps, status="queued", created_at="x")

        client.app[APP_CTX_KEY].state.submit_batch = fake_submit
        resp = await client.post("/api/download", json=payload)
        return resp.status, await resp.json(), seen


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["false", "true", 0, 1, None, []])
async def test_include_anexos_must_be_a_strict_bool(tmp_path, bad):
    status, body, seen = await _post(
        tmp_path, {"processos": [_num(1)], "include_anexos": bad}
    )
    assert status == 400
    assert "include_anexos" in body["error"]
    assert seen == []


@pytest.mark.asyncio
@pytest.mark.parametrize("good", [True, False])
async def test_include_anexos_bool_is_accepted(tmp_path, good):
    status, _, seen = await _post(
        tmp_path, {"processos": [_num(1)], "include_anexos": good}
    )
    assert status == 201
    assert seen == [good]


@pytest.mark.asyncio
async def test_include_anexos_omitted_defaults_to_true(tmp_path):
    status, _, seen = await _post(tmp_path, {"processos": [_num(1)]})
    assert status == 201 and seen == [True]
