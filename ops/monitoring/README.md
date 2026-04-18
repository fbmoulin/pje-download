# ops/monitoring/

Observability artifacts for pje-download and, eventually, other apps
that Felipe runs (kratos-v5, kcp, pdf-graph).

## Structure

- `pje/` — per-app artifacts (`dashboard.json`, `alert-rules.yml`). **Co-located
  with the code that defines the metrics they query**, so a change in
  `metrics.py` and the dashboard panel update can ship in the same commit.
- `stack/` — deploy-once infra (`docker-compose.yml`, `prometheus.yml`,
  `alertmanager.yml`, `blackbox.yml`, `grafana/`). Runs on openclaw VPS.
- `verify.sh` — pre-commit static validator (promtool, amtool, jq, compose,
  blackbox-exporter `--config.check`). Uses docker fallbacks with
  `--entrypoint` overrides when host binaries are missing.

## Quick start

```bash
# Static validation of all configs (idempotent; safe to run anytime)
./ops/monitoring/verify.sh

# Full end-to-end smoke on dev machine (monitoring stack only, no pje-download)
# See DEPLOY.md step 10 for the full pje-download runtime chain on openclaw.
cd ops/monitoring/stack

# 1. Substitute env placeholders to /tmp
PJE_TAILNET_IP=127.0.0.1 envsubst '${PJE_TAILNET_IP}' \
    < prometheus.yml > /tmp/prometheus.local.yml
BOT_TOKEN=dummy CHAT_ID=1 envsubst '${BOT_TOKEN} ${CHAT_ID}' \
    < alertmanager.yml > /tmp/alertmanager.local.yml

# 2. Create docker-compose.override.yml (gitignored). Two things in one file:
#    (a) swap bind-mounts to /tmp files so prometheus/alertmanager read the
#        substituted versions instead of the tracked templates, and
#    (b) publish 127.0.0.1:{9090,9093,9115} so host-side curl can hit the APIs.
#    In production (openclaw) the override file does NOT exist; Prometheus,
#    Alertmanager, and blackbox stay on the internal ops-net only.

# 3. docker compose up -d; run the 7-assertion smoke script; teardown.
# See git log for the pattern used by commit 29fcbb9 (chore: e2e smoke passed).
```

## Deploy (production)

See `stack/DEPLOY.md`. Target host: openclaw VPS. 10 steps, ~20-30 min on fresh install.

## Adding another app

When `kratos-v5` (or similar) needs to join this stack:

1. **In that app's repo**, create `ops/monitoring/<app>/{dashboard.json, alert-rules.yml}` following the `pje/` structure. Dashboard UID unique, alert rules have `labels: {app: <app>}` to enable grouping.
2. **On openclaw**, `git clone` that repo to `/opt/monitoring/apps/<app>/`.
3. **Edit `stack/prometheus.yml`** (or better: add `stack/prometheus.d/<app>.yml` and include via `rule_files:`): add `scrape_configs` pointing to the app's tailnet endpoints; add a new `rule_files:` entry OR bind-mount pointing to the new app's `alert-rules.yml`.
4. **Edit `stack/docker-compose.yml`** Grafana service: add a new volume binding like `../../<app>/ops/monitoring/<app>:/var/lib/grafana/dashboards/<app>:ro` so Grafana picks up the app's dashboard JSON.
5. **Reload** — `docker compose up -d` (recreates only changed services) OR `docker compose exec prometheus wget -qO- --post-data='' localhost:9090/-/reload` for Prometheus config hot-reload and the analogous call for Alertmanager.

When an openclaw IaC repo exists in the future, move `stack/` there and each
app keeps only its per-app `<app>/` directory. See spec §5 migration path.

## Access

- **Dashboard:** SSH tunnel `ssh -L 3000:localhost:3000 openclaw-vps`, then `http://localhost:3000`.
- **Alerts:** Telegram `@kaiOpsBot` (dedicated — separate from `@clawvirtualagentbot`).
- **Prometheus/Alertmanager/blackbox UIs:** NOT exposed publicly; internal-only on `ops-net`. To inspect: `ssh openclaw-vps; docker compose exec prometheus wget -qO- localhost:9090/<path>`.

## References

- Design spec: `docs/superpowers/specs/2026-04-18-grafana-dashboard-design.md`
- Implementation plan: `docs/superpowers/plans/2026-04-18-grafana-dashboard.md`
- Metrics source: `metrics.py` (21 series) + `worker.py` `_metrics_handler` (Task 1)
- Backlog item closed: `CLAUDE.md` §"Backlog (não-código)" #2
