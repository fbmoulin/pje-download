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

# If promtool / amtool missing, use docker wrappers.
# Images default ENTRYPOINT to /bin/prometheus and /bin/alertmanager respectively,
# so we must override with --entrypoint. -i passes stdin (for `check config /dev/stdin`).
if ! command -v "$PROMTOOL" >/dev/null 2>&1; then
    PROMTOOL="docker run --rm -i --entrypoint promtool -v $PWD:/work -w /work prom/prometheus:v2.55.0"
fi
if ! command -v "$AMTOOL" >/dev/null 2>&1; then
    AMTOOL="docker run --rm -i --entrypoint amtool -v $PWD:/work -w /work prom/alertmanager:v0.27.0"
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
    BOT_TOKEN=dummy CHAT_ID=1 envsubst '${BOT_TOKEN} ${CHAT_ID}' \
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
