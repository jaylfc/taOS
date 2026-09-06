#!/usr/bin/env bash
# Package taOS.app into a DMG via create-dmg.
#
# Args: --app <PATH> --version <X.Y.Z> --output <DIR>
set -euo pipefail

APP=""
VERSION=""
OUTPUT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --app) APP="$2"; shift 2 ;;
    --version) VERSION="$2"; shift 2 ;;
    --output) OUTPUT="$2"; shift 2 ;;
    *) echo "package_dmg.sh: unknown arg $1" >&2; exit 2 ;;
  esac
done

[[ -n "$APP" && -n "$VERSION" && -n "$OUTPUT" ]] || { echo "all args required" >&2; exit 2; }

command -v create-dmg >/dev/null || { echo "create-dmg not installed (brew install create-dmg)" >&2; exit 1; }

DMG="$OUTPUT/taOS-$VERSION.dmg"
rm -f "$DMG"

# --skip-jenkins bypasses the Finder-prettifying AppleScript that
# intermittently fails with "AppleEvent timed out (-1712)". The
# /Applications drop link is still created; only the custom icon
# positioning is skipped.
create-dmg \
  --volname "taOS $VERSION" \
  --window-size 600 400 \
  --icon-size 96 \
  --app-drop-link 450 200 \
  --hdiutil-quiet \
  --skip-jenkins \
  "$DMG" \
  "$APP"

echo "[package_dmg] done: $DMG"
