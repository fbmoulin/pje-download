# Grafana Dashboard — pje-download Observability (P0.4)

**Status:** Design approved, pending spec review
**Date:** 2026-04-18
**Author:** Felipe (design via Kai)
**Closes:** Backlog item #2 (CLAUDE.md) — "Grafana dashboard (fecha P0.4)"
**Related:** Sprint 7 (audit sync to Railway), Sprint 11 (circuit breaker /health)

---

## 1. Context

The pje-download service exposes 21 Prometheus metrics at `:8007/metrics` covering MNI SOAP calls, Google Drive downloads, batch throughput, worker/control-plane events, and (post-Sprint 7) Railway audit sync. All metric instrumentation shipped in Sprints 1–11 (377 tests, 6 merged PRs on 2026-04-17). What is missing is a scraper, a visualization layer, and automated alerting — without them, degradation in any of those 21 dimensions is only visible by manually `grep`-ing `docker logs`.

Backlog item #2 specifies four mandatory alert conditions:

1. `pje_audit_sync_lag_seconds > 60` (event-time lag) — sync falling behind Railway
2. `pje_audit_sync_batches_total{status="failed"}` rate > 0 — Railway unreachable
3. `pje_worker_dead_letters_total > 0` — malformed queue payloads
4. Liveness probe on `/health` detecting `_health_status="redis_unreachable"` (circuit breaker, Sprint 11)

The backlog also signals a deployment preference: "reusar VPS openclaw se possível". openclaw VPS (HostGator, 4 vCPU / 8 GB / 200 GB, Ubuntu 22.04) currently runs only the Lex gateway (systemd) and `@clawvirtualagentbot` (Telegram), leaving ample headroom for a monitoring stack.

## 2. Goals & Non-Goals

**Goals:**

- A single Grafana dashboard with 8 operational panels covering all 5 metric categories from `metrics.py`, not just the 4 alert vectors.
- 4 alert rules (+1 split for the `/health` probe) routed to a **dedicated** Telegram bot separate from the existing agent bot.
- Zero code changes in the pje-download Python app. All observability lives in config (YAML + JSON).
- Dashboards and alert rules version-controlled in the **pje-download repo** next to the metrics they reference. When a metric is added/renamed in `metrics.py`, the dashboard JSON and alert rules must be updated in the same commit.
- Cross-host scrape via Tailscale overlay (no public `/metrics` exposure).
- Stack reusable: when kratos-v5/kcp/pdf-graph enter the tailnet later, they plug into the same Prometheus with a new `scrape_config` + per-app dashboard JSON; no re-deploy of the stack.

**Non-goals (v1):**

- SLO burn-rate alerts (multiwindow/multiburn-rate) — pje-download has no external SLA; premature.
- Anomaly detection (Holt-Winters, trend-change) — overengineering for ~50 jobs/day with one user.
- High-availability Prometheus (sharding, Thanos, Cortex) — single instance is sufficient for five apps on one tailnet.
- Email/PagerDuty fallback — Telegram-only until the first bug report that an alert was missed.
- Log aggregation (Loki, Elasticsearch) — JSON-L logs already tailed by the Railway audit syncer; log observability is a separate project.

## 3. Architecture

```
┌──────────────────────────────┐         ┌──────────────────────────────────────────┐
│ pje VPS                      │         │ openclaw VPS (HostGator, 4vCPU/8GB)      │
│                              │         │                                          │
│  pje-dashboard :8007         │         │  ┌──────────┐   ┌──────────┐             │
│    /metrics  ────────────────┼─ 100.x ─┼─▶│Prometheus│──▶│Grafana   │             │
│    /health   ────────────────┼─ 100.x ─┼─▶│  :9090   │   │ :3000    │             │
│                              │         │  └────┬─────┘   └──────────┘             │
│  tailscaled (node A)         │         │       ▼                                  │
│                              │         │  ┌──────────────┐                        │
└──────────────────────────────┘         │  │Alertmanager  │                        │
                                         │  │   :9093      │──┐                     │
                                         │  └──────────────┘  │                     │
                                         │                    ▼                     │
                                         │            Telegram @kaiOpsBot           │
                                         │  tailscaled (node B)                     │
                                         └──────────────────────────────────────────┘
```

**Components added:**

- **pje VPS:** `tailscaled` daemon only. Nothing else changes; `dashboard_api.py` continues to listen on `:8007` (`/metrics` and `/health` endpoints already exist).
- **openclaw VPS:** Prometheus 2.55, Grafana 11.3, Alertmanager 0.27, blackbox_exporter 0.25. All containers, one `docker-compose.yml`.

**Data flow (scrape cycle):**

1. Every 30 s: Prometheus dispatches two scrapes — `http://<pje-tailnet-ip>:8007/metrics` (metrics endpoint) and, via blackbox_exporter, a probe GET to `http://<pje-tailnet-ip>:8007/health` (to capture HTTP 503 from the Sprint-11 circuit breaker).
2. Tailscale routes both over the WireGuard overlay (UDP, encrypted, NAT-traversal handled). No public port is opened on pje VPS.
3. pje-dashboard replies with ~3–5 KB Prometheus text (21 metric series × ~200 bytes) for the `/metrics` scrape; blackbox_exporter records `probe_http_status_code` for the `/health` probe.
4. Prometheus stores samples in local TSDB (default retention 15 days — plenty for our use).
5. Every 30 s Prometheus evaluates alert rules against TSDB.
6. Firing alerts → Alertmanager. Alertmanager groups by `app` label (`group_wait=10s`, `group_interval=5m`, `repeat_interval=4h`) and forwards via webhook to Telegram `@kaiOpsBot`.
7. Grafana queries Prometheus via its HTTP API (`localhost:9090`) to render panels on refresh (default 30 s).

## 4. Design Decisions

Decisions made during brainstorming (Q1–Q4):

| # | Question | Decision | Rationale |
|---|---|---|---|
| 1 | Host location | **openclaw VPS** | Backlog preference; 8 GB/4 vCPU has ample headroom; separates monitor from monitored; Telegram bot already lives there. |
| 2 | Cross-host scrape transport | **Tailscale overlay** | Zero-trust, no public exposure, NAT traversal free, enables future expansion (kratos/kcp/pdf-graph) with zero marginal config. `/metrics` content (process volume, legal party identifiers in the future) is sensitive enough to warrant encryption. |
| 3 | Alert channel | **Dedicated Telegram bot `@kaiOpsBot`** | Separates ops notifications from agent conversations (existing `@clawvirtualagentbot` is multi-purpose). Same bot will receive alerts from other apps later. |
| 4 | Dashboard scope | **8 panels — operational (not SLO)** | Covers all 5 metric categories; low marginal cost vs minimum (4 panels) because the metrics are already instrumented; troubleshooting is the real day-to-day use, not reactive alerting. |

## 5. Repository Layout

All observability artifacts for pje-download live in this repo (next to the metrics they reference):

```
ops/monitoring/
  README.md                    # overview + "how to add a new app" guide
  pje/                         # per-app artifacts (evolve with metrics.py)
    dashboard.json             # Grafana dashboard JSON (provisioned, 8 panels)
    alert-rules.yml            # 5 Prometheus alert rules
  stack/                       # deploy-once infra for openclaw
    docker-compose.yml         # prometheus + grafana + alertmanager + blackbox_exporter
    prometheus.yml             # scrape_configs (pje-vps via tailnet IP)
    alertmanager.yml           # webhook Telegram @kaiOpsBot
    blackbox.yml               # /health probe module
    grafana/
      provisioning/
        datasources/prometheus.yml
        dashboards/default.yml
    DEPLOY.md                  # SSH openclaw + clone + up -d (step-by-step)
  verify.sh                    # promtool/amtool sanity-check script
```

When kratos-v5/kcp/pdf-graph are added later, each brings its own `ops/monitoring/<app>/{dashboard.json, alert-rules.yml}` in its own repo, and the `stack/prometheus.yml` on openclaw gains one more `scrape_config`. The `stack/` directory is intentionally co-located here (not in a separate openclaw repo) because (a) Felipe has no standing openclaw IaC repo and (b) a single source for the monitoring bootstrap keeps the hand-off unambiguous. Once the stack is live on openclaw, subsequent app dashboards/rules are pulled in by `git clone` or `rsync` into `/opt/monitoring/apps/<app>/`.

## 6. Dashboard Panels (8 + header stat row)

Unit of time: default **last 1h** (user-selectable). Refresh: **30 s**. Data source: single Prometheus DS provisioned as default.

**Header stat row (top of dashboard, not counted in the 8):**

- `up{job="pje-dashboard"}` → green/red stat ("Scrape health")
- `pje_dashboard_active_batches` → gauge ("Active batches now")
- `pje_audit_sync_lag_seconds` → stat with green/yellow/red thresholds (<30, 30–60, >60)

**Row 1 — Audit Sync (CNJ 615/2025 compliance) — 3 panels**

1. **Audit sync lag (time series)** — `pje_audit_sync_lag_seconds`. Visual thresholds: green <30, yellow 30–60, red >60. Correlates with alert #1 (`PjeAuditSyncLagHigh`).
2. **Sync batches by status (stacked timeseries)** — `rate(pje_audit_sync_batches_total[5m])` by `status`. Shows success vs retry vs failed stream. Correlates with alert #2 (`PjeAuditSyncBatchesFailing`).
3. **Sync tick latency (heatmap)** — `pje_audit_sync_latency_seconds` buckets. Detects Railway latency degradation before it becomes a lag spike.

**Row 2 — Worker & control plane — 2 panels**

4. **Dead letters by reason (time series)** — `increase(pje_worker_dead_letters_total[1h])` by `reason`. Correlates with alert #3 (`PjeWorkerDeadLetters`).
5. **Publish failures / timeouts / recoveries (time series, 3 lines)** — `rate(pje_worker_publish_failures_total[5m])`, `rate(pje_dashboard_batch_timeouts_total[5m])`, `increase(pje_dashboard_active_batch_recoveries_total[1h])`. Redis-loop health signal.

**Row 3 — MNI SOAP — 2 panels**

6. **MNI latency p50/p95/p99 (time series, 3 lines)** — `histogram_quantile(0.5, rate(pje_mni_latency_seconds_bucket[5m]))` etc., grouped by `operation`. Detects PJe slowness before timeouts fire.
7. **MNI request outcomes (stacked time series)** — `rate(pje_mni_requests_total[5m])` by `status`. Proportion of success vs mni_error vs auth_failed vs timeout.

**Row 4 — GDrive — 1 panel**

8. **GDrive strategy success rate (stacked percentage)** — `sum(rate(pje_gdrive_attempts_total{status="success"}[5m])) by (strategy) / sum(rate(pje_gdrive_attempts_total[5m])) by (strategy)`. Detects when gdown breaks (Google anti-bot) and Playwright fallback starts carrying load.

All panels reference metrics already instrumented in `metrics.py`. Zero new instrumentation required.

## 7. Alert Rules

File: `ops/monitoring/pje/alert-rules.yml`. Evaluation interval: 30 s. All rules carry labels `severity`, `app=pje-download`, and annotation `runbook`.

```yaml
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

      - alert: PjeCircuitBreakerOpen
        expr: probe_http_status_code{job="pje-health-probe"} == 503
        for: 1m
        labels:
          severity: critical
          app: pje-download
        annotations:
          summary: "pje-dashboard /health retorna 503 (circuit breaker open)"
          description: "Sprint 11: blpop circuit breaker disparou após REDIS_CIRCUIT_THRESHOLD falhas. Worker não processa."
          runbook: "docker logs pje-redis; docker restart pje-redis"
```

**Design note — 4 backlog alerts → 5 rules:** the backlog alert #4 ("Liveness probe via `/health` detecta circuit breaker") covers two distinct failure modes that Prometheus `up==0` cannot distinguish: (a) the container is fully down vs (b) the container is up but `/health` returns HTTP 503 because the Sprint-11 circuit breaker is open. We split the logical alert into `PjeDashboardDown` and `PjeCircuitBreakerOpen` so runbooks and paging policy can differ (the first implies SSH/restart; the second implies investigating Redis first). Capturing the 503 requires blackbox_exporter, which is added to the stack.

## 8. Error Handling & Failure Modes

| Failure | Detection | Behavior |
|---|---|---|
| pje-dashboard container down | `up==0` for 2m | `PjeDashboardDown` critical |
| Tailscale offline on pje VPS | Prometheus can't reach `:8007` → `up==0` | same alert; operationally indistinguishable from container-down |
| Tailscale offline on openclaw | all jobs `up==0` across all future apps | alerts cannot fire from Prometheus itself — see **Out of scope** (DeadMansSwitch) |
| Telegram API rate-limit / down | Alertmanager retries (default 1 min); failures visible in `:9093/#/status` | alerts queue until Telegram recovers |
| Railway audit DB down (cascading) | both `PjeAuditSyncBatchesFailing` and `PjeAuditSyncLagHigh` fire | Alertmanager groups by `app=pje-download` → single Telegram message with both alerts |
| Grafana down | alerting is unaffected (Alertmanager is independent) | dashboard inaccessible, alerts still reach Telegram |
| pje-dashboard circuit breaker opens (Sprint 11 behavior) | `probe_http_status_code==503` for 1m | `PjeCircuitBreakerOpen` critical |
| Prometheus TSDB disk full | Prometheus stops ingesting, self-alerting possible via `prometheus_tsdb_*` metrics | out of v1 scope; 15 GB volume chosen for ~6 months of retention at current cardinality |

## 9. Testing Strategy

No Python test changes. Test count remains 377. Validation layers:

1. **Config syntax (CI-ready, pre-commit):**

   ```bash
   ops/monitoring/verify.sh
   # runs: promtool check rules ops/monitoring/pje/alert-rules.yml
   #       promtool check config ops/monitoring/stack/prometheus.yml
   #       amtool check-config ops/monitoring/stack/alertmanager.yml
   #       jq . ops/monitoring/pje/dashboard.json > /dev/null
   ```

2. **Dashboard JSON schema:** `jq` parse check + manual import verification in Grafana (panels render, queries resolve). Grafana's `/api/dashboards/db` endpoint returns 400 on schema violation.

3. **End-to-end local test (before openclaw deploy):**
   - Start a local pje-download: `docker compose up -d` (the existing compose, unmodified).
   - Start the monitoring stack on the same host: `docker compose -f ops/monitoring/stack/docker-compose.yml up -d` with `prometheus.yml` temporarily rewritten to target `host.docker.internal:8007`.
   - Verify: Prometheus `:9090/targets` shows `pje-dashboard` UP within 30 s.
   - Fire a synthetic alert: `amtool alert add app=pje-download severity=critical alertname=SmokeTest`. Confirm `@kaiOpsBot` Telegram delivery within 30 s.
   - Tear down: `docker compose -f ops/monitoring/stack/docker-compose.yml down -v` (ephemeral; zero state leak).

4. **Post-deploy verification on openclaw (part of `DEPLOY.md`):**
   - `http://openclaw:9090/targets` → both `pje-dashboard` and `pje-health-probe` UP.
   - Trigger `PjeWorkerDeadLetters` artificially: `redis-cli -h <pje-tailnet-ip> LPUSH pje:work '{garbage:true}'`; alert fires within 1 min.
   - Open `http://openclaw:3000` → pje-download dashboard auto-discovered, 8 panels render with live data.

## 10. Deployment (condensed)

Full step-by-step in `ops/monitoring/stack/DEPLOY.md`. Summary:

1. Install Tailscale on both VPS. Record tailnet IPs.
2. BotFather: create `@kaiOpsBot`. Record bot token and chat ID (via `@userinfobot`).
3. SSH openclaw: `git clone https://github.com/fbmoulin/pje-download /opt/monitoring/apps/pje-download`.
4. Edit `stack/prometheus.yml` → substitute `<PJE_TAILNET_IP>`.
5. Edit `stack/alertmanager.yml` → fill `bot_token`, `chat_id`.
6. `cd /opt/monitoring/apps/pje-download/ops/monitoring/stack && docker compose up -d`.
7. Port-forward or open firewall: `ssh -L 3000:localhost:3000 openclaw-vps` → browse `http://localhost:3000` → admin/admin → change password.
8. Dashboard is auto-provisioned (no manual import). Both scrape jobs should be UP.
9. Fire `amtool alert add` smoke test. Confirm Telegram.
10. Enable UFW rule on openclaw to block public `:9090`, `:9093` (only Grafana `:3000` may be accessed, and even that via SSH tunnel).

**Rollback:** `docker compose down -v` at the stack directory. TSDB is erased, but nothing in pje-download depends on it — JSON-L local logs and Railway audit table remain the sources of truth.

## 11. Out of Scope (v1 — future work)

- **DeadMansSwitch watchdog alert** — detects Prometheus itself going down. Standard pattern (`expr: vector(1)`, always firing, external cron checks that it arrives every N minutes). Adds maturity; defer until the first silent outage.
- **Multi-app scaling (kratos/kcp/pdf-graph)** — stack is designed to handle them but no config committed yet. Each app will contribute its own `ops/monitoring/<app>/` directory when onboarded.
- **Grafana OAuth / SSO** — v1 uses local admin; acceptable because Grafana port is firewalled off (SSH-only access).
- **Loki / log aggregation** — separate project; JSON-L logs are already queryable by Railway syncer.
- **SLO burn-rate alerts, anomaly detection, recording rules** — overengineering at current scale.

## 12. Acceptance Criteria (for plan-review hand-off)

The plan that follows this spec must produce, at minimum:

- `ops/monitoring/pje/dashboard.json` — valid Grafana v11 JSON, 8 panels + stat row, importable without errors.
- `ops/monitoring/pje/alert-rules.yml` — passes `promtool check rules`; defines the 5 rules above verbatim.
- `ops/monitoring/stack/docker-compose.yml` — pins Prometheus 2.55, Grafana 11.3, Alertmanager 0.27, blackbox_exporter 0.25; all named containers; all restart `unless-stopped`; Grafana bound to `127.0.0.1:3000` only.
- `ops/monitoring/stack/prometheus.yml` — scrape jobs `pje-dashboard` (pulls `/metrics`) and `pje-health-probe` (via blackbox_exporter, pulls `/health`), 30 s interval; includes `rule_files:` pointing to `pje/alert-rules.yml`.
- `ops/monitoring/stack/alertmanager.yml` — single receiver of type `telegram_configs`, env-var-substitutable `bot_token` and `chat_id`, grouped by `[app]`.
- `ops/monitoring/stack/blackbox.yml` — HTTP 2xx prober module used by the `pje-health-probe` job.
- `ops/monitoring/stack/grafana/provisioning/` — datasource + dashboard providers (bind-mount of `ops/monitoring/pje/`).
- `ops/monitoring/stack/DEPLOY.md` — the 10-step deploy runbook, complete with exact commands.
- `ops/monitoring/verify.sh` — pre-commit validation script; exits nonzero on any syntax failure.
- No changes to any `.py` file in the repo.
- Test count unchanged (377).
- `CLAUDE.md` → backlog item #2 marked complete; new "Observability" section added.

## 13. References

- Backlog item: `CLAUDE.md#backlog-não-código`
- Metric definitions: `metrics.py` (all 21 series)
- Circuit breaker origin: Sprint 11 (commit `574c1fb`, PR #9) — blpop circuit breaker with `REDIS_CIRCUIT_THRESHOLD=20` → `/health` returns 503
- Audit sync origin: Sprint 7 (commit `6612135`, PR #3) — Railway Postgres syncer
- Previous memos: `2026-04-17-audit-sweep-session.md` (audit sweep session)
- openclaw VPS details: MEMORY.md → `topic/session-2026-04-16-openclaw-vps-install.md`
