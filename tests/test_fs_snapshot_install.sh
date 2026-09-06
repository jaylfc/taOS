#!/usr/bin/env bash
set -euo pipefail

SCRIPT=scripts/fs-snapshot-install.sh
PROBE=scripts/fs-snapshot-probe.sh

echo "test: syntax (install)"
bash -n "$SCRIPT"

echo "test: syntax (probe)"
bash -n "$PROBE"

echo "test: install script exits 0 when run on a host without incus (simulates container-less CI)"
# CI machines don't have incus. Run as root to bypass the EUID check; the
# script should hit the "incus not initialised" early-exit path and return 0.
if [ "$EUID" -eq 0 ]; then
  if command -v incus >/dev/null 2>&1; then
    echo "(skipping no-incus test: incus is available locally)"
  else
    bash "$SCRIPT" || { echo "FAIL: expected exit 0 on no-incus host"; exit 1; }
  fi
else
  echo "(skipping no-incus test: not running as root)"
fi

echo "test: probe script is read-only (no state changes)"
# Spot-check: probe uses no systemctl commands that mutate.
if grep -E "systemctl (enable|start|disable|stop)" "$PROBE" >/dev/null; then
  echo "FAIL: probe contains systemctl mutations"
  exit 1
fi

echo "test: install script contains btrfs case block"
if ! grep -q "btrfs)" "$SCRIPT"; then
  echo "FAIL: btrfs case block missing from install script"
  exit 1
fi

echo "test: install script handles zfs with skip message"
if ! grep -q "zfs-auto-snapshot" "$SCRIPT"; then
  echo "FAIL: zfs skip message missing from install script"
  exit 1
fi

echo "test: install script uses taos-containers config name"
if ! grep -q "taos-containers" "$SCRIPT"; then
  echo "FAIL: snapper config name 'taos-containers' not found in install script"
  exit 1
fi

echo "test: retention limits are set (24 hourly, 7 daily)"
if ! grep -q "TIMELINE_LIMIT_HOURLY=24" "$SCRIPT"; then
  echo "FAIL: TIMELINE_LIMIT_HOURLY=24 not found"
  exit 1
fi
if ! grep -q "TIMELINE_LIMIT_DAILY=7" "$SCRIPT"; then
  echo "FAIL: TIMELINE_LIMIT_DAILY=7 not found"
  exit 1
fi

echo "test: install script does not touch container-side scripts"
if grep -rq "Layer 1\|recycle-bin-install\|taos-trash" "$SCRIPT"; then
  echo "FAIL: install script references container-side Layer 1 logic"
  exit 1
fi

echo "all tests passed"
