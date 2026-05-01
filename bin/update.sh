#!/usr/bin/env bash
# bin/update.sh — pull latest, rebuild frontend if stale, restart taOS service.
# Usage: bin/update.sh
# Idempotent: skips rebuild when the static bundle is already current.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# The systemd ExecStartPre rebuild produces new content-hashed bundle files
# in static/desktop/assets/ and modifies desktop/tsconfig.tsbuildinfo, leaving
# the working tree dirty after every restart. git pull --ff-only refuses to
# overwrite that, so the user can never update. Wipe build outputs first;
# they're regenerated below if needed and otherwise come down with the pull.
echo "==> Resetting build outputs before pull..."
git checkout -- desktop/tsconfig.tsbuildinfo static/desktop 2>/dev/null || true
git clean -fd static/desktop/assets 2>/dev/null || true

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
