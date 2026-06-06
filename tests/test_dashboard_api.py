"""Tests for dashboard_api — max batch size, progress cache, graceful shutdown."""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from prometheus_client import generate_latest

import dashboard_api
from dashboard_api import (
    MAX_BATCH_SIZE,
    MAX_BATCH_HISTORY,
    BatchJob,
    DashboardState,
)

# Ensure internal rate-limiter state exists for test environments that don't
# initialize module-level dicts at import time. Some CI/test runners import
# dashboard_api in a way that skips module-level initialization; tests assume
# these dicts exist and mutate them directly, so make them explicit here.
dashboard_api._rate_buckets = getattr(dashboard_api, "_rate_buckets", {})
dashboard_api._rate_bucket_last_seen = getattr(
    dashboard_api, "_rate_bucket_last_seen", {}
)


# ─────────────────────────────────────────────
# MAX_BATCH_SIZE constant
# ─────────────────────────────────────────────


def test_max_batch_size_is_500():
    assert MAX_BATCH_SIZE == 500

# ... rest of file unchanged
