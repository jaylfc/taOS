#!/usr/bin/env bash
# setup-mdns.sh — Configure mDNS discovery for taOS via Avahi
# Advertises the taOS web UI as _http._tcp on the local network.
# After running, other devices on the LAN can find this instance via avahi-browse.

set -euo pipefail

TAOS_PORT=6969
SERVICE_FILE="/etc/avahi/services/tinyagentos.service"
SERVICE_XML='<?xml version="1.0" standalone='"'"'no'"'"'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">taOS on %h</name>
  <service>
    <type>_http._tcp</type>
    <port>'"${TAOS_PORT}"'</port>
    <txt-record>path=/</txt-record>
    <txt-record>version=1.0</txt-record>
  </service>
</service-group>'

# ── Detect package manager ────────────────────────────────────────────────────
if command -v apt-get &>/dev/null; then
    PKG_INSTALL="sudo apt-get install -y"
elif command -v dnf &>/dev/null; then
    PKG_INSTALL="sudo dnf install -y"
else
    echo "ERROR: No supported package manager found (apt-get or dnf required)." >&2
    exit 1
fi

# ── Install avahi if missing ──────────────────────────────────────────────────
if ! command -v avahi-daemon &>/dev/null && [ ! -f /usr/sbin/avahi-daemon ]; then
    echo "avahi-daemon not found — installing..."
    if command -v apt-get &>/dev/null; then
        $PKG_INSTALL avahi-daemon avahi-utils
    else
        $PKG_INSTALL avahi avahi-tools nss-mdns
    fi
else
    echo "avahi-daemon is already installed."
fi

# ── Enable and start avahi ────────────────────────────────────────────────────
if ! systemctl is-enabled avahi-daemon &>/dev/null; then
    echo "Enabling avahi-daemon..."
    sudo systemctl enable avahi-daemon
fi

if ! systemctl is-active avahi-daemon &>/dev/null; then
    echo "Starting avahi-daemon..."
    sudo systemctl start avahi-daemon
fi

# ── Write the Avahi service file ──────────────────────────────────────────────
echo "Writing Avahi service file to ${SERVICE_FILE}..."
echo "${SERVICE_XML}" | sudo tee "${SERVICE_FILE}" > /dev/null
echo "Service file written."

# ── Restart avahi to pick up the new service ─────────────────────────────────
echo "Restarting avahi-daemon..."
sudo systemctl restart avahi-daemon
sleep 1

if systemctl is-active avahi-daemon &>/dev/null; then
    echo "avahi-daemon is running."
else
    echo "ERROR: avahi-daemon failed to start. Check: journalctl -u avahi-daemon" >&2
    exit 1
fi

# ── Hostname note ─────────────────────────────────────────────────────────────
CURRENT_HOSTNAME="$(hostname)"
echo ""
echo "Current hostname: ${CURRENT_HOSTNAME}"
if [ "${CURRENT_HOSTNAME}" != "tinyagentos" ]; then
    echo ""
    echo "NOTE: This machine's hostname is '${CURRENT_HOSTNAME}', not 'tinyagentos'."
    echo "      Avahi is advertising taOS as a service so it can be found via:"
    echo "        avahi-browse -t _http._tcp"
    echo "      But the mDNS hostname will be '${CURRENT_HOSTNAME}.local', not 'tinyagentos.local'."
    echo ""
    echo "      To use 'tinyagentos.local', set the hostname with:"
    echo "        sudo hostnamectl set-hostname tinyagentos"
    echo "      Then restart avahi: sudo systemctl restart avahi-daemon"
fi

# ── Quick discovery test ──────────────────────────────────────────────────────
echo ""
echo "Testing mDNS discovery (5-second browse)..."
if command -v avahi-browse &>/dev/null; then
    avahi-browse -t _http._tcp 2>/dev/null | grep -i "taos\|tinyagentos\|${CURRENT_HOSTNAME}" || \
        echo "(taOS service not yet visible in 5s browse — try again in a moment)"
else
    echo "avahi-browse not available. Install avahi-utils to test discovery."
fi

echo ""
echo "mDNS setup complete. taOS is advertised as '_http._tcp' on port ${TAOS_PORT}."
