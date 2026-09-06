#!/bin/bash
# host-firewall-up.sh — insert ACCEPT rules into DOCKER-USER so incus bridge
# traffic isn't dropped by Docker's default FORWARD policy.
#
# Idempotent: uses 'iptables -C' to check before each '-I'. Safe to run
# multiple times (e.g. on service restart, timer tick, or manual invocation).
#
# Usage:
#   host-firewall-up.sh           normal mode: insert missing rules
#   host-firewall-up.sh --check   check mode: exit 0 if rules present, 1 if any missing
#
# Environment:
#   BRIDGES  space-separated list of bridge interfaces to allow
#            default: incusbr0

set -euo pipefail

CHECK_MODE=0
if [ "${1:-}" = "--check" ]; then
    CHECK_MODE=1
fi

if [ "$EUID" -ne 0 ]; then
    echo "host-firewall-up.sh: must run as root" >&2
    exit 1
fi

BRIDGES="${BRIDGES:-incusbr0}"

# If neither incus binary nor incusbr0 interface exists, there is nothing to
# protect — skip silently so the unit stays healthy on pure-Docker hosts.
if ! command -v incus >/dev/null 2>&1 && ! ip link show incusbr0 >/dev/null 2>&1; then
    echo "host-firewall-up: no incus binary and no incusbr0 interface found; nothing to configure."
    exit 0
fi

# If the DOCKER-USER chain doesn't exist, there's nothing to do.
if ! iptables -n -L DOCKER-USER >/dev/null 2>&1; then
    echo "host-firewall-up: DOCKER-USER chain not found (docker not installed?); nothing to do."
    exit 0
fi

all_present=1

for BRIDGE in $BRIDGES; do
    # Rule 1: traffic arriving on the bridge (container → host or container → internet)
    if iptables -C DOCKER-USER -i "$BRIDGE" -j ACCEPT 2>/dev/null; then
        echo "host-firewall-up: ACCEPT -i $BRIDGE already present; skipping."
    else
        all_present=0
        if [ "$CHECK_MODE" -eq 0 ]; then
            iptables -I DOCKER-USER -i "$BRIDGE" -j ACCEPT
            echo "host-firewall-up: inserted ACCEPT -i $BRIDGE into DOCKER-USER."
        else
            echo "host-firewall-up: ACCEPT -i $BRIDGE missing."
        fi
    fi

    # Rule 2: traffic leaving the bridge toward containers (return traffic / initiated from host)
    if iptables -C DOCKER-USER -o "$BRIDGE" -j ACCEPT 2>/dev/null; then
        echo "host-firewall-up: ACCEPT -o $BRIDGE already present; skipping."
    else
        all_present=0
        if [ "$CHECK_MODE" -eq 0 ]; then
            iptables -I DOCKER-USER -o "$BRIDGE" -j ACCEPT
            echo "host-firewall-up: inserted ACCEPT -o $BRIDGE into DOCKER-USER."
        else
            echo "host-firewall-up: ACCEPT -o $BRIDGE missing."
        fi
    fi
done

if [ "$CHECK_MODE" -eq 1 ]; then
    if [ "$all_present" -eq 1 ]; then
        echo "host-firewall-up: --check passed; all rules present."
        exit 0
    else
        echo "host-firewall-up: --check failed; one or more rules missing."
        exit 1
    fi
fi
