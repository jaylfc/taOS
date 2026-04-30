#!/usr/bin/env bash
# bin/update.sh — pull latest, rebuild frontend if stale, restart taOS service.
# Usage: bin/update.sh
# Idempotent: skips rebuild when the static bundle is already current.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "==> Pulling latest..."
git pull --ff-only

if [ -d desktop ] && { [ ! -f static/desktop/index.html ] || [ -n "$(find desktop/src -type f -newer static/desktop/index.html -print -quit 2>/dev/null)" ]; }; then
  echo "==> Frontend source moved since last build — rebuilding..."
  cd desktop
  npm install --silent
  npm run build
  cd "$REPO_ROOT"
else
  echo "==> Frontend bundle is current — skipping rebuild."
fi

echo "==> Restarting tinyagentos service..."
sudo systemctl restart tinyagentos

echo "==> Done. Service status:"
systemctl status tinyagentos --no-pager | head -5
