"""Build identity: the running code must be able to name the commit it was built from.

Why these tests assert on the response BODY and not on the helper: a test that only
exercises `config.build_identity()` passes even when neither endpoint is wired up, which
is precisely the state this change exists to leave behind. The endpoint assertions carry
the contract; the helper assertions only pin its defaulting rules.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

import config
import dashboard_api

from tests.test_dashboard_api import DummyRequest, _make_ctx
from tests.test_worker import _load_worker_module


class TestBuildIdentityHelper:
    def test_defaults_to_unknown_when_unset(self, monkeypatch):
        monkeypatch.delenv("BUILD_SHA", raising=False)
        assert config.build_identity() == "unknown"

    def test_defaults_to_unknown_when_blank(self, monkeypatch):
        """An empty build arg is indistinguishable from a missing one, and must not
        masquerade as a real identity — `BUILD_SHA=` is what an unthreaded build arg
        produces."""
        monkeypatch.setenv("BUILD_SHA", "   ")
        assert config.build_identity() == "unknown"

    def test_returns_the_injected_sha(self, monkeypatch):
        monkeypatch.setenv("BUILD_SHA", "3968971abcdef")
        assert config.build_identity() == "3968971abcdef"

    def test_is_read_at_call_time_not_at_import(self, monkeypatch):
        """A module-level constant would freeze the value at import, before
        `load_env()` has necessarily run, and could not be exercised without
        `importlib.reload`."""
        monkeypatch.setenv("BUILD_SHA", "first")
        assert config.build_identity() == "first"
        monkeypatch.setenv("BUILD_SHA", "second")
        assert config.build_identity() == "second"


class TestWorkerHealthCarriesBuildSha:
    @pytest.mark.asyncio
    async def test_health_body_reports_the_injected_sha(self, monkeypatch):
        monkeypatch.setenv("BUILD_SHA", "deadbeefcafe")
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        worker.redis = AsyncMock()
        worker.redis.ping = AsyncMock(return_value=True)
        worker.mni_client = MagicMock()
        worker._health_status = "consuming"

        response = await worker._health_handler(MagicMock())
        body = json.loads(response.text)

        assert body["build_sha"] == "deadbeefcafe"

    @pytest.mark.asyncio
    async def test_health_body_reports_unknown_without_the_arg(self, monkeypatch):
        """The deploy treats "unknown" as a failure, so the endpoint must actually
        emit it rather than omitting the key."""
        monkeypatch.delenv("BUILD_SHA", raising=False)
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        worker.redis = AsyncMock()
        worker.redis.ping = AsyncMock(return_value=True)
        worker.mni_client = MagicMock()
        worker._health_status = "consuming"

        response = await worker._health_handler(MagicMock())
        body = json.loads(response.text)

        assert body["build_sha"] == "unknown"


class TestDashboardHealthzCarriesBuildSha:
    @pytest.mark.asyncio
    async def test_healthz_body_reports_the_injected_sha(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BUILD_SHA", "deadbeefcafe")
        ds = dashboard_api.DashboardState(tmp_path)
        ds.get_redis = AsyncMock(
            return_value=AsyncMock(ping=AsyncMock(return_value=True))
        )
        ctx = _make_ctx(state=ds)

        resp = await dashboard_api.handle_healthz(DummyRequest(ctx=ctx))
        body = json.loads(resp.body.decode())

        assert body["build_sha"] == "deadbeefcafe"

    @pytest.mark.asyncio
    async def test_healthz_stays_public_and_still_reports_readiness(
        self, tmp_path, monkeypatch
    ):
        """Guard against the build_sha addition changing the endpoint's contract:
        /healthz is unauthenticated on purpose (that is what makes the deploy
        assertion checkable over HTTP), and it must keep reporting readiness."""
        monkeypatch.delenv("BUILD_SHA", raising=False)
        ds = dashboard_api.DashboardState(tmp_path)
        ds.get_redis = AsyncMock(
            return_value=AsyncMock(ping=AsyncMock(return_value=True))
        )
        ctx = _make_ctx(state=ds)

        resp = await dashboard_api.handle_healthz(DummyRequest(ctx=ctx))
        body = json.loads(resp.body.decode())

        assert resp.status == 200
        assert body["ready"] is True
        assert body["build_sha"] == "unknown"
        assert "/healthz" in dashboard_api._AUTH_PUBLIC_PREFIXES
