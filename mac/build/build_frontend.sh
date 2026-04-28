#!/usr/bin/env bash
# Build the Vite frontend and copy output to staging.
#
# Args: --output <STAGING_DIR>
set -euo pipefail

OUTPUT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --output) OUTPUT="$2"; shift 2 ;;
    *) echo "build_frontend.sh: unknown arg $1" >&2; exit 2 ;;
  esac
done

[[ -n "$OUTPUT" ]] || { echo "--output required" >&2; exit 2; }

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

cd "$REPO_ROOT/desktop"
echo "[build_frontend] npm ci"
npm ci
echo "[build_frontend] npm run build"
npm run build

# Vite is configured (desktop/vite.config.ts) to write to ../static/desktop
DIST="$REPO_ROOT/static/desktop"
[[ -d "$DIST" ]] || { echo "[build_frontend] missing $DIST" >&2; exit 1; }

mkdir -p "$OUTPUT/frontend"
cp -R "$DIST"/. "$OUTPUT/frontend/"
echo "[build_frontend] done: $OUTPUT/frontend"
