#!/usr/bin/env bash
# Orchestrate the full build: launcher, python, frontend, container CLI,
# bundle, sign, DMG, sparkle-sign, notarize.
#
# Args:
#   --version <X.Y.Z>
#   --python-version <PYVER>
#   --container-cli-version <CLIVER>
#   --output <DIR>
set -euo pipefail

VERSION=""
PYTHON_VER="3.12.7"
CLI_VER="0.5.0"
OUTPUT="dist"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) VERSION="$2"; shift 2 ;;
    --python-version) PYTHON_VER="$2"; shift 2 ;;
    --container-cli-version) CLI_VER="$2"; shift 2 ;;
    --output) OUTPUT="$2"; shift 2 ;;
    *) echo "build.sh: unknown arg $1" >&2; exit 2 ;;
  esac
done

[[ -n "$VERSION" ]] || { echo "--version required" >&2; exit 2; }

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT_DIR="$REPO_ROOT/mac/build"
STAGING="$REPO_ROOT/$OUTPUT/staging"

echo "[build] env validation"
command -v swift >/dev/null || { echo "swift not found" >&2; exit 1; }
command -v create-dmg >/dev/null || { echo "create-dmg not found (brew install create-dmg)" >&2; exit 1; }

mkdir -p "$REPO_ROOT/$OUTPUT"
rm -rf "$STAGING"
mkdir -p "$STAGING"

echo "[build] (1/9) launcher"
cd "$REPO_ROOT/mac/launcher"
swift build -c release --arch arm64
LAUNCHER_BINARY="$REPO_ROOT/mac/launcher/.build/arm64-apple-macosx/release/taOSLauncher"
cd "$REPO_ROOT"

echo "[build] (2/9) python"
"$SCRIPT_DIR/build_python.sh" --version "$PYTHON_VER" --output "$STAGING"

echo "[build] (3/9) frontend"
"$SCRIPT_DIR/build_frontend.sh" --output "$STAGING"

echo "[build] (4/9) container CLI"
"$SCRIPT_DIR/fetch_container_cli.sh" --version "$CLI_VER" --output "$STAGING"

echo "[build] (5/9) assemble bundle"
"$SCRIPT_DIR/assemble_bundle.sh" \
    --version "$VERSION" \
    --staging "$STAGING" \
    --launcher-binary "$LAUNCHER_BINARY" \
    --output "$REPO_ROOT/$OUTPUT"

APP="$REPO_ROOT/$OUTPUT/taOS.app"

echo "[build] (6/9) sign"
"$SCRIPT_DIR/sign.sh" --app "$APP"

echo "[build] (7/9) package DMG"
"$SCRIPT_DIR/package_dmg.sh" --app "$APP" --version "$VERSION" --output "$REPO_ROOT/$OUTPUT"
DMG="$REPO_ROOT/$OUTPUT/taOS-$VERSION.dmg"

echo "[build] (8/9) notarize"
"$SCRIPT_DIR/notarize.sh" --dmg "$DMG"

echo "[build] (9/9) sparkle-sign"
"$SCRIPT_DIR/sparkle_sign.sh" --dmg "$DMG" --version "$VERSION" --output "$REPO_ROOT/$OUTPUT"

# Clean staging on success
rm -rf "$STAGING"

echo "[build] done"
echo "  app: $APP"
echo "  dmg: $DMG"
echo "  sig: ${DMG}.sig"
echo "  appcast snippet: $REPO_ROOT/$OUTPUT/appcast-snippet.xml"
