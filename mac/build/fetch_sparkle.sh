#!/usr/bin/env bash
# Fetch and verify Sparkle 2.6.0 release tarball for macOS updaters.
#
# Args: --output <STAGING_DIR>
# Output: $STAGING_DIR/Sparkle.framework (directory structure)

set -euo pipefail

OUTPUT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --output) OUTPUT="$2"; shift 2 ;;
    *) echo "fetch_sparkle.sh: unknown arg $1" >&2; exit 2 ;;
  esac
done

[[ -n "$OUTPUT" ]] || { echo "--output required" >&2; exit 2; }

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

# Sparkle 2.6.0 (as per mac/launcher/Package.swift comments)
TAG="2.6.0"
CHECKSUM_FILE="$REPO_ROOT/mac/build/checksums/sparkle-${TAG}.sha256"
[[ -f "$CHECKSUM_FILE" ]] || {
  echo "[fetch_sparkle] missing checksum file for ${TAG}: $CHECKSUM_FILE" >&2
  exit 2
}

# Swift Package Manager expects a zip file, not tarball
# The release page contains both "Sparkle-for-Swift-Package-Manager.zip"
# and "sparkle-${TAG}.tar.gz". We need the zip.
URL="https://github.com/sparkle-project/Sparkle/releases/download/${TAG}/Sparkle-for-Swift-Package-Manager.zip"

mkdir -p "$OUTPUT"
ZIP="$OUTPUT/sparkle-${TAG}.zip"

echo "[fetch_sparkle] downloading $URL"
curl -L --fail -o "$ZIP" "$URL"

EXPECTED_SHA="$(cat "$CHECKSUM_FILE")"
ACTUAL_SHA="$(shasum -a 256 "$ZIP" | awk '{print $1}')"
if [[ "$EXPECTED_SHA" != "$ACTUAL_SHA" ]]; then
  echo "[fetch_sparkle] SHA mismatch: expected $EXPECTED_SHA got $ACTUAL_SHA" >&2
  exit 1
fi

echo "[fetch_sparkle] extracting"
# The zip contains Sparkle.framework directly
unzip -o "$ZIP" -d "$OUTPUT"
rm "$ZIP"

echo "[fetch_sparkle] done: $OUTPUT/Sparkle.framework"
