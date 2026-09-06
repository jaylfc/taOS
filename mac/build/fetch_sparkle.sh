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
if command -v shasum >/dev/null 2>&1; then
  ACTUAL_SHA="$(shasum -a 256 "$ZIP" | awk '{print $1}')"
else
  ACTUAL_SHA="$(sha256sum "$ZIP" | awk '{print $1}')"
fi
if [[ "$EXPECTED_SHA" != "$ACTUAL_SHA" ]]; then
  echo "[fetch_sparkle] SHA mismatch: expected $EXPECTED_SHA got $ACTUAL_SHA" >&2
  exit 1
fi

echo "[fetch_sparkle] extracting"
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TEMP_DIR"' EXIT
unzip -o "$ZIP" -d "$TEMP_DIR"
rm "$ZIP"

# Sparkle.xcframework/macos-arm64_x86_64/Sparkle.framework exists per the release notes
SPARKLE_FRAMEWORK_PATH="$TEMP_DIR/Sparkle.xcframework/macos-arm64_x86_64/Sparkle.framework"
if [[ -d "$SPARKLE_FRAMEWORK_PATH" ]]; then
  echo "[fetch_sparkle] found Sparkle.framework at $SPARKLE_FRAMEWORK_PATH"
  cp -R "$SPARKLE_FRAMEWORK_PATH" "$OUTPUT/Sparkle.framework"
else
  echo "[fetch_sparkle] ERROR: Sparkle.framework not found at expected path $SPARKLE_FRAMEWORK_PATH" >&2
  exit 1
fi

# Stage bin/sign_update and bin/generate_appcast for sparkle_sign.sh
if [[ -d "$TEMP_DIR/bin" ]]; then
  echo "[fetch_sparkle] staging sparkle-bin/ directory"
  mkdir -p "$OUTPUT/sparkle-bin"
  cp -R "$TEMP_DIR/bin/"* "$OUTPUT/sparkle-bin/" 2>/dev/null || true
fi

echo "[fetch_sparkle] done: $OUTPUT/Sparkle.framework"
