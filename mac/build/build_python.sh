#!/usr/bin/env bash
# Build the embedded Python distribution from python-build-standalone.
#
# Args: --version <PYTHON_VER> --output <STAGING_DIR>
# Output: $STAGING_DIR/python/{bin,lib,...}
set -euo pipefail

PYTHON_VER=""
OUTPUT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) PYTHON_VER="$2"; shift 2 ;;
    --output) OUTPUT="$2"; shift 2 ;;
    *) echo "build_python.sh: unknown arg $1" >&2; exit 2 ;;
  esac
done

[[ -n "$PYTHON_VER" ]] || { echo "--version required" >&2; exit 2; }
[[ -n "$OUTPUT" ]] || { echo "--output required" >&2; exit 2; }

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CHECKSUM_FILE="$REPO_ROOT/mac/build/checksums/python-build-standalone.sha256"

# Latest stable release naming convention from astral-sh/python-build-standalone
TAG="20260414"
URL="https://github.com/astral-sh/python-build-standalone/releases/download/${TAG}/cpython-${PYTHON_VER}+${TAG}-aarch64-apple-darwin-install_only.tar.gz"

mkdir -p "$OUTPUT"
TARBALL="$OUTPUT/python-${PYTHON_VER}.tar.gz"

echo "[build_python] downloading $URL"
curl -L --fail -o "$TARBALL" "$URL"

EXPECTED_SHA="$(cat "$CHECKSUM_FILE")"
ACTUAL_SHA="$(shasum -a 256 "$TARBALL" | awk '{print $1}')"
if [[ "$EXPECTED_SHA" != "$ACTUAL_SHA" ]]; then
  echo "[build_python] SHA mismatch: expected $EXPECTED_SHA got $ACTUAL_SHA" >&2
  exit 1
fi

echo "[build_python] extracting"
mkdir -p "$OUTPUT/python"
tar -xzf "$TARBALL" -C "$OUTPUT/python" --strip-components=1
rm "$TARBALL"

PYBIN="$OUTPUT/python/bin/python3"
"$PYBIN" -m pip install --upgrade pip
"$PYBIN" -m pip install --no-deps -r "$REPO_ROOT/tinyagentos/requirements.lock"

echo "[build_python] done: $OUTPUT/python/bin/python3"
