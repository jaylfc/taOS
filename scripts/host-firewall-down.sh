#!/bin/bash
# host-firewall-down.sh — remove the ACCEPT rules inserted by host-firewall-up.sh.
#
# Idempotent: uses 'iptables -C' to check before each '-D'. Safe to run even
# if the rules are already absent or DOCKER-USER no longer exists.
#
# Environment:
#   BRIDGES  space-separated list of bridge interfaces to remove
#            default: incusbr0

set -euo pipefail

if [ "$EUID" -ne 0 ]; then
    echo "host-firewall-down.sh: must run as root" >&2
    exit 1
fi

BRIDGES="${BRIDGES:-incusbr0}"

# If the DOCKER-USER chain doesn't exist, nothing to remove.
if ! iptables -n -L DOCKER-USER >/dev/null 2>&1; then
    echo "host-firewall-down: DOCKER-USER chain not found; nothing to remove."
    exit 0
fi

for BRIDGE in $BRIDGES; do
    if iptables -C DOCKER-USER -i "$BRIDGE" -j ACCEPT 2>/dev/null; then
        iptables -D DOCKER-USER -i "$BRIDGE" -j ACCEPT
        echo "host-firewall-down: removed ACCEPT -i $BRIDGE from DOCKER-USER."
    else
        echo "host-firewall-down: ACCEPT -i $BRIDGE not present; skipping."
    fi

    if iptables -C DOCKER-USER -o "$BRIDGE" -j ACCEPT 2>/dev/null; then
        iptables -D DOCKER-USER -o "$BRIDGE" -j ACCEPT
        echo "host-firewall-down: removed ACCEPT -o $BRIDGE from DOCKER-USER."
    else
        echo "host-firewall-down: ACCEPT -o $BRIDGE not present; skipping."
    fi
done
