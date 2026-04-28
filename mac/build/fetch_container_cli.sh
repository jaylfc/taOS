#!/usr/bin/env bash
# Fetch and verify the apple/container CLI release tarball.
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

URL="https://github.com/apple/container/releases/download/${CLI_VER}/container-${CLI_VER}-arm64-macos.tar.gz"
mkdir -p "$OUTPUT/bin"
TARBALL="$OUTPUT/container-${CLI_VER}.tar.gz"

echo "[fetch_container_cli] downloading $URL"
curl -L --fail -o "$TARBALL" "$URL"

EXPECTED_SHA="$(cat "$CHECKSUM_FILE")"
ACTUAL_SHA="$(shasum -a 256 "$TARBALL" | awk '{print $1}')"
if [[ "$EXPECTED_SHA" != "$ACTUAL_SHA" ]]; then
  echo "[fetch_container_cli] SHA mismatch: expected $EXPECTED_SHA got $ACTUAL_SHA" >&2
  exit 1
fi

tar -xzf "$TARBALL" -C "$OUTPUT/bin" --strip-components=1 container
rm "$TARBALL"
chmod +x "$OUTPUT/bin/container"

echo "[fetch_container_cli] done: $OUTPUT/bin/container"
