#!/usr/bin/env bash
# Notarise the DMG via xcrun notarytool. v0.1: stub when no Dev ID.
#
# Args: --dmg <PATH>
set -euo pipefail

DMG=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dmg) DMG="$2"; shift 2 ;;
    *) echo "notarize.sh: unknown arg $1" >&2; exit 2 ;;
  esac
done

[[ -n "$DMG" ]] || { echo "--dmg required" >&2; exit 2; }

if [[ -z "${DEV_ID:-}" || -z "${NOTARY_PROFILE:-}" ]]; then
  echo "[notarize] skipped — no DEV_ID / NOTARY_PROFILE configured (v0.1)"
  exit 0
fi

echo "[notarize] submitting $DMG"
xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
xcrun stapler staple "$DMG"
echo "[notarize] stapled"
