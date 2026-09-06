#!/usr/bin/env bash
# Prints the detected incus storage pool driver + subvolume path + whether
# Snapper is actively covering it. Read-only; does not change state.
set -euo pipefail

if ! command -v incus >/dev/null 2>&1; then
  echo "incus not installed"
  exit 0
fi

incus storage list -f csv -c nDS 2>/dev/null | while IFS=, read -r name driver source; do
  echo "pool: $name  driver: $driver  source: $source"
done

if command -v snapper >/dev/null 2>&1; then
  echo
  echo "snapper configs:"
  snapper list-configs 2>/dev/null | sed 's/^/  /'

  if snapper list-configs 2>/dev/null | grep -q taos-containers; then
    echo
    echo "taos-containers snapshot count:"
    snapper -c taos-containers list 2>/dev/null | wc -l
    echo "most recent 3:"
    snapper -c taos-containers list 2>/dev/null | tail -3
  fi
else
  echo
  echo "snapper not installed — Layer 3 backstop unavailable"
fi
