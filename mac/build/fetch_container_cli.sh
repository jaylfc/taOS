#!/usr/bin/env bash
# Fetch and verify the apple/container CLI release pkg, extract the
# bundled binary + libexec plugins.
#
# Args: --version <CLI_VER> --output <STAGING_DIR>
set -euo pipefail

CLI_VER=""
OUTPUT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) CLI_VER="$2"; shift 2 ;;
    --output) OUTPUT="$2"; shift 2 ;;
    *) echo "fetch_container_cli.sh: unknown arg $1" >&2; exit 2 ;;
  esac
done

[[ -n "$CLI_VER" ]] || { echo "--version required" >&2; exit 2; }
[[ -n "$OUTPUT" ]] || { echo "--output required" >&2; exit 2; }

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CHECKSUM_FILE="$REPO_ROOT/mac/build/checksums/apple-container-cli.sha256"

URL="https://github.com/apple/container/releases/download/${CLI_VER}/container-${CLI_VER}-installer-signed.pkg"
mkdir -p "$OUTPUT"
PKG="$OUTPUT/container-${CLI_VER}.pkg"

echo "[fetch_container_cli] downloading $URL"
curl -L --fail -o "$PKG" "$URL"

EXPECTED_SHA="$(cat "$CHECKSUM_FILE")"
ACTUAL_SHA="$(shasum -a 256 "$PKG" | awk '{print $1}')"
if [[ "$EXPECTED_SHA" != "$ACTUAL_SHA" ]]; then
  echo "[fetch_container_cli] SHA mismatch: expected $EXPECTED_SHA got $ACTUAL_SHA" >&2
  exit 1
fi

WORK="$OUTPUT/.container-extract"
rm -rf "$WORK"
mkdir -p "$WORK"

echo "[fetch_container_cli] extracting pkg"
(cd "$WORK" && xar -xf "$PKG")
mkdir -p "$WORK/payload"
(cd "$WORK/payload" && gunzip -dc "$WORK/Payload" | cpio -i --quiet)

mkdir -p "$OUTPUT/bin" "$OUTPUT/libexec"
cp "$WORK/payload/bin/container" "$OUTPUT/bin/container"
chmod +x "$OUTPUT/bin/container"
cp -R "$WORK/payload/libexec/container" "$OUTPUT/libexec/container"

rm -rf "$WORK" "$PKG"

echo "[fetch_container_cli] done: $OUTPUT/bin/container"
