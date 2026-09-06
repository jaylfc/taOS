#!/usr/bin/env bash
# Recursive ad-hoc codesign of taOS.app.
# When DEV_ID env var is set, signs with the Developer ID identity instead.
#
# Args: --app <PATH>
set -euo pipefail

APP=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --app) APP="$2"; shift 2 ;;
    *) echo "sign.sh: unknown arg $1" >&2; exit 2 ;;
  esac
done

[[ -n "$APP" ]] || { echo "--app required" >&2; exit 2; }
[[ -d "$APP" ]] || { echo "no such bundle: $APP" >&2; exit 1; }

if [[ -n "${DEV_ID:-}" ]]; then
  IDENTITY="Developer ID Application: $DEV_ID"
  EXTRA_ARGS=(--options runtime --timestamp)
  echo "[sign] using Dev ID: $DEV_ID"
else
  IDENTITY="-"
  EXTRA_ARGS=()
  echo "[sign] ad-hoc signing (v0.1, no Dev ID)"
fi

# Empty-array expansion is unbound-safe with this guard (macOS ships bash 3.2)
EA=("${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}")

# Sign nested binaries deepest-first. We use a while-read loop instead of
# `xargs ... 2>/dev/null || true` so any codesign failure surfaces and exits.
while IFS= read -r -d '' bin; do
  codesign --force --sign "$IDENTITY" "${EA[@]+"${EA[@]}"}" "$bin"
done < <(
  find "$APP/Contents" -depth \( -type f -perm -u+x -o -type f -name "*.dylib" \) -print0
)

# Sign frameworks
find "$APP/Contents/Frameworks" -name "*.framework" -maxdepth 2 -type d -print0 \
  | xargs -0 -I{} codesign --force --sign "$IDENTITY" "${EA[@]+"${EA[@]}"}" {}

# Sign the bundle itself last
codesign --force --deep --sign "$IDENTITY" "${EA[@]+"${EA[@]}"}" "$APP"

echo "[sign] verifying"
codesign --verify --deep --strict --verbose=2 "$APP"

echo "[sign] done"
