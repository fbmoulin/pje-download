"""Focused coverage for dashboard POST rate limiting under concurrent scheduling."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from aiohttp import web

import dashboard_api
from dashboard_api import APP_CTX_KEY, AppContext


class DummyRequest:
    """Minimal request object for direct middleware tests."""

    def __init__(self, *, method: str = "POST", remote: str = "127.0.0.1"):
        self.method = method
        self.remote = remote
        self.headers: dict[str, str] = {}
        self.app = {APP_CTX_KEY: AppContext(state=MagicMock())}


async def _ok_handler(request):
    return web.Response(text="ok")


@pytest.mark.asyncio
async def test_concurrent_requests_from_same_ip_rate_limited():
    """Concurrent POSTs from one IP must saturate the sliding-window bucket."""
    ip = "10.77.77.77"
    ctx = AppContext(state=MagicMock())

    async def one_request():
        request = DummyRequest(method="POST", remote=ip)
        request.app[APP_CTX_KEY] = ctx
        return await dashboard_api.rate_limit_middleware(request, _ok_handler)

    responses = await asyncio.gather(*[one_request() for _ in range(15)])
    statuses = [response.status for response in responses]

    assert statuses.count(429) >= 5, (
        f"Expected at least 5 rate-limited responses: {statuses}"
    )
    assert ip in ctx.rate_buckets
