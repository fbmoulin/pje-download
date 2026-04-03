FROM python:3.12-slim AS base

# ── System user (before code copy for layer caching) ──
RUN groupadd --gid 1001 appuser && \
    useradd --uid 1001 --gid appuser --create-home appuser

WORKDIR /app

# ── Python deps (cached layer) ──
COPY requirements.txt .

# ── Dashboard target: no Playwright, no Xvfb ──
FROM base AS dashboard
RUN pip install --no-cache-dir aiohttp prometheus_client structlog zeep requests gdown && \
    apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*
COPY --chown=appuser:appuser config.py mni_client.py gdrive_downloader.py \
     batch_downloader.py dashboard_api.py dashboard.html metrics.py ./
COPY --chown=appuser:appuser static/ static/
RUN mkdir -p /data/downloads && chown -R appuser:appuser /data
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -sf http://127.0.0.1:8007/api/status || exit 1
EXPOSE 8007
USER appuser
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
CMD ["python", "worker.py"]
