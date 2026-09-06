#!/usr/bin/env bash
# Installs Snapper snapshot coverage for the taOS container storage pool.
# Layer 3 of the recycle-bin design (see docs/design/architecture-pivot-v2.md §6).
# Only runs on btrfs-backed pools; ZFS support is Phase 2+. Safe to re-run.

set -euo pipefail

if [ "$EUID" -ne 0 ]; then
  echo "fs-snapshot-install: must run as root"
  exit 1
fi

# Detect incus storage pool driver. Single-pool assumption for MVP.
DRIVER=$(incus storage list -f csv -c nD 2>/dev/null | head -1 | cut -d, -f2)
POOL=$(incus storage list -f csv -c nD 2>/dev/null | head -1 | cut -d, -f1)

if [ -z "$DRIVER" ]; then
  echo "fs-snapshot-install: incus not initialised or no storage pool found; skipping."
  exit 0
fi

echo "fs-snapshot-install: detected incus pool '$POOL' driver '$DRIVER'"

case "$DRIVER" in
  btrfs)
    ;;
  zfs)
    echo "fs-snapshot-install: zfs pool detected; use zfs-auto-snapshot (Phase 2+). Skipping."
    exit 0
    ;;
  dir|*)
    echo "fs-snapshot-install: driver '$DRIVER' does not support snapshots; Layer 3 unavailable."
    echo "fs-snapshot-install: recycle bin still works via /usr/local/bin/rm soft-delete in each container."
    exit 0
    ;;
esac

# btrfs path — install snapper if missing, configure the pool subvolume.
command -v snapper >/dev/null 2>&1 || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq snapper

# Resolve the pool's btrfs subvolume path.
POOL_PATH=$(incus storage show "$POOL" 2>/dev/null | awk -F': ' '/source:/ {print $2; exit}')
if [ -z "$POOL_PATH" ] || [ ! -d "$POOL_PATH" ]; then
  echo "fs-snapshot-install: could not resolve source path for pool '$POOL'; skipping."
  exit 0
fi

# Create snapper config if missing. Name it 'taos-containers'.
if ! snapper list-configs 2>/dev/null | grep -q taos-containers; then
  snapper -c taos-containers create-config "$POOL_PATH"
fi

# Retention: hourly snapshots, keep 24; daily, keep 7; nothing weekly/monthly.
snapper -c taos-containers set-config \
  TIMELINE_CREATE=yes \
  TIMELINE_CLEANUP=yes \
  TIMELINE_MIN_AGE=1800 \
  TIMELINE_LIMIT_HOURLY=24 \
  TIMELINE_LIMIT_DAILY=7 \
  TIMELINE_LIMIT_WEEKLY=0 \
  TIMELINE_LIMIT_MONTHLY=0 \
  NUMBER_CLEANUP=yes \
  NUMBER_MIN_AGE=1800 \
  NUMBER_LIMIT=50 \
  NUMBER_LIMIT_IMPORTANT=10

# Enable snapper's timers.
systemctl enable --now snapper-timeline.timer
systemctl enable --now snapper-cleanup.timer

echo "fs-snapshot-install: snapper configured on pool '$POOL' (btrfs). Hourly snapshots + 7-day retention."
snapper list-configs
