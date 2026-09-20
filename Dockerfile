FROM python:3.12-slim AS base

# ── System user (before code copy for layer caching) ──
RUN groupadd --gid 1001 appuser && \
    useradd --uid 1001 --gid appuser --create-home appuser

WORKDIR /app

# ── Python deps (cached layer) ──
COPY requirements.txt .

# ── Dashboard target: no Playwright, no Xvfb ──
FROM base AS dashboard
# Versions come from requirements.txt — DERIVED, never a second list.
#
# This used to be a hand-written list of bare package names with no version
# specifier at all, so the dashboard image ignored every pin the repo has:
# measured 2026-09-20, a rebuild shipped redis 8.1.0, structlog 26.1.0 (a major)
# and aiohttp 3.14.3 while requirements.txt pinned 8.0.0 / 25.5.0 / 3.14.1. CI
# tests the pinned set, so nothing green said anything about what the dashboard
# actually ran, and the two containers of one deploy ran different libraries.
# That is the same class as the redis-py 8.0.0 socket_timeout outage (PR #32),
# whose stated defence is precisely this pin.
#
# Only playwright is dropped: it is 139 MB and the dashboard never imports it
# (pje_session imports it lazily, inside functions). Deriving by exclusion
# rather than maintaining a second pin list is deliberate — a second list is
# what drifted here, and what drifted in the COPY list fixed by 237ea55. A new
# dependency added to requirements.txt now reaches this image automatically.
RUN grep -vE '^playwright[=<>~!]' requirements.txt > /tmp/requirements-dashboard.txt && \
    pip install --no-cache-dir -r /tmp/requirements-dashboard.txt && \
    rm /tmp/requirements-dashboard.txt && \
    apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*
# Copy all first-party modules (was an explicit list that drifted — Sprint 13/14
# helpers async_retry.py/file_utils.py/protocol.py were missing, crash-looping the
# dashboard on `from async_retry import AsyncRetry`). *.py mirrors the worker's COPY . .
COPY --chown=appuser:appuser *.py dashboard.html ./
COPY --chown=appuser:appuser static/ static/
COPY --chown=appuser:appuser migrations/ migrations/
RUN mkdir -p /data/downloads && chown -R appuser:appuser /data
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -sf http://127.0.0.1:8007/api/status || exit 1
EXPOSE 8007
USER appuser
# ── Build identity — MUST stay last ──
# An ARG invalidates every layer below it. Near the top of this file it would
# force pip install (and, in the worker target, `playwright install chromium`)
# to re-run on every single deploy. Here the only layer it busts is its own.
# ARG is also stage-scoped: declaring it once in `base` would not reach this
# target, so each target declares its own.
ARG BUILD_SHA=unknown
ENV BUILD_SHA=${BUILD_SHA}
LABEL org.opencontainers.image.revision="${BUILD_SHA}"
CMD ["python", "dashboard_api.py", "--port", "8007", "--output", "/data/downloads"]

# ── Worker target: includes Playwright + Xvfb ──
FROM base AS worker
RUN apt-get update && \
    apt-get install -y --no-install-recommends xvfb curl fonts-liberation \
    libasound2t64 libatk-bridge2.0-0t64 libdrm2 libgbm1 libnss3 libxss1 && \
    rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r requirements.txt && \
    playwright install chromium
COPY --chown=appuser:appuser . .
RUN mkdir -p /data/downloads && chown -R appuser:appuser /data
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -sf http://127.0.0.1:8006/health || exit 1
EXPOSE 8006
USER appuser
# ── Build identity — MUST stay last (see the note in the dashboard target) ──
# This target is the expensive one: `pip install -r requirements.txt` plus
# `playwright install chromium`. An ARG above those turns every deploy into a
# multi-minute rebuild.
ARG BUILD_SHA=unknown
ENV BUILD_SHA=${BUILD_SHA}
LABEL org.opencontainers.image.revision="${BUILD_SHA}"
CMD ["python", "worker.py"]
