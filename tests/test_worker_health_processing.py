"""O worker não pode se declarar doente só porque está trabalhando.

``download_process`` marca ``_health_status = "processing"`` durante todo o job,
e o ``/health`` só considerava ``ready``/``consuming`` saudáveis. Resultado:
HTTP 503 e ``healthy: false`` a cada job, o healthcheck do Docker
(``curl -sf``) falhando em jobs longos e a dashboard descartando o corpo do 503
(``_fetch_worker_health`` devolve ``status: unhealthy`` para qualquer não-200).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.test_worker_publish_error_containment import _load_worker_module


async def _health(status: str, *, redis_ok: bool = True):
    w = _load_worker_module()
    worker = w.PJeSessionWorker()
    worker.mni_client = None
    worker.redis = AsyncMock()
    if not redis_ok:
        worker.redis.ping = AsyncMock(side_effect=ConnectionError("down"))
    worker._health_status = status
    resp = await worker._health_handler(MagicMock())
    return w, resp.status, json.loads(resp.body)


class TestHealthWhileWorking:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", ["ready", "consuming", "processing"])
    async def test_working_states_are_healthy(self, status):
        _, code, body = await _health(status)
        assert code == 200 and body["healthy"] is True
        assert body["status"] == status

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "status", ["redis_unreachable", "session_expired", "captcha_required"]
    )
    async def test_fatal_states_stay_unhealthy(self, status):
        _, code, body = await _health(status)
        assert code == 503 and body["healthy"] is False
        assert body["status"] == status

    @pytest.mark.asyncio
    async def test_processing_does_not_hide_a_real_redis_failure(self):
        _, code, body = await _health("processing", redis_ok=False)
        assert code == 503 and body["healthy"] is False
        assert body["checks"]["redis"] == "unreachable"

    def test_the_healthy_set_is_explicit(self):
        w = _load_worker_module()
        assert w.HEALTHY_STATUSES == frozenset({"ready", "consuming", "processing"})
