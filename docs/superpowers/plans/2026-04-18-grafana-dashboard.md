# Grafana Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provision a Grafana + Prometheus + Alertmanager monitoring stack on the openclaw VPS that scrapes pje-download's `/metrics` and `/health*` endpoints over Tailscale, renders 8 operational panels, and pages `@kaiOpsBot` on Telegram for 5 alert conditions. Closes backlog item #2 (CLAUDE.md).

**Architecture:** Single `docker-compose.yml` stack at `ops/monitoring/stack/` containing Prometheus 2.55, Grafana 11.3, Alertmanager 0.27, blackbox_exporter 0.25. Scrapes 4 targets over the tailnet: `:8007/metrics` (dashboard), `:8006/metrics` (worker — requires one 5-LOC code change in `worker.py`), plus blackbox probes of `:8007/healthz` and `:8006/health`. Per-app artifacts (alert rules, dashboard JSON) live under `ops/monitoring/pje/` to co-evolve with `metrics.py`. No changes to `dashboard_api.py` or any business logic.

**Tech Stack:** Prometheus 2.55, Grafana 11.3, Alertmanager 0.27 (native `telegram_configs` receiver), prometheus/blackbox_exporter 0.25, Tailscale (already being installed on both VPS — out of this plan's scope, covered in `DEPLOY.md`), aiohttp (existing dep in `worker.py`), prometheus-client (existing dep).

**Reference spec:** `docs/superpowers/specs/2026-04-18-grafana-dashboard-design.md`

---

## File Structure

```
ops/monitoring/                          # NEW DIRECTORY
  README.md                              # Task 12
  verify.sh                              # Task 2 (validator — written before the configs it checks)
  pje/
    alert-rules.yml                      # Task 3
    dashboard.json                       # Task 4
  stack/
    docker-compose.yml                   # Task 9 (last — ties everything together)
    prometheus.yml                       # Task 5
    alertmanager.yml                     # Task 6
    blackbox.yml                         # Task 7
    grafana/
      provisioning/
        datasources/prometheus.yml       # Task 8a
        dashboards/default.yml           # Task 8b
    DEPLOY.md                            # Task 11

worker.py                                # Task 1 (modify: +1 route, +3 imports)
tests/test_worker.py                     # Task 1 (modify: +1 test for /metrics endpoint)
CLAUDE.md                                # Task 13 (modify: backlog item #2 marked done, new Observability section)
```

**Why this order:**
- Task 1 first: unblocks the alert `PjeWorkerDeadLetters` (worker metrics invisible without `/metrics` endpoint — see spec §2a).
- Task 2 (verify.sh) second: all subsequent config tasks use it as their "test" step, so a failing configuration fails loudly at commit time rather than at deploy.
- Per-app artifacts (Tasks 3-4) before stack infra (Tasks 5-9): the stack configs reference the per-app paths via bind mount.
- Task 9 (`docker-compose.yml`) last in stack phase: depends on every other stack file existing.
- Task 10 (e2e): the final gate before handoff docs.

---

## Project Conventions (read once, applies everywhere)

- **Commit style:** Conventional commits in Portuguese descriptions (see `git log` for examples). No `Co-Authored-By` trailers — Felipe is sole author. Reference: `~/.claude/projects/-home-fbmoulin/memory/feedback_no-coauthorship.md`.
- **Tests:** `pytest tests/ -q` from repo root. 377 tests currently pass; this plan adds one test → 378.
- **Linter:** `ruff check <files> && ruff format <files>` before every commit (CLAUDE.md line 16-17).
- **CWD:** Always `/home/fbmoulin/projetos-2026/pje-download/pje-download`. Never `cd` to subdirs — use relative paths from repo root.
- **Metrics registry:** `metrics.py` uses a dedicated `CollectorRegistry` (not global default). When exporting from the worker, use `generate_latest(metrics.REGISTRY)` — same pattern as `dashboard_api.py:1159`.
- **Worker health bind:** `HEALTH_BIND_HOST` defaults to `127.0.0.1` in `config.py`; `docker-compose.yml:112` overrides to `0.0.0.0`. Preserve that override. Do not harden it back to 127.0.0.1 — breaks every `:8006/*` scrape.
- **Docker image pins:** All version tags go in `ops/monitoring/stack/docker-compose.yml`. Use exact tags (not `latest`).
- **PromQL quirk:** `histogram_quantile` needs `le` label in its input. Pattern: `histogram_quantile(0.95, sum(rate(metric_bucket[5m])) by (le, <other_labels>))`. Getting this wrong returns NaN, not an error — silent failure.

---

## Task 1: Add `/metrics` endpoint to `worker.py` (TDD)

**Files:**
- Modify: `worker.py` (add 1 route + 2 imports next to existing `/health` route at line 1672)
- Modify: `tests/test_worker.py` (add 1 test class/function)

**Rationale:** `worker.py:1451-1554` increments 4 worker metrics (`worker_results_total`, `worker_progress_events_total`, `worker_dead_letters_total`, `worker_publish_failures_total`) but the worker process never exposes `/metrics`. The dashboard's `:8007/metrics` uses a per-process `CollectorRegistry` so it does not include any of these counters. Alert #3 from the backlog (`pje_worker_dead_letters_total > 0`) is unobservable until this task lands.

- [ ] **Step 1: Read the existing `/health` route registration for pattern context**

Run: `sed -n '1660,1680p' worker.py`

Confirm the current `start_health_server` method only registers one route (`/health`). The new `/metrics` route goes right next to it.

- [ ] **Step 2: Read the dashboard's `/metrics` handler to mirror its style**

Run: `sed -n '1153,1165p' dashboard_api.py`

Note the exact pattern: `generate_latest(m.REGISTRY)` returned with `headers={"Content-Type": CONTENT_TYPE_LATEST}`. We copy that pattern in the worker.

- [ ] **Step 3: Write the failing test (add to `tests/test_worker.py`)**

**Important:** the existing file uses a helper `_load_worker_module()` at line 11 that mocks `redis`, `redis.asyncio`, `playwright`, `playwright.async_api`, and `mni_client` in `sys.modules` before importing `worker`. Without this helper the import fails or side-effects production. All async tests use `@pytest.mark.asyncio`. The class name is `PJeSessionWorker` (not `PJeWorker`). Additionally, `config.py:94-95` captures `HEALTH_PORT` / `HEALTH_BIND_HOST` at its module load and `worker.py` imports them with `from config import ...` — reloading `worker` does NOT re-read `config`'s values. So monkeypatching env vars has no effect on the already-bound names; we must rebind them on the worker module directly after loading.

Find the end of `tests/test_worker.py` and append:

```python
# ──────────────────────────────────────────────────────────────────────────
# /metrics endpoint (Prometheus scrape) — added for Grafana dashboard (P0.4)
# ──────────────────────────────────────────────────────────────────────────


class TestWorkerMetricsEndpoint:
    """The worker must expose its Prometheus registry at /metrics on the
    same aiohttp health server that serves /health, so that Prometheus can
    scrape worker-side counters (dead_letters, publish_failures, results,
    progress_events) that are unreachable via the dashboard's /metrics.
    """

    @pytest.mark.asyncio
    async def test_metrics_endpoint_returns_prometheus_text(self, monkeypatch):
        """GET /metrics returns Prometheus text with worker-counter names."""
        w = _load_worker_module()

        # Rebind module-level constants: worker.py does `from config import
        # HEALTH_PORT, HEALTH_BIND_HOST` at load time, so env-var monkeypatching
        # has no effect after import. We rebind directly on the worker module.
        monkeypatch.setattr(w, "HEALTH_PORT", 18006)  # off-band from prod :8006
        monkeypatch.setattr(w, "HEALTH_BIND_HOST", "127.0.0.1")

        import metrics as m
        # Pre-populate a known counter so the scrape output is non-trivial.
        # NOTE: metrics.REGISTRY is process-wide; this leaves a stale sample
        # in the counter for the rest of the test session. Any future test
        # asserting exact counter values across this line must account for it.
        m.worker_dead_letters_total.labels(reason="__pje_metrics_smoke__").inc()

        pje_worker = w.PJeSessionWorker()
        await pje_worker.start_health_server()
        try:
            import aiohttp
            async with aiohttp.ClientSession() as sess:
                async with sess.get("http://127.0.0.1:18006/metrics") as resp:
                    assert resp.status == 200
                    assert resp.headers["Content-Type"].startswith("text/plain")
                    body = await resp.text()
                    assert "pje_worker_dead_letters_total" in body
                    assert 'reason="__pje_metrics_smoke__"' in body
        finally:
            await pje_worker.stop_health_server()
```

Commit nothing yet — this test is expected to fail.

- [ ] **Step 4: Run test, verify it fails**

Run: `pytest tests/test_worker.py::TestWorkerMetricsEndpoint::test_metrics_endpoint_returns_prometheus_text -v`

Expected: FAIL with `AssertionError: assert 404 == 200` (the route does not exist yet).

If the error is **anything else**, stop and investigate — something in the test setup is broken, not the production code. Common wrong failures:
- `AttributeError: module 'worker' has no attribute 'PJeSessionWorker'` → class was renamed; re-verify with `grep '^class PJe' worker.py`.
- `ModuleNotFoundError: No module named 'redis'` → the `_load_worker_module()` helper wasn't used; re-read Step 3.
- `OSError: [Errno 98] Address already in use` → another worker is bound to the same port; change `monkeypatch.setattr(w, "HEALTH_PORT", 18006)` to a different free port.
- Warning "coroutine was never awaited" / test "SKIPPED" → `@pytest.mark.asyncio` decorator missing.

- [ ] **Step 5: Implement the route**

Edit `worker.py` at the `start_health_server` method (currently around line 1664-1679). Replace the method body so it registers both `/health` and the new `/metrics` route. The exact diff:

Find:
```python
    async def start_health_server(self) -> None:
        """Inicia servidor HTTP minimalista para health checks."""
        from aiohttp import web

        if self._health_runner is not None:
            return

        app = web.Application()
        app.router.add_get("/health", self._health_handler)
```

Replace with:
```python
    async def start_health_server(self) -> None:
        """Inicia servidor HTTP minimalista para health checks + /metrics."""
        from aiohttp import web

        if self._health_runner is not None:
            return

        app = web.Application()
        app.router.add_get("/health", self._health_handler)
        app.router.add_get("/metrics", self._metrics_handler)
```

Then find `stop_health_server` and, immediately BEFORE it, add the new handler method (keeps handlers grouped):

```python
    async def _metrics_handler(self, request):
        """GET /metrics — Prometheus scrape of worker-process registry.

        Worker-process counters (dead_letters, publish_failures, results,
        progress_events) live in their own CollectorRegistry; the dashboard's
        /metrics cannot see them. This endpoint exposes them to Prometheus.
        """
        from aiohttp import web
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

        import metrics as m

        return web.Response(
            body=generate_latest(m.REGISTRY),
            headers={"Content-Type": CONTENT_TYPE_LATEST},
        )
```

Do NOT touch `_health_handler` or anything else.

- [ ] **Step 6: Run the test, verify it passes**

Run: `pytest tests/test_worker.py::TestWorkerMetricsEndpoint::test_metrics_endpoint_returns_prometheus_text -v`

Expected: PASS. If it fails, the most likely cause is the handler not finding `metrics.REGISTRY` — check that `import metrics as m` inside the handler is idempotent (it is — `metrics.py` has no env-var deps per CLAUDE.md).

- [ ] **Step 7: Run the full suite to confirm no regressions**

Run: `pytest tests/ -q`

Expected: `378 passed` (was 377, +1 new test). If any previously-passing test now fails, revert `worker.py` and investigate.

- [ ] **Step 8: Lint**

Run: `ruff check worker.py tests/test_worker.py && ruff format worker.py tests/test_worker.py`

Expected: no errors. If `ruff format` changes anything, inspect the diff — should be whitespace only.

- [ ] **Step 9: Commit**

```bash
git add worker.py tests/test_worker.py
git commit -m "feat(worker): expose /metrics for Prometheus scrape

Worker-process metrics (dead_letters, publish_failures, results,
progress_events) lived in worker.py's CollectorRegistry but never
reached Prometheus — dashboard_api.py's /metrics only reads the
dashboard process's registry.

Add /metrics route to the existing aiohttp health server on :8006
(bind-host override 0.0.0.0 in docker-compose.yml preserved). Route
mirrors dashboard_api.py:1153-1159 pattern: generate_latest(REGISTRY)
with CONTENT_TYPE_LATEST header.

+1 test (378 total). Pre-req for backlog #2 grafana alerts."
```

---

## Task 2: Write `ops/monitoring/verify.sh` (validator — runs before every subsequent task)

**Files:**
- Create: `ops/monitoring/verify.sh` (executable)

**Rationale:** Every subsequent task produces YAML/JSON that we want to validate syntactically before commit. `promtool`, `amtool`, and `jq` catch 95% of schema mistakes. Writing the validator first means every following task's Step-N ("Run verify.sh, expect PASS") is actionable from the start.

- [ ] **Step 1: Create the directory tree**

```bash
mkdir -p ops/monitoring/pje
mkdir -p ops/monitoring/stack/grafana/provisioning/datasources
mkdir -p ops/monitoring/stack/grafana/provisioning/dashboards
```

- [ ] **Step 2: Write `verify.sh`**

Create `ops/monitoring/verify.sh`:

```bash
#!/usr/bin/env bash
# ops/monitoring/verify.sh
# Static validation for the monitoring stack configs. Run from repo root.
# Exits non-zero on any syntax/schema error.
#
# Requires: promtool, amtool, jq on PATH. Falls back to docker images if
# the binaries are missing, so a fresh dev machine doesn't need to install
# the Prometheus toolbelt locally.

set -euo pipefail

cd "$(dirname "$0")/../.."   # repo root (two levels up from ops/monitoring/)

echo "→ verify.sh: validating ops/monitoring/ artifacts"

PROMTOOL="${PROMTOOL:-promtool}"
AMTOOL="${AMTOOL:-amtool}"

# If promtool / amtool missing, use docker wrappers
if ! command -v "$PROMTOOL" >/dev/null 2>&1; then
    PROMTOOL="docker run --rm -v $PWD:/work -w /work prom/prometheus:v2.55.0 promtool"
fi
if ! command -v "$AMTOOL" >/dev/null 2>&1; then
    AMTOOL="docker run --rm -v $PWD:/work -w /work prom/alertmanager:v0.27.0 amtool"
fi

# ── 1. Prometheus alert rules (per-app) ───────────────────────────────
if [ -f ops/monitoring/pje/alert-rules.yml ]; then
    echo "  checking ops/monitoring/pje/alert-rules.yml"
    $PROMTOOL check rules ops/monitoring/pje/alert-rules.yml
fi

# ── 2. Prometheus main config (with dummy envsubst for ${PJE_TAILNET_IP}) ─
# promtool validates target host:port syntax, so a raw ${PJE_TAILNET_IP}
# placeholder fails. Substitute a dummy before piping to promtool.
if [ -f ops/monitoring/stack/prometheus.yml ]; then
    echo "  checking ops/monitoring/stack/prometheus.yml (with dummy envsubst)"
    PJE_TAILNET_IP=127.0.0.1 envsubst '${PJE_TAILNET_IP}' \
        < ops/monitoring/stack/prometheus.yml \
        | $PROMTOOL check config /dev/stdin
fi

# ── 3. Alertmanager config (with dummy envsubst for ${BOT_TOKEN} ${CHAT_ID}) ─
# Raw ${CHAT_ID} placeholder is not a valid YAML integer; amtool rejects it.
# envsubst with whitelist avoids eating stray $-sigils elsewhere in the file.
if [ -f ops/monitoring/stack/alertmanager.yml ]; then
    echo "  checking ops/monitoring/stack/alertmanager.yml (with dummy envsubst)"
    BOT_TOKEN=dummy CHAT_ID=0 envsubst '${BOT_TOKEN} ${CHAT_ID}' \
        < ops/monitoring/stack/alertmanager.yml \
        | $AMTOOL check-config /dev/stdin
fi

# ── 4. Grafana dashboard JSON (parse only) ────────────────────────────
if [ -f ops/monitoring/pje/dashboard.json ]; then
    echo "  checking ops/monitoring/pje/dashboard.json (jq parse)"
    jq . ops/monitoring/pje/dashboard.json >/dev/null
fi

# ── 5. docker-compose (syntax) ────────────────────────────────────────
if [ -f ops/monitoring/stack/docker-compose.yml ]; then
    echo "  checking ops/monitoring/stack/docker-compose.yml (compose config)"
    docker compose -f ops/monitoring/stack/docker-compose.yml config -q
fi

echo "✓ verify.sh: all present artifacts valid"
```

- [ ] **Step 3: Make executable**

Run: `chmod +x ops/monitoring/verify.sh`

- [ ] **Step 4: Smoke test — run it against an empty directory (no configs yet, all checks skipped)**

Run: `./ops/monitoring/verify.sh`

Expected output:
```
→ verify.sh: validating ops/monitoring/ artifacts
✓ verify.sh: all present artifacts valid
```

(No file checks run because none of the five files exist yet — each `if [ -f ... ]` returns false. This is correct. The script becomes progressively stricter as subsequent tasks add files.)

- [ ] **Step 5: Commit**

```bash
git add ops/monitoring/verify.sh
git commit -m "ops(monitoring): add verify.sh static config validator

Wrapper around promtool/amtool/jq/docker-compose-config that lints
every artifact under ops/monitoring/ that exists. Falls back to
docker images when host tools are missing so it works on a fresh
dev machine. Called from every subsequent monitoring task as the
'test' step before commit."
```

---

## Task 3: Write `ops/monitoring/pje/alert-rules.yml`

**Files:**
- Create: `ops/monitoring/pje/alert-rules.yml`

- [ ] **Step 1: Write the rules file**

Create `ops/monitoring/pje/alert-rules.yml` with this exact content (matches spec §7 verbatim):

```yaml
# pje-download alert rules — Grafana dashboard P0.4
# Evaluated every 30s by Prometheus. Labels: severity, app=pje-download.
#
# Spec: docs/superpowers/specs/2026-04-18-grafana-dashboard-design.md §7

groups:
  - name: pje-download.audit-sync
    interval: 30s
    rules:
      - alert: PjeAuditSyncLagHigh
        expr: pje_audit_sync_lag_seconds > 60
        for: 2m
        labels:
          severity: warning
          app: pje-download
        annotations:
          summary: "Audit sync lag > 60s ({{ $value | humanizeDuration }})"
          description: "Railway sync atrás do JSON-L local. Railway degradado ou syncer travado."
          runbook: "docker compose logs dashboard | grep audit_sync"

      - alert: PjeAuditSyncBatchesFailing
        expr: increase(pje_audit_sync_batches_total{status="failed"}[10m]) > 0
        for: 0s
        labels:
          severity: critical
          app: pje-download
        annotations:
          summary: "Railway audit sync failing ({{ $value }} batches / 10min)"
          description: "Railway Postgres indisponível ou credenciais inválidas. JSON-L local continua sendo source of truth."
          runbook: "check Railway status; revisar logs audit_sync; verificar DATABASE_URL"

  - name: pje-download.worker
    interval: 30s
    rules:
      - alert: PjeWorkerDeadLetters
        expr: increase(pje_worker_dead_letters_total[5m]) > 0
        for: 0s
        labels:
          severity: warning
          app: pje-download
        annotations:
          summary: "Worker dead-lettered {{ $value }} payloads (reason: {{ $labels.reason }})"
          description: "Redis queue recebeu payload malformado. Investigar produtor."
          runbook: "redis-cli LRANGE pje:dead_letter 0 -1"

  - name: pje-download.health
    interval: 30s
    rules:
      - alert: PjeDashboardDown
        expr: up{job="pje-dashboard"} == 0
        for: 2m
        labels:
          severity: critical
          app: pje-download
        annotations:
          summary: "pje-dashboard :8007 não responde ao scrape Prometheus"
          description: "Container down, Tailscale offline, ou aiohttp travado. /metrics e /healthz fora."
          runbook: "ssh pje-vps; docker ps; docker logs pje-dashboard --tail 100"

      - alert: PjeWorkerCircuitBreakerOpen
        expr: probe_http_status_code{job="pje-worker-probe"} == 503
        for: 1m
        labels:
          severity: critical
          app: pje-download
        annotations:
          summary: "pje-worker :8006/health retorna 503 (circuit breaker aberto)"
          description: "Sprint 11 blpop circuit breaker: 20 falhas consecutivas de BLPOP marcaram _health_status=redis_unreachable. Worker não processa jobs."
          runbook: "docker logs pje-redis; docker restart pje-redis; docker logs pje-worker --tail 100"
```

- [ ] **Step 2: Validate with verify.sh**

Run: `./ops/monitoring/verify.sh`

Expected: includes line `checking ops/monitoring/pje/alert-rules.yml` with no error. promtool prints a count of groups/rules; last line is `✓ verify.sh: all present artifacts valid`.

If promtool reports a syntax error, read the error message — it names the exact YAML line and the expected vs actual field. Most common mistake: indentation or missing colon.

- [ ] **Step 3: Commit**

```bash
git add ops/monitoring/pje/alert-rules.yml
git commit -m "ops(monitoring): add pje-download alert rules (5 rules)

4 backlog alerts + 1 split for PjeDashboardDown (up==0) vs
PjeWorkerCircuitBreakerOpen (probe_http_status_code==503 on :8006/health).
Spec §7. promtool check rules PASS."
```

---

## Task 4: Write `ops/monitoring/pje/dashboard.json` (Grafana v11 dashboard, 8 panels)

**Files:**
- Create: `ops/monitoring/pje/dashboard.json`

**Rationale:** This is the largest config artifact in the plan. The JSON schema is Grafana's standard dashboard format (v11). Key fields per panel: `id`, `title`, `type` (`timeseries`, `stat`, `heatmap`), `targets` (array of `{expr, legendFormat, refId}`), `fieldConfig.defaults.unit`, `gridPos` (x/y/w/h on a 24-col grid).

Because the JSON is verbose, write it in one go and validate with `jq`. Do NOT attempt to hand-craft the JSON visually in Grafana and export — that introduces Grafana-version-specific fields the reviewer will flag as churn.

- [ ] **Step 1: Create the file**

Create `ops/monitoring/pje/dashboard.json` with the full dashboard JSON below. Refresh: 30s, timezone browser, default time range last 1h.

```json
{
  "annotations": {"list": []},
  "description": "pje-download operational observability — 8 panels covering MNI SOAP, GDrive, batch, worker, and Railway audit sync. Spec: 2026-04-18-grafana-dashboard-design.md",
  "editable": true,
  "fiscalYearStartMonth": 0,
  "graphTooltip": 1,
  "id": null,
  "links": [],
  "liveNow": false,
  "panels": [
    {
      "id": 100,
      "type": "stat",
      "title": "Scrape health (dashboard)",
      "gridPos": {"x": 0, "y": 0, "w": 4, "h": 3},
      "targets": [{"expr": "up{job=\"pje-dashboard\"}", "refId": "A"}],
      "fieldConfig": {"defaults": {"mappings": [{"type": "value", "options": {"0": {"text": "DOWN", "color": "red"}, "1": {"text": "UP", "color": "green"}}}]}}
    },
    {
      "id": 101,
      "type": "stat",
      "title": "Scrape health (worker)",
      "gridPos": {"x": 4, "y": 0, "w": 4, "h": 3},
      "targets": [{"expr": "up{job=\"pje-worker\"}", "refId": "A"}],
      "fieldConfig": {"defaults": {"mappings": [{"type": "value", "options": {"0": {"text": "DOWN", "color": "red"}, "1": {"text": "UP", "color": "green"}}}]}}
    },
    {
      "id": 102,
      "type": "stat",
      "title": "Active batches",
      "gridPos": {"x": 8, "y": 0, "w": 4, "h": 3},
      "targets": [{"expr": "pje_dashboard_active_batches", "refId": "A"}]
    },
    {
      "id": 103,
      "type": "stat",
      "title": "Audit sync lag (s)",
      "gridPos": {"x": 12, "y": 0, "w": 12, "h": 3},
      "targets": [{"expr": "pje_audit_sync_lag_seconds", "refId": "A"}],
      "fieldConfig": {"defaults": {"unit": "s", "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": null}, {"color": "yellow", "value": 30}, {"color": "red", "value": 60}]}}}
    },
    {
      "id": 1,
      "type": "timeseries",
      "title": "1. Audit sync lag",
      "gridPos": {"x": 0, "y": 3, "w": 8, "h": 7},
      "targets": [{"expr": "pje_audit_sync_lag_seconds", "refId": "A", "legendFormat": "lag"}],
      "fieldConfig": {"defaults": {"unit": "s", "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": null}, {"color": "yellow", "value": 30}, {"color": "red", "value": 60}]}}}
    },
    {
      "id": 2,
      "type": "timeseries",
      "title": "2. Sync batches by status",
      "gridPos": {"x": 8, "y": 3, "w": 8, "h": 7},
      "targets": [{"expr": "rate(pje_audit_sync_batches_total[5m])", "refId": "A", "legendFormat": "{{status}}"}],
      "fieldConfig": {"defaults": {"custom": {"stacking": {"mode": "normal"}}}}
    },
    {
      "id": 3,
      "type": "heatmap",
      "title": "3. Sync tick latency (heatmap)",
      "gridPos": {"x": 16, "y": 3, "w": 8, "h": 7},
      "targets": [{"expr": "sum(rate(pje_audit_sync_latency_seconds_bucket[5m])) by (le)", "refId": "A", "format": "heatmap", "legendFormat": "{{le}}"}]
    },
    {
      "id": 4,
      "type": "timeseries",
      "title": "4. Worker dead letters by reason",
      "gridPos": {"x": 0, "y": 10, "w": 12, "h": 7},
      "targets": [{"expr": "increase(pje_worker_dead_letters_total[1h])", "refId": "A", "legendFormat": "{{reason}}"}]
    },
    {
      "id": 5,
      "type": "timeseries",
      "title": "5. Publish failures / timeouts / recoveries",
      "gridPos": {"x": 12, "y": 10, "w": 12, "h": 7},
      "targets": [
        {"expr": "rate(pje_worker_publish_failures_total[5m])", "refId": "A", "legendFormat": "publish_failures (worker) {{kind}}"},
        {"expr": "rate(pje_dashboard_batch_timeouts_total[5m])", "refId": "B", "legendFormat": "batch_timeouts (dashboard)"},
        {"expr": "increase(pje_dashboard_active_batch_recoveries_total[1h])", "refId": "C", "legendFormat": "active_batch_recoveries (dashboard) [1h]"}
      ]
    },
    {
      "id": 6,
      "type": "timeseries",
      "title": "6. MNI latency p50/p95/p99 (by operation)",
      "gridPos": {"x": 0, "y": 17, "w": 12, "h": 7},
      "targets": [
        {"expr": "histogram_quantile(0.50, sum(rate(pje_mni_latency_seconds_bucket[5m])) by (le, operation))", "refId": "A", "legendFormat": "p50 {{operation}}"},
        {"expr": "histogram_quantile(0.95, sum(rate(pje_mni_latency_seconds_bucket[5m])) by (le, operation))", "refId": "B", "legendFormat": "p95 {{operation}}"},
        {"expr": "histogram_quantile(0.99, sum(rate(pje_mni_latency_seconds_bucket[5m])) by (le, operation))", "refId": "C", "legendFormat": "p99 {{operation}}"}
      ],
      "fieldConfig": {"defaults": {"unit": "s"}}
    },
    {
      "id": 7,
      "type": "timeseries",
      "title": "7. MNI request outcomes",
      "gridPos": {"x": 12, "y": 17, "w": 12, "h": 7},
      "targets": [{"expr": "rate(pje_mni_requests_total[5m])", "refId": "A", "legendFormat": "{{operation}} / {{status}}"}],
      "fieldConfig": {"defaults": {"custom": {"stacking": {"mode": "normal"}}}}
    },
    {
      "id": 8,
      "type": "timeseries",
      "title": "8. GDrive strategy success rate",
      "gridPos": {"x": 0, "y": 24, "w": 24, "h": 7},
      "targets": [{"expr": "sum(rate(pje_gdrive_attempts_total{status=\"success\"}[5m])) by (strategy) / clamp_min(sum(rate(pje_gdrive_attempts_total[5m])) by (strategy), 1e-9)", "refId": "A", "legendFormat": "{{strategy}} success rate"}],
      "fieldConfig": {"defaults": {"unit": "percentunit", "custom": {"stacking": {"mode": "none"}}, "min": 0, "max": 1}}
    }
  ],
  "refresh": "30s",
  "schemaVersion": 39,
  "tags": ["pje-download", "observability", "backlog-p04"],
  "templating": {"list": []},
  "time": {"from": "now-1h", "to": "now"},
  "timepicker": {},
  "timezone": "browser",
  "title": "pje-download — operational",
  "uid": "pje-download-ops",
  "version": 1,
  "weekStart": ""
}
```

**Design notes:**
- Panel 8 uses `clamp_min(..., 1e-9)` in the denominator to avoid division-by-zero when a strategy has no attempts in the window (returns effectively 0 instead of NaN).
- Stat panel 103 duplicates the lag metric in the header row for at-a-glance. The timeseries panel 1 gives the history.
- `schemaVersion: 39` targets Grafana 11.x. Do not change to a higher version without validating in the target Grafana.

- [ ] **Step 2: Validate**

Run: `./ops/monitoring/verify.sh`

Expected: includes `checking ops/monitoring/pje/dashboard.json (jq parse)` with no error. If jq fails, the error line number is usually accurate — check for trailing commas or unescaped quotes.

- [ ] **Step 3: Commit**

```bash
git add ops/monitoring/pje/dashboard.json
git commit -m "ops(monitoring): add pje-download Grafana dashboard (8 panels)

4 stat panels (scrape health x2, active batches, audit lag) on top row,
then 8 panels grouped: audit sync (3), worker (2), MNI SOAP (2), GDrive (1).
All PromQL references metrics already instrumented in metrics.py.
Panel 6 uses histogram_quantile(sum(rate()) by (le, operation)) per
spec §6. Panel 8 uses clamp_min to prevent NaN on zero-traffic strategies.
Spec §6. jq parse PASS."
```

---

## Task 5: Write `ops/monitoring/stack/prometheus.yml`

**Files:**
- Create: `ops/monitoring/stack/prometheus.yml`

- [ ] **Step 1: Write the file**

Create `ops/monitoring/stack/prometheus.yml`:

```yaml
# Prometheus 2.55 config — monitoring stack for pje-download (and future apps).
# Spec: docs/superpowers/specs/2026-04-18-grafana-dashboard-design.md §3

global:
  scrape_interval: 30s
  evaluation_interval: 30s
  external_labels:
    monitor: openclaw-ops

# Alert rule files mounted from per-app directories (bind-mount in compose).
rule_files:
  - /etc/prometheus/rules/pje/*.yml

alerting:
  alertmanagers:
    - static_configs:
        - targets:
            - alertmanager:9093

scrape_configs:
  # ── pje-download DASHBOARD process (port 8007) ─────────────────────────
  - job_name: pje-dashboard
    metrics_path: /metrics
    scheme: http
    static_configs:
      - targets: ["${PJE_TAILNET_IP}:8007"]
        labels:
          app: pje-download
          process: dashboard

  # ── pje-download WORKER process (port 8006) ───────────────────────────
  - job_name: pje-worker
    metrics_path: /metrics
    scheme: http
    static_configs:
      - targets: ["${PJE_TAILNET_IP}:8006"]
        labels:
          app: pje-download
          process: worker

  # ── Blackbox probe: dashboard /healthz ────────────────────────────────
  - job_name: pje-dashboard-probe
    metrics_path: /probe
    params:
      module: [http_2xx_or_503]
    static_configs:
      - targets: ["http://${PJE_TAILNET_IP}:8007/healthz"]
        labels:
          app: pje-download
          process: dashboard
    relabel_configs:
      - source_labels: [__address__]
        target_label: __param_target
      - source_labels: [__param_target]
        target_label: instance
      - target_label: __address__
        replacement: blackbox:9115

  # ── Blackbox probe: worker /health (circuit breaker visibility) ───────
  - job_name: pje-worker-probe
    metrics_path: /probe
    params:
      module: [http_2xx_or_503]
    static_configs:
      - targets: ["http://${PJE_TAILNET_IP}:8006/health"]
        labels:
          app: pje-download
          process: worker
    relabel_configs:
      - source_labels: [__address__]
        target_label: __param_target
      - source_labels: [__param_target]
        target_label: instance
      - target_label: __address__
        replacement: blackbox:9115

  # ── Self-scrape (Prometheus monitoring itself) ────────────────────────
  - job_name: prometheus
    static_configs:
      - targets: ["localhost:9090"]
```

**Note on `${PJE_TAILNET_IP}`:** Prometheus does not expand env vars in this config natively (it reads the file as-is). The substitution happens at docker-compose level via the image entrypoint's env-var support, OR — simpler and what we use — at deploy time via `envsubst` run once in `DEPLOY.md`. The operator fills `PJE_TAILNET_IP=100.x.x.x` in an openclaw-side `.env` and regenerates the config. An alternative (file_sd_configs + tailscale serve) is over-engineering at current scale.

- [ ] **Step 2: Validate**

Run: `./ops/monitoring/verify.sh`

Expected: `checking ops/monitoring/stack/prometheus.yml (with dummy envsubst)` passes. promtool validates `static_configs.targets` as host:port, so the Task-2 verify.sh pipes the file through `PJE_TAILNET_IP=127.0.0.1 envsubst '${PJE_TAILNET_IP}'` first. This catches real syntax errors without requiring Prometheus to actually reach the tailnet address.

If promtool errors on `rule_files`, it is because the path (`/etc/prometheus/rules/pje/*.yml`) does not exist at validation time on the dev host. Prometheus only enforces that path at load time inside the container where the bind-mount lives. Most promtool versions warn but do not fail on a missing glob — if your version fails, temporarily comment out the `rule_files:` block during verify and restore it before commit.

- [ ] **Step 3: Commit**

```bash
git add ops/monitoring/stack/prometheus.yml
git commit -m "ops(monitoring): prometheus.yml with 4 scrape jobs + rule loading

pje-dashboard + pje-worker scrape /metrics on :8007 and :8006.
pje-dashboard-probe + pje-worker-probe use blackbox module
'http_2xx_or_503' (defined in blackbox.yml, next task) so 503
from circuit breaker surfaces as probe_http_status_code=503 with
probe_success=1. ${PJE_TAILNET_IP} resolved at deploy via envsubst.
Rules loaded from bind-mount /etc/prometheus/rules/pje/. Spec §3."
```

---

## Task 6: Write `ops/monitoring/stack/alertmanager.yml`

**Files:**
- Create: `ops/monitoring/stack/alertmanager.yml`

**Rationale:** Alertmanager 0.27 has a native `telegram_configs` receiver (since 0.26) — we use it instead of a generic webhook. Config needs: bot token, chat ID, routing tree.

- [ ] **Step 1: Write the file**

Create `ops/monitoring/stack/alertmanager.yml`:

```yaml
# Alertmanager 0.27 config — routes alerts to Telegram @kaiOpsBot.
# Spec: docs/superpowers/specs/2026-04-18-grafana-dashboard-design.md §3
#
# ${BOT_TOKEN} and ${CHAT_ID} filled by envsubst at deploy time
# (values live in openclaw's .env, never committed).

global:
  resolve_timeout: 5m

route:
  receiver: telegram-kaiops
  group_by: [app]
  group_wait: 10s
  group_interval: 5m
  repeat_interval: 4h
  routes:
    - matchers:
        - severity="critical"
      receiver: telegram-kaiops
      group_wait: 0s    # critical pages fire immediately without grouping delay

receivers:
  - name: telegram-kaiops
    telegram_configs:
      - bot_token: "${BOT_TOKEN}"
        chat_id: ${CHAT_ID}              # integer, NO quotes
        parse_mode: HTML
        send_resolved: true
        message: |
          <b>[{{ .Status | toUpper }}] {{ .CommonLabels.alertname }}</b>
          <i>app:</i> {{ .CommonLabels.app }}
          <i>severity:</i> {{ .CommonLabels.severity }}
          {{ range .Alerts -}}
          • {{ .Annotations.summary }}
            <code>{{ .Annotations.runbook }}</code>
          {{ end }}

inhibit_rules:
  # When the dashboard is fully down, suppress lag/batch alerts
  # (they are noisy symptoms of the same root cause).
  - source_matchers: [alertname="PjeDashboardDown"]
    target_matchers: [app="pje-download"]
    equal: [app]
```

**Important on `chat_id`:** YAML without quotes → integer type. Alertmanager 0.27 requires `chat_id` to be an integer, not a string. Getting this wrong causes a silent validation pass but no messages delivered. If `envsubst` leaves `${CHAT_ID}` as a literal string (not substituted), amtool check-config will error because the unquoted literal is not a valid YAML integer — this is the intended behavior (fail loud if deploy forgot to substitute).

- [ ] **Step 2: Validate**

Run: `./ops/monitoring/verify.sh`

Expected: `checking ops/monitoring/stack/alertmanager.yml (with dummy envsubst)` passes. The Task-2 `verify.sh` already includes the `BOT_TOKEN=dummy CHAT_ID=0 envsubst '${BOT_TOKEN} ${CHAT_ID}'` preprocessing step (amtool rejects the raw `${CHAT_ID}` placeholder as a non-integer), so no edit to verify.sh is needed here — amtool receives a pre-substituted stream and validates it.

If amtool errors with a message mentioning `chat_id` and "cannot unmarshal" or "integer", the dummy-envsubst step in verify.sh is missing — go back to Task 2 and confirm the alertmanager.yml check block uses the pipe form. If amtool errors on something else, the issue is real YAML syntax — fix the committed file.

- [ ] **Step 3: Commit**

```bash
git add ops/monitoring/stack/alertmanager.yml
git commit -m "ops(monitoring): alertmanager.yml with native telegram_configs

Routes all alerts to @kaiOpsBot (spec §3). group_by=[app] coalesces
cascading failures into single messages (Railway down fires both
lag and batches-failing → one Telegram message).

critical severity overrides group_wait to 0s for immediate paging.

inhibit_rule: PjeDashboardDown suppresses lag/batch alerts from
same app (symptom suppression).

Validator (Task 2 verify.sh) already handles the ${CHAT_ID} / ${BOT_TOKEN}
integer-type gotcha via envsubst preprocessing — no verify.sh edit needed."
```

---

## Task 7: Write `ops/monitoring/stack/blackbox.yml`

**Files:**
- Create: `ops/monitoring/stack/blackbox.yml`

**Rationale:** Default `http_2xx` module marks `probe_success=0` on non-2xx. We want 503 (circuit breaker) to preserve `probe_success=1` so we don't get a probe-failure cascade alongside the intentional `PjeWorkerCircuitBreakerOpen`. Custom module name: `http_2xx_or_503` (matches Task 5's scrape params).

- [ ] **Step 1: Write the file**

Create `ops/monitoring/stack/blackbox.yml`:

```yaml
# blackbox_exporter 0.25 modules.
# Spec: docs/superpowers/specs/2026-04-18-grafana-dashboard-design.md §12

modules:
  # Standard 2xx+503 — 503 is an expected signal from /health when the
  # Sprint-11 circuit breaker opens. Keeping probe_success=1 on 503
  # prevents a spurious "probe failed" alert from firing alongside
  # PjeWorkerCircuitBreakerOpen (same root cause, double page).
  http_2xx_or_503:
    prober: http
    timeout: 5s
    http:
      valid_http_versions: ["HTTP/1.1", "HTTP/2.0"]
      valid_status_codes: [200, 503]
      method: GET
      preferred_ip_protocol: ip4
      follow_redirects: false
      fail_if_ssl: false
      fail_if_not_ssl: false
```

- [ ] **Step 2: Validate**

blackbox_exporter does not ship its own config validator; the only way to catch syntax errors is `docker run --rm -v $PWD:/work prom/blackbox-exporter:v0.25.0 --config.check --config.file=/work/ops/monitoring/stack/blackbox.yml` (returns 0 on success, logs error on failure).

Add this to `verify.sh` after the amtool block:

```bash
# ── 6. blackbox_exporter modules ──────────────────────────────────────
if [ -f ops/monitoring/stack/blackbox.yml ]; then
    echo "  checking ops/monitoring/stack/blackbox.yml"
    docker run --rm -v "$PWD/ops/monitoring/stack:/cfg" prom/blackbox-exporter:v0.25.0 \
        --config.check --config.file=/cfg/blackbox.yml
fi
```

Run: `./ops/monitoring/verify.sh`

Expected: blackbox check passes silently (`--config.check` exits 0).

- [ ] **Step 3: Commit**

```bash
git add ops/monitoring/stack/blackbox.yml ops/monitoring/verify.sh
git commit -m "ops(monitoring): blackbox.yml with http_2xx_or_503 module

Custom module treats 200 and 503 as valid (probe_success=1).
503 is the intentional signal for Sprint-11 circuit breaker on
:8006/health — keeping probe_success=1 prevents cascade with
PjeWorkerCircuitBreakerOpen alert. Spec §12.

verify.sh: add blackbox --config.check invocation."
```

---

## Task 8: Grafana provisioning files (datasource + dashboard provider)

**Files:**
- Create: `ops/monitoring/stack/grafana/provisioning/datasources/prometheus.yml`
- Create: `ops/monitoring/stack/grafana/provisioning/dashboards/default.yml`

### Task 8a — Datasource

- [ ] **Step 1: Write datasource provider**

Create `ops/monitoring/stack/grafana/provisioning/datasources/prometheus.yml`:

```yaml
# Grafana 11 datasource provider — single Prometheus instance from the stack.
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    uid: prometheus-default
    access: proxy
    url: http://prometheus:9090
    isDefault: true
    editable: false
    jsonData:
      timeInterval: "30s"
      httpMethod: POST
```

- [ ] **Step 2: Commit (no validator for provisioning YAML; Grafana validates at startup)**

```bash
git add ops/monitoring/stack/grafana/provisioning/datasources/prometheus.yml
git commit -m "ops(monitoring): grafana datasource provider (Prometheus, default)"
```

### Task 8b — Dashboard provider

- [ ] **Step 1: Write dashboard provider**

Create `ops/monitoring/stack/grafana/provisioning/dashboards/default.yml`:

```yaml
# Grafana 11 dashboard provider — reads all *.json from the mounted apps/ tree.
apiVersion: 1
providers:
  - name: pje-download
    orgId: 1
    folder: pje-download
    type: file
    disableDeletion: false
    updateIntervalSeconds: 30
    allowUiUpdates: false
    options:
      # Bind-mount source is ops/monitoring/pje/ in the repo → /var/lib/grafana/dashboards/pje/ in container
      path: /var/lib/grafana/dashboards/pje
      foldersFromFilesStructure: true
```

- [ ] **Step 2: Commit**

```bash
git add ops/monitoring/stack/grafana/provisioning/dashboards/default.yml
git commit -m "ops(monitoring): grafana dashboard provider (file-based, pje/)"
```

---

## Task 9: `ops/monitoring/stack/docker-compose.yml`

**Files:**
- Create: `ops/monitoring/stack/docker-compose.yml`

**Rationale:** Ties all stack components together. Four services: prometheus, grafana, alertmanager, blackbox. Grafana bound to `127.0.0.1:3000` only (SSH tunnel access only, per spec §10 step 10). Prometheus and Alertmanager exposed only on the internal docker network.

- [ ] **Step 1: Write the compose file**

Create `ops/monitoring/stack/docker-compose.yml`:

```yaml
# ops/monitoring/stack/docker-compose.yml
# Monitoring stack for openclaw VPS. Deploy: `docker compose up -d` (from this directory).
#
# Spec: docs/superpowers/specs/2026-04-18-grafana-dashboard-design.md §3
# Deploy runbook: DEPLOY.md
#
# Requires pre-deploy envsubst to resolve ${PJE_TAILNET_IP}, ${BOT_TOKEN}, ${CHAT_ID}
# in prometheus.yml and alertmanager.yml. See DEPLOY.md step 5.

name: openclaw-monitoring

services:
  # ────────────────────────────────
  # PROMETHEUS (TSDB + scraper + alert evaluator)
  # ────────────────────────────────
  prometheus:
    image: prom/prometheus:v2.55.0
    container_name: openclaw-prometheus
    restart: unless-stopped
    mem_limit: 512m
    command:
      - "--config.file=/etc/prometheus/prometheus.yml"
      - "--storage.tsdb.path=/prometheus"
      - "--storage.tsdb.retention.time=15d"
      - "--storage.tsdb.retention.size=10GB"
      - "--web.enable-lifecycle"
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - ../pje:/etc/prometheus/rules/pje:ro   # per-app rules bind-mount
      - prometheus_data:/prometheus
    extra_hosts:
      - "host.docker.internal:host-gateway"   # Linux local-dev scrape (§9 step 3)
    networks:
      - ops-net

  # ────────────────────────────────
  # ALERTMANAGER
  # ────────────────────────────────
  alertmanager:
    image: prom/alertmanager:v0.27.0
    container_name: openclaw-alertmanager
    restart: unless-stopped
    mem_limit: 128m
    command:
      - "--config.file=/etc/alertmanager/alertmanager.yml"
      - "--storage.path=/alertmanager"
    volumes:
      - ./alertmanager.yml:/etc/alertmanager/alertmanager.yml:ro
      - alertmanager_data:/alertmanager
    networks:
      - ops-net

  # ────────────────────────────────
  # BLACKBOX EXPORTER (HTTP probes for /healthz + /health)
  # ────────────────────────────────
  blackbox:
    image: prom/blackbox-exporter:v0.25.0
    container_name: openclaw-blackbox
    restart: unless-stopped
    mem_limit: 64m
    command:
      - "--config.file=/config/blackbox.yml"
    volumes:
      - ./blackbox.yml:/config/blackbox.yml:ro
    networks:
      - ops-net

  # ────────────────────────────────
  # GRAFANA (dashboards + datasource)
  # ────────────────────────────────
  grafana:
    image: grafana/grafana:11.3.0
    container_name: openclaw-grafana
    restart: unless-stopped
    mem_limit: 256m
    environment:
      GF_SECURITY_ADMIN_USER: admin
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD:-admin}
      GF_USERS_ALLOW_SIGN_UP: "false"
      GF_AUTH_ANONYMOUS_ENABLED: "false"
      GF_SERVER_ROOT_URL: "http://localhost:3000/"
    ports:
      - "127.0.0.1:3000:3000"    # LOOPBACK ONLY — access via SSH tunnel
    volumes:
      - ./grafana/provisioning:/etc/grafana/provisioning:ro
      - ../pje:/var/lib/grafana/dashboards/pje:ro  # dashboard JSON bind-mount
      - grafana_data:/var/lib/grafana
    depends_on:
      - prometheus
    networks:
      - ops-net

networks:
  ops-net:
    name: ops-net

volumes:
  prometheus_data:
  alertmanager_data:
  grafana_data:
```

- [ ] **Step 2: Validate**

Run: `./ops/monitoring/verify.sh`

Expected: `checking ops/monitoring/stack/docker-compose.yml (compose config)` passes. `docker compose config -q` parses the file and resolves interpolations.

- [ ] **Step 3: Commit**

```bash
git add ops/monitoring/stack/docker-compose.yml
git commit -m "ops(monitoring): docker-compose.yml ties stack together

4 services: prometheus (2.55), alertmanager (0.27), blackbox (0.25),
grafana (11.3). All pinned, all restart unless-stopped, all memory-
limited. Only Grafana exposed, and only on 127.0.0.1:3000 (SSH
tunnel access — spec §10 step 10). Prometheus/Alertmanager/blackbox
reachable only via internal ops-net.

extra_hosts host-gateway on prometheus/blackbox enables local-dev
smoke test on Linux (spec §9 step 3).

TSDB retention: 15 days OR 10GB cap (whichever hit first). Grafana
admin password via GRAFANA_ADMIN_PASSWORD env var (default: admin,
forced-change prompted on first login)."
```

---

## Task 10: End-to-end local smoke test

**Files:** no new files. Verification task.

**Rationale:** Before shipping `DEPLOY.md` we validate the stack actually works end-to-end. This catches gotchas that static validation cannot — missing bind mounts, wrong volume paths, container restart loops.

- [ ] **Step 1: Rebuild both images so the Task 1 code change is picked up**

From repo root:
```bash
docker compose --profile worker build worker dashboard
docker compose --profile worker up -d
```

The `build` call is required because Task 1 modified `worker.py`; if the worker container was already running from a pre-Task-1 image, `up -d` alone would not pick up the new `/metrics` route and Step 2 below would 404.

Wait 15 s. Verify both containers healthy: `docker ps --format 'table {{.Names}}\t{{.Status}}' | grep -E 'pje-dashboard|pje-worker'`. Expected two rows with `(healthy)`.

Sanity-check endpoints respond locally:
```bash
curl -sf http://localhost:8007/metrics | head -5
curl -sf http://localhost:8007/healthz | jq .ready
curl -sf http://localhost:8006/metrics | head -5
curl -sf http://localhost:8006/health  | jq .
```

All four should return data. If `:8006/metrics` returns 404, the rebuild did not include the Task-1 change — re-run `docker compose build --no-cache worker` and retry.

- [ ] **Step 2: Prepare local-dev variants of prometheus.yml and alertmanager.yml via a `docker-compose.override.yml` (NOT by editing the tracked compose file)**

Reason for the override pattern: editing the tracked `docker-compose.yml` and reverting later is fragile — if anyone forgets the revert, a broken local path ends up committed. Docker Compose **automatically merges** any `docker-compose.override.yml` sitting next to `docker-compose.yml`, so we use that (git-ignored) file to swap bind-mounts without touching the tracked one.

From `ops/monitoring/stack/`:
```bash
# Substitute the env vars into temp files (/tmp — outside the repo tree).
PJE_TAILNET_IP=host.docker.internal envsubst '${PJE_TAILNET_IP}' \
    < prometheus.yml > /tmp/prometheus.local.yml

BOT_TOKEN=dummy CHAT_ID=0 envsubst '${BOT_TOKEN} ${CHAT_ID}' \
    < alertmanager.yml > /tmp/alertmanager.local.yml

# Create the override file (auto-picked-up by docker compose, never committed).
cat > docker-compose.override.yml <<'EOF'
services:
  prometheus:
    volumes:
      - /tmp/prometheus.local.yml:/etc/prometheus/prometheus.yml:ro
  alertmanager:
    volumes:
      - /tmp/alertmanager.local.yml:/etc/alertmanager/alertmanager.yml:ro
EOF
```

Verify it's out of git: `git status` should **not** list `docker-compose.override.yml`. If it does, add an entry to the repo root `.gitignore`:
```bash
echo "ops/monitoring/stack/docker-compose.override.yml" >> .gitignore
```
(This .gitignore entry can stay committed — it's idiomatic.)

- [ ] **Step 3: Start the monitoring stack**

From `ops/monitoring/stack/`:
```bash
docker compose up -d
```

Expected: all 4 services `Up`. `docker compose ps` shows them healthy-ish (blackbox has no healthcheck, others start fast).

- [ ] **Step 4: Verify Prometheus sees all 4 targets**

Open `http://localhost:9090/targets` (or curl):
```bash
curl -sf http://localhost:9090/api/v1/targets | jq '.data.activeTargets[] | {job: .labels.job, health: .health}'
```

Expected: 5 entries (4 pje jobs + self-scrape `prometheus`), all `"health": "up"`. Wait up to 60s for first scrape if any are `"unknown"`.

If a probe target is "down", the error message (`.lastError`) will name the cause — most likely `host.docker.internal` not resolving (on Linux without the `extra_hosts` → `host-gateway` trick; Task 9 already includes it).

- [ ] **Step 5: Fire a synthetic alert and verify routing**

```bash
docker exec openclaw-alertmanager amtool alert add \
    alertname=SmokeTest app=pje-download severity=critical \
    --annotation=summary="e2e smoke" \
    --annotation=runbook="none"
```

Check Alertmanager receives it:
```bash
curl -sf http://localhost:9093/api/v2/alerts | jq '.[] | {alertname: .labels.alertname, state: .status.state}'
```

Expected: one entry `{"alertname": "SmokeTest", "state": "active"}`.

**Note:** with `CHAT_ID=0 BOT_TOKEN=dummy` the Telegram delivery will 401 — that is expected locally. The deliverable of this step is "alert reaches Alertmanager and Alertmanager attempts to forward", confirmed via `docker logs openclaw-alertmanager | grep -i telegram` showing an auth error (not a config error).

- [ ] **Step 6: Open Grafana, verify dashboard auto-provisioned**

SSH-tunnel or open directly (if on dev machine): `http://localhost:3000` → admin/admin → change password → navigate to "pje-download" folder → open "pje-download — operational" dashboard.

Expected: all 8 panels render. Stat panels on top show live values (dashboard UP/UP/0/0s or similar). Rate panels may be empty if there has been no traffic — this is correct and not a failure.

If the dashboard is missing, check `docker logs openclaw-grafana | grep provisioning` — most likely the bind-mount path does not resolve (check that `../pje` from the stack directory resolves to the expected `ops/monitoring/pje/`).

- [ ] **Step 7: Tear down**

```bash
cd ops/monitoring/stack
docker compose down -v                          # removes volumes; fresh TSDB next time
rm /tmp/prometheus.local.yml /tmp/alertmanager.local.yml
rm docker-compose.override.yml                  # drop the local override
```

Then confirm no tracked-file edits leaked:
```bash
git diff --stat ops/monitoring/stack/docker-compose.yml
# expected: no output
git status --short ops/monitoring/stack/
# expected: no output (override.yml should be gitignored, nothing else modified)
```

If either command shows output, revert before the Step 9 empty commit.

- [ ] **Step 8: Run verify.sh one more time to confirm committed state is clean**

Run: `./ops/monitoring/verify.sh`

Expected: full pass, all 6 check lines printed, final `✓`.

- [ ] **Step 9: Commit (empty — records the smoke-test gate)**

No files to add. Create an empty commit documenting the gate passed:

```bash
git commit --allow-empty -m "chore(monitoring): e2e local smoke test passed

- docker compose up -d (both pje-download and monitoring stack)
- 4 scrape targets UP (pje-dashboard, pje-worker, *-probe)
- Synthetic alert posted via amtool → reached Alertmanager → forwarded (401 expected, dummy token)
- Grafana dashboard 'pje-download — operational' auto-provisioned, 8 panels render

Ready for DEPLOY.md hand-off to openclaw."
```

---

## Task 11: Write `ops/monitoring/stack/DEPLOY.md`

**Files:**
- Create: `ops/monitoring/stack/DEPLOY.md`

- [ ] **Step 1: Write the runbook**

Create `ops/monitoring/stack/DEPLOY.md`:

````markdown
# Deploy Runbook — openclaw Monitoring Stack

**Target host:** openclaw VPS (`ssh openclaw-vps` — alias in `~/.ssh/config`).
**Target path:** `/opt/monitoring/apps/pje-download/`
**Prerequisite:** both VPS (openclaw + pje) already running. pje-download built with the `worker /metrics` change (Task 1).

Estimated time: 20–30 minutes on a fresh openclaw install.

---

## 1. Install Tailscale on both VPS

**Openclaw:**
```bash
ssh openclaw-vps
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
# Record the 100.x.x.x address
tailscale ip -4
```

**pje VPS:**
```bash
ssh <pje-vps-alias>
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
tailscale ip -4
# Record as PJE_TAILNET_IP
```

Verify cross-host reachability from openclaw:
```bash
tailscale ping <PJE_TAILNET_IP>     # should show direct or relayed route
curl -sf http://<PJE_TAILNET_IP>:8007/healthz | jq .service
# expected: "pje-dashboard"
```

## 2. Create `@kaiOpsBot` on Telegram

1. Open Telegram, search `@BotFather`.
2. `/newbot` → name: `Kai Ops Bot`, username: `kaiOpsBot` (or variant — must end in `bot`).
3. Record the **bot token** (format `NNNNN:AAAA...`).
4. Start a DM with the new bot (click its profile → Start). Send `/start`.
5. Get your chat ID:
   - DM `@userinfobot` — it replies with your numeric user ID. That is your `CHAT_ID` for personal alerts.
   - For a group channel, add the bot as admin and use `@RawDataBot` to reveal the channel's negative ID (e.g., `-1001234567890`).

## 3. Clone the repo on openclaw

```bash
ssh openclaw-vps
sudo mkdir -p /opt/monitoring/apps
sudo chown $USER /opt/monitoring/apps
cd /opt/monitoring/apps
git clone https://github.com/fbmoulin/pje-download pje-download
cd pje-download/ops/monitoring/stack
```

## 4. Create `.env` on openclaw (never committed)

```bash
cat > .env <<EOF
PJE_TAILNET_IP=100.x.x.x
BOT_TOKEN=NNNNN:AAAA...
CHAT_ID=123456789
GRAFANA_ADMIN_PASSWORD=<strong-password>
EOF
chmod 600 .env
```

## 5. Substitute env vars into config files

```bash
# prometheus.yml (in-place substitution)
envsubst '${PJE_TAILNET_IP}' < prometheus.yml > prometheus.yml.substituted
mv prometheus.yml.substituted prometheus.yml

# alertmanager.yml (in-place substitution)
envsubst '${BOT_TOKEN} ${CHAT_ID}' < alertmanager.yml > alertmanager.yml.substituted
mv alertmanager.yml.substituted alertmanager.yml
```

**Note:** this is a one-way substitution. If you later `git pull` and the upstream file changes, re-run this step. Alternative: keep the original in a separate file (`*.tpl`) and generate the final each deploy — future refinement, not needed today.

## 6. Bring the stack up

```bash
docker compose up -d
```

Verify:
```bash
docker compose ps
# All 4 services "Up". No restart loops.

curl -sf http://localhost:9090/api/v1/targets | jq '.data.activeTargets[] | .health' | sort | uniq -c
# Expected: 5 lines "up" (4 pje jobs + 1 self)
```

## 7. Configure UFW

Block external access to Prometheus/Alertmanager (internal only); Grafana is already bound to `127.0.0.1`.

```bash
sudo ufw status    # check current rules
# Prometheus/Alertmanager ports are not exposed in docker-compose.yml — already safe.
# Tailscale traffic goes over UDP 41641, already allowed by default Tailscale install.
```

## 8. Access Grafana via SSH tunnel

From Felipe's laptop:
```bash
ssh -L 3000:localhost:3000 openclaw-vps
# Keep this session open; Grafana now reachable at http://localhost:3000
```

Login: `admin` / `<GRAFANA_ADMIN_PASSWORD from .env>`. Change password on first login if different.

Navigate: Dashboards → Browse → `pje-download` folder → `pje-download — operational`. All 8 panels should render.

## 9. Fire a smoke test alert

```bash
ssh openclaw-vps docker exec openclaw-alertmanager amtool alert add \
    alertname=DeployTest app=pje-download severity=warning \
    --annotation=summary="deploy verification" \
    --annotation=runbook="none"
```

Within 30s a Telegram message should arrive at `@kaiOpsBot`. If not:
- Check `docker logs openclaw-alertmanager | tail -30` for auth errors (wrong bot token) or 400s (malformed chat_id — must be integer).
- Verify bot is not blocked by your Telegram account (test by sending `/start` to the bot again).

## 10. Verify the real alert path (Redis injection)

```bash
ssh openclaw-vps
# Trigger PjeWorkerDeadLetters by posting a garbage payload
# kratos:pje:jobs is the Redis queue name per worker.py:1587
docker exec <redis-container> redis-cli LPUSH kratos:pje:jobs '{"garbage":true}'
```

Wait up to 1 min (alert `for: 0s` + Alertmanager `group_wait: 0s` for warning). Telegram message should say `PjeWorkerDeadLetters | reason=invalid_json`.

## Rollback

```bash
ssh openclaw-vps
cd /opt/monitoring/apps/pje-download/ops/monitoring/stack
docker compose down -v    # drops TSDB and all container state
```

`pje-download` itself is unaffected — the monitoring stack is purely read-only over the tailnet.

## Updating (after future `git pull`)

```bash
ssh openclaw-vps
cd /opt/monitoring/apps/pje-download
git pull
cd ops/monitoring/stack
# Re-run step 5 substitution if config files changed upstream
# Then:
docker compose pull         # get any updated image versions
docker compose up -d        # restart with new configs/images
curl -XPOST http://localhost:9090/-/reload  # hot-reload prometheus without restart
```
````

- [ ] **Step 2: Commit**

```bash
git add ops/monitoring/stack/DEPLOY.md
git commit -m "ops(monitoring): DEPLOY.md — 10-step openclaw runbook

Covers Tailscale install, BotFather kaiOpsBot creation, repo clone,
.env bootstrap, envsubst substitution, stack up, UFW posture, SSH
tunnel access, smoke test, and real alert path (Redis injection).

Rollback: docker compose down -v (pje-download unaffected — monitoring
is read-only over tailnet).

Spec §10. Each step has expected output or verification curl."
```

---

## Task 12: Write `ops/monitoring/README.md`

**Files:**
- Create: `ops/monitoring/README.md`

- [ ] **Step 1: Write the README**

Create `ops/monitoring/README.md`:

```markdown
# ops/monitoring/

Observability artifacts for pje-download and, eventually, other apps
that Felipe runs (kratos-v5, kcp, pdf-graph).

## Structure

- `pje/` — per-app artifacts (dashboard.json, alert-rules.yml). **Co-located
  with the code that defines the metrics they query**, so a change in
  `metrics.py` and the dashboard panel update can ship in the same commit.
- `stack/` — deploy-once infra (docker-compose.yml, prometheus.yml,
  alertmanager.yml, blackbox.yml, grafana/). Runs on openclaw VPS.
- `verify.sh` — pre-commit static validator (promtool, amtool, jq, compose).

## Quick start

```bash
# Validate everything locally (uses docker fallback if promtool/amtool missing)
./ops/monitoring/verify.sh

# Full end-to-end on dev machine (spec §9 step 3)
docker compose --profile worker up -d     # pje-download
cd ops/monitoring/stack
PJE_TAILNET_IP=host.docker.internal envsubst < prometheus.yml > /tmp/prom.local.yml
# ... (see spec §9)
```

## Deploy

See `stack/DEPLOY.md`. Target host: openclaw VPS.

## Adding another app

When `kratos-v5` (or similar) needs to join this stack:

1. In that app's repo, create `ops/monitoring/<app>/{dashboard.json, alert-rules.yml}` following the pje structure.
2. On openclaw, `git clone` that repo to `/opt/monitoring/apps/<app>/`.
3. Edit `stack/prometheus.yml` (or a `stack/prometheus.d/<app>.yml` fragment — better hygiene): add scrape_configs pointing to the app's tailnet endpoints; add a new `rule_files:` entry or bind-mount pointing to the new app's alert-rules.yml.
4. `docker compose up -d` (recreates only changed services) OR `curl -XPOST localhost:9090/-/reload` for config hot-reload.
5. Import the app's dashboard via Grafana provisioning bind-mount (add a new volume to Grafana service: `../../<app>/:/var/lib/grafana/dashboards/<app>:ro`).

When the openclaw IaC repo exists (future), move `stack/` there and each
app keeps only its per-app `<app>/` directory. See spec §5 migration path.

## References

- Design spec: `docs/superpowers/specs/2026-04-18-grafana-dashboard-design.md`
- Metrics source: `metrics.py` (21 series)
- Backlog item closed: CLAUDE.md §"Backlog (não-código)" #2
```

- [ ] **Step 2: Commit**

```bash
git add ops/monitoring/README.md
git commit -m "ops(monitoring): README — structure, quick-start, add-another-app"
```

---

## Task 13: Update `CLAUDE.md`

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Mark backlog item #2 done**

Open `CLAUDE.md` and find the section `## Backlog (não-código)`:

```markdown
2. **Grafana dashboard** (fecha P0.4) — provisionar Grafana (reusar VPS openclaw se possível), consumir `/metrics` do `:8007`. Alertas:
   - `pje_audit_sync_lag_seconds_event_time > 60` → atraso de sync
   - `pje_audit_sync_batches_total{status="failed"}` → Railway caiu
   - `pje_worker_dead_letters_total > 0` → jobs malformados
   - Liveness probe via `/health` detecta circuit breaker (`_health_status=redis_unreachable`)
```

Replace the **entire** item #2 block (the 5 lines above) with:

```markdown
2. ~~**Grafana dashboard** (fecha P0.4)~~ — DONE 2026-04-18. Stack (Prometheus 2.55 + Grafana 11.3 + Alertmanager 0.27 + blackbox_exporter 0.25) provisionada no openclaw VPS via `ops/monitoring/stack/` (docker-compose). Scrape cross-host via Tailscale. 4 scrape jobs + 5 alert rules + 8 panels. Telegram `@kaiOpsBot` dedicado. Spec: `docs/superpowers/specs/2026-04-18-grafana-dashboard-design.md`.
```

- [ ] **Step 2: Add new "Observability" section**

Immediately after the `## Backlog (não-código)` section, insert a new section:

```markdown
## Observability

- **Stack:** Prometheus + Grafana + Alertmanager + blackbox_exporter on openclaw VPS.
- **Scrape transport:** Tailscale overlay; no public `/metrics` exposure.
- **Dashboard access:** SSH tunnel — `ssh -L 3000:localhost:3000 openclaw-vps`, then `http://localhost:3000`.
- **Alert channel:** Telegram `@kaiOpsBot` (separate from `@clawvirtualagentbot`).
- **Worker `/metrics` endpoint:** exposed at `:8006/metrics` (Task 1 of this feature). The bind-host override `HEALTH_BIND_HOST=0.0.0.0` in `docker-compose.yml:112` is load-bearing — do not revert to the `config.py` default of `127.0.0.1`, or all `:8006/*` scrapes break.
- **Adding another app:** see `ops/monitoring/README.md` "Adding another app".
- **Deploy:** `ops/monitoring/stack/DEPLOY.md`.
- **Static validator:** `./ops/monitoring/verify.sh` before every config commit.
```

- [ ] **Step 3: Verify tests still pass (no change here, but confirm)**

Run: `pytest tests/ -q`

Expected: 378 passed.

- [ ] **Step 4: Lint (no-op check)**

Task 13 only touches `CLAUDE.md` (markdown — no Python linting applies). Skip unless a previous task left something un-lint-clean. To double-check, run `ruff check worker.py tests/test_worker.py` — it should exit 0 silently.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md backlog #2 marcado concluido + secao Observability

Grafana stack shipped em ops/monitoring/. Backlog agora com apenas
o item #1 restante (deploy prod do AUDIT_SYNC_ENABLED=true na VPS).

Nova secao 'Observability' resume stack, access path, worker-bind-host
invariant, e aponta para README + DEPLOY + verify.sh."
```

---

## Final verification (no new task — summary of state)

After Task 13 commits:

- [ ] `git log --oneline -16` shows 14 new commits: Tasks 1, 2, 3, 4, 5, 6, 7, 8a, 8b, 9, 10 (empty smoke-test gate), 11, 12, 13 — plus the pre-existing spec/plan commits.
- [ ] `pytest tests/ -q` → 378 passed.
- [ ] `./ops/monitoring/verify.sh` → full pass.
- [ ] `git status` → clean.
- [ ] Spec acceptance criteria §12 fully satisfied. Cross-check manually:
  - [ ] `ops/monitoring/pje/dashboard.json` present, valid Grafana v11 JSON, 8 panels + stat row.
  - [ ] `ops/monitoring/pje/alert-rules.yml` passes `promtool check rules`, 5 rules.
  - [ ] `ops/monitoring/stack/docker-compose.yml` pins all images, Grafana on `127.0.0.1:3000`.
  - [ ] `ops/monitoring/stack/prometheus.yml` has 4 scrape jobs (+self) and loads rules from bind-mount.
  - [ ] `ops/monitoring/stack/alertmanager.yml` uses native `telegram_configs`.
  - [ ] `ops/monitoring/stack/blackbox.yml` defines `http_2xx_or_503` with `valid_status_codes: [200, 503]`.
  - [ ] Grafana provisioning files present.
  - [ ] `DEPLOY.md` has 10 steps with exact commands.
  - [ ] `verify.sh` present and executable.
  - [ ] `worker.py` has `/metrics` route; `docker-compose.yml:112` override preserved.
  - [ ] Test count 378.
  - [ ] CLAUDE.md updated.
