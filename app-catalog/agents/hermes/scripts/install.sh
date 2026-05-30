#!/usr/bin/env bash
# install.sh — Hermes Agent Gateway runtime installer
# Runs once inside a fresh Debian bookworm LXC container.
# Idempotent: safe to re-run on an already-provisioned container.
set -euo pipefail

echo "[hermes] installing Hermes Agent Gateway"

# ---------------------------------------------------------------------------
# 1. System dependencies
# ---------------------------------------------------------------------------
DEBIAN_FRONTEND=noninteractive apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
  python3 python3-pip python3-venv git curl ca-certificates

# ---------------------------------------------------------------------------
# 2. Install hermes-agent from PyPI
# ---------------------------------------------------------------------------
pip3 install --break-system-packages hermes-agent

# Verify
if ! command -v hermes >/dev/null 2>&1; then
  echo "[hermes] FATAL: hermes CLI not found after install"
  exit 1
fi

HERMES_VERSION=$(hermes --version 2>/dev/null || echo "unknown")
echo "[hermes] installed: $HERMES_VERSION"

# ---------------------------------------------------------------------------
# 3. Create directories
# ---------------------------------------------------------------------------
mkdir -p /var/lib/hermes /var/log/hermes
chmod 750 /var/lib/hermes

# ---------------------------------------------------------------------------
# 4. Systemd unit
# ---------------------------------------------------------------------------
cat > /etc/systemd/system/hermes-gateway.service <<'UNIT'
[Unit]
Description=Hermes Agent Gateway
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=-/var/lib/hermes/env
ExecStart=/usr/local/bin/hermes gateway start
Restart=on-failure
RestartSec=5
WorkingDirectory=/var/lib/hermes
StandardOutput=append:/var/log/hermes/gateway.log
StandardError=append:/var/log/hermes/gateway.log

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable hermes-gateway.service

echo "[hermes] install complete (service enabled, start deferred to deployer)"
echo "[hermes] deployer must write /var/lib/hermes/env with:"
echo "        HERMES_PROFILE=taos-agent"
echo "        HERMES_HOME=/var/lib/hermes"
echo "        TAOS_BRIDGE_URL=<url>"
echo "        TAOS_AGENT_API_KEY=<key>"
