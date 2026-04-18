# Deploy Runbook — openclaw Monitoring Stack

**Target host:** openclaw VPS (`ssh openclaw-vps` — alias in `~/.ssh/config`).
**Target path:** `/opt/monitoring/apps/pje-download/`
**Prerequisite:** both VPS (openclaw + pje) already running. pje-download image built with the `worker /metrics` change from Task 1.

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
curl -sf http://<PJE_TAILNET_IP>:8006/health | jq .service
# expected: "pje-worker"
```

## 2. Create `@kaiOpsBot` on Telegram

1. Open Telegram, search `@BotFather`.
2. `/newbot` → name: `Kai Ops Bot`, username: `kaiOpsBot` (or variant — must end in `bot`).
3. Record the **bot token** (format `NNNNN:AAAA...`).
4. Start a DM with the new bot (click its profile → Start). Send `/start`.
5. Get your chat ID:
   - DM `@userinfobot` — it replies with your numeric user ID. That is your `CHAT_ID` for personal alerts.
   - For a group channel, add the bot as admin and use `@RawDataBot` to reveal the channel's negative ID (e.g., `-1001234567890`).

Keep these two values — they fill `${BOT_TOKEN}` and `${CHAT_ID}` in the next steps. `CHAT_ID` must be a non-zero integer (Alertmanager's telegram_config rejects 0 as zero-value).

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

`GRAFANA_ADMIN_PASSWORD` is read by docker-compose via `${GRAFANA_ADMIN_PASSWORD:-admin}` — if not set, defaults to `admin` and Grafana will prompt rotation on first login.

## 5. Substitute env vars into config files

```bash
set -a; source .env; set +a

# prometheus.yml — whitelist envsubst so no stray $-sigils are eaten
envsubst '${PJE_TAILNET_IP}' < prometheus.yml > prometheus.yml.substituted
mv prometheus.yml.substituted prometheus.yml

# alertmanager.yml — same pattern
envsubst '${BOT_TOKEN} ${CHAT_ID}' < alertmanager.yml > alertmanager.yml.substituted
mv alertmanager.yml.substituted alertmanager.yml
```

**Note:** this is a one-way substitution. If you later `git pull` and the upstream file changes, re-run this step after the pull (git restores the template with `${VAR}` placeholders). Alternative: keep the originals as `*.tpl` and regenerate each deploy — future refinement.

## 6. Bring the stack up

```bash
docker compose up -d

docker compose ps
# All 4 services Up (prometheus, alertmanager, blackbox, grafana).
# No restart loops.

# Prometheus sees all 5 scrape targets (4 pje jobs + self-scrape)
docker compose exec prometheus wget -qO- localhost:9090/api/v1/targets \
  | jq '[.data.activeTargets[].scrapePool] | unique'
# Expected: ["pje-dashboard","pje-dashboard-probe","pje-worker","pje-worker-probe","prometheus"]
```

Prometheus, Alertmanager, and blackbox are NOT published to the host in the production compose (only Grafana on `127.0.0.1:3000`). Use `docker compose exec` to inspect their APIs.

## 7. Configure UFW (firewall)

```bash
sudo ufw status    # check current rules

# Prometheus :9090, Alertmanager :9093, Blackbox :9115 are NOT published in
# docker-compose.yml — reachable only on the internal ops-net. Safe by default.
# Grafana :3000 is bound to 127.0.0.1 only — reachable only via SSH tunnel. Safe.
# Tailscale traffic: UDP 41641, allowed by default on tailscale install.

# If ufw isn't set up yet, a minimal policy:
# sudo ufw default deny incoming
# sudo ufw default allow outgoing
# sudo ufw allow ssh
# sudo ufw enable
```

## 8. Access Grafana via SSH tunnel

From Felipe's laptop:
```bash
ssh -L 3000:localhost:3000 openclaw-vps
# Keep this session open; Grafana now reachable at http://localhost:3000
```

Login: `admin` / `<GRAFANA_ADMIN_PASSWORD from .env>` (or `admin` if not set in .env — forced rotation on first login).

Navigate: Dashboards → Browse → `pje-download — operational`. The 8 panels + 4 stat header row should render.

**Known cosmetic issue:** Grafana 11.3 may place the dashboard in the `General` folder rather than a `pje-download` folder (the provider's `folder: pje-download` directive doesn't auto-create the folder when `nestedFolders=true` is enabled by default in 11.3). Fix post-deploy:
```bash
AUTH='Authorization: Basic YWRtaW46YWRtaW4='   # adjust for your password
# Pre-create the folder (idempotent if already exists)
curl -sf -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"title":"pje-download","uid":"pje-download"}' \
  http://localhost:3000/api/folders || true
```
Or via UI: Dashboards → `pje-download — operational` → Settings → Move → choose/create `pje-download` folder. Cosmetic only; panels render correctly regardless.

## 9. Fire a smoke test alert

```bash
ssh openclaw-vps docker exec openclaw-alertmanager amtool alert add \
    alertname=DeployTest app=pje-download severity=warning \
    --annotation=summary="deploy verification" \
    --annotation=runbook="none"
```

Within 30 s a Telegram message should arrive at `@kaiOpsBot`. If not:
- Check `docker logs openclaw-alertmanager | tail -30` for auth errors (wrong bot token — 401) or 400s (malformed chat_id — must be non-zero integer).
- Verify bot is not blocked by your Telegram account (test by sending `/start` to the bot again).
- Check the `alertmanager_notifications_total{integration="telegram"}` counter: `docker compose exec alertmanager wget -qO- localhost:9093/metrics | grep notifications_total` — should increment on each attempt even if delivery fails.

## 10. Verify the real alert path (Redis injection)

This exercises the full chain: pje-worker → Prometheus scrape → alert rule fires → Alertmanager → Telegram.

```bash
ssh <pje-vps>
# kratos:pje:jobs is the Redis queue name per worker.py:1587
docker exec pje-redis redis-cli -a "$REDIS_PASSWORD" LPUSH kratos:pje:jobs '{"garbage":true}'
```

Wait up to 1 min. The worker will consume the garbage payload, fail JSON parsing, push to dead-letter queue (`kratos:pje:dead-letter`), and increment `pje_worker_dead_letters_total{reason="invalid_json"}`. Prometheus scrapes the updated counter, evaluates `increase(pje_worker_dead_letters_total[5m]) > 0` → `PjeWorkerDeadLetters` alert fires → Alertmanager groups by `app=pje-download` → Telegram message arrives at `@kaiOpsBot`.

The Telegram message should read: `[FIRING] PjeWorkerDeadLetters | app: pje-download | severity: warning` + the `summary` and `runbook` annotation content.

Clean up the dead-letter queue after verification:
```bash
docker exec pje-redis redis-cli -a "$REDIS_PASSWORD" DEL kratos:pje:dead-letter
```

## Rollback

```bash
ssh openclaw-vps
cd /opt/monitoring/apps/pje-download/ops/monitoring/stack
docker compose down -v    # drops TSDB, container state, volumes
```

`pje-download` itself is unaffected — the monitoring stack is purely read-only over the tailnet. JSON-L audit logs on pje VPS and Railway Postgres syncer remain the sources of truth for compliance data.

## Updating (after future `git pull`)

```bash
ssh openclaw-vps
cd /opt/monitoring/apps/pje-download
git pull

cd ops/monitoring/stack
# Re-run step 5 if prometheus.yml or alertmanager.yml changed upstream
# (git pull restores the template with ${VAR} placeholders).

docker compose pull              # get any updated image versions
docker compose up -d             # restart services with new configs/images
docker compose exec prometheus wget -qO- -S --post-data='' localhost:9090/-/reload   # hot-reload
docker compose exec alertmanager wget -qO- -S --post-data='' localhost:9093/-/reload
```
