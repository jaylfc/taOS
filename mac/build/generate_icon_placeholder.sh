#!/usr/bin/env bash
# Generate placeholder AppIcon.icns from a 1024x1024 PNG.
#
# Args: --source <PNG> --output <DIR>
set -euo pipefail

SRC=""
OUT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --source) SRC="$2"; shift 2 ;;
    --output) OUT="$2"; shift 2 ;;
    *) echo "generate_icon_placeholder.sh: unknown arg $1" >&2; exit 2 ;;
  esac
done

[[ -n "$SRC" && -n "$OUT" ]] || { echo "all args required" >&2; exit 2; }

ICONSET="$OUT/AppIcon.iconset"
mkdir -p "$ICONSET"
for size in 16 32 128 256 512; do
  sips -z "$size" "$size" "$SRC" --out "$ICONSET/icon_${size}x${size}.png"
  double=$((size * 2))
  sips -z "$double" "$double" "$SRC" --out "$ICONSET/icon_${size}x${size}@2x.png"
done
iconutil -c icns "$ICONSET" -o "$OUT/AppIcon.icns"
rm -rf "$ICONSET"
echo "[icon] $OUT/AppIcon.icns"
