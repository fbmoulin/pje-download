FROM python:3.12-slim

# ── System user (before code copy for layer caching) ──
RUN groupadd --gid 1001 appuser && \
    useradd --uid 1001 --gid appuser --create-home appuser

# ── System deps (Xvfb for headless Playwright, curl for healthcheck) ──
RUN apt-get update && \
    apt-get install -y --no-install-recommends xvfb curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Python deps (cached layer) ──
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    playwright install chromium --with-deps

# ── Application code ──
COPY --chown=appuser:appuser config.py .
COPY --chown=appuser:appuser mni_client.py .
COPY --chown=appuser:appuser gdrive_downloader.py .
COPY --chown=appuser:appuser batch_downloader.py .
COPY --chown=appuser:appuser worker.py .
COPY --chown=appuser:appuser dashboard_api.py .
COPY --chown=appuser:appuser dashboard.html .
COPY --chown=appuser:appuser static/ static/

# ── Data directory ──
RUN mkdir -p /data/downloads && chown -R appuser:appuser /data

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -sf http://127.0.0.1:${HEALTH_PORT:-8006}/health || \
        curl -sf http://127.0.0.1:${DASHBOARD_PORT:-8007}/api/status || \
        exit 1

EXPOSE 8006 8007

USER appuser

# Default: worker mode. Override with dashboard_api.py for dashboard.
CMD ["python", "worker.py"]
