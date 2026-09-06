#!/bin/bash
# incus-bridge-probe.sh — detect and resolve incusbr0 subnet collisions.
#
# Reads the current incusbr0 IPv4 address, lists all other interfaces, and
# re-assigns incusbr0 to an unused RFC1918 10.x.y.0/24 subnet if a collision
# is detected.  Idempotent: exits 0 without changes if no collision exists.
#
# Requires: incus, ip (iproute2), awk.
# Must run as root.

set -euo pipefail

if [ "$EUID" -ne 0 ]; then
    echo "incus-bridge-probe: must run as root" >&2
    exit 1
fi

if ! command -v incus >/dev/null 2>&1; then
    echo "incus-bridge-probe: incus not found; skipping bridge collision check."
    exit 0
fi

# Fetch the current incusbr0 address (e.g. "10.26.37.1/24").
INCUS_ADDR=$(incus network get incusbr0 ipv4.address 2>/dev/null || true)
if [ -z "$INCUS_ADDR" ] || [ "$INCUS_ADDR" = "none" ]; then
    echo "incus-bridge-probe: incusbr0 ipv4.address not set; nothing to check."
    exit 0
fi

# Extract the /24 prefix of the incusbr0 address (first 3 octets).
INCUS_PREFIX=$(echo "$INCUS_ADDR" | awk -F'[./]' '{print $1 "." $2 "." $3}')
echo "incus-bridge-probe: incusbr0 address=$INCUS_ADDR  prefix=$INCUS_PREFIX.0/24"

# Gather /24 prefixes in use by all other interfaces (excluding incusbr0 itself).
OTHER_PREFIXES=$(ip -o -f inet addr show \
    | awk '$2 != "incusbr0" {split($4, a, "/"); split(a[1], o, "."); print o[1] "." o[2] "." o[3]}')

# Check for collision.
if ! echo "$OTHER_PREFIXES" | grep -qxF "$INCUS_PREFIX"; then
    echo "incus-bridge-probe: no collision detected; no change needed."
    exit 0
fi

echo "incus-bridge-probe: collision detected on $INCUS_PREFIX.0/24 — selecting a new subnet."

# Build a list of all /24 prefixes currently in use (including incusbr0) so we
# can pick a free one.  We scan the 10.x.y range, varying both x and y, until
# we find a prefix that isn't in use.
ALL_PREFIXES=$(ip -o -f inet addr show \
    | awk '{split($4, a, "/"); split(a[1], o, "."); print o[1] "." o[2] "." o[3]}')

NEW_PREFIX=""
for x in $(shuf -i 16-254 -n 239); do
    for y in $(shuf -i 1-254 -n 254); do
        CANDIDATE="10.$x.$y"
        if ! echo "$ALL_PREFIXES" | grep -qxF "$CANDIDATE"; then
            NEW_PREFIX="$CANDIDATE"
            break 2
        fi
    done
done

if [ -z "$NEW_PREFIX" ]; then
    echo "incus-bridge-probe: could not find a free RFC1918 /24 subnet; leaving incusbr0 unchanged." >&2
    exit 1
fi

NEW_ADDR="${NEW_PREFIX}.1/24"
echo "incus-bridge-probe: reassigning incusbr0 from $INCUS_ADDR to $NEW_ADDR"
incus network set incusbr0 ipv4.address="$NEW_ADDR"
echo "incus-bridge-probe: done — new incusbr0 address: $NEW_ADDR"
