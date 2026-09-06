#!/bin/bash
# Build TinyAgentOS image for a specific board
# Usage: ./build.sh [BOARD] [EXTRA_ARGS...]
#
# Examples:
#   ./build.sh orangepi5plus
#   ./build.sh rock5b BRANCH=current
#   ./build.sh                        # defaults to orangepi5plus

set -euo pipefail

BOARD="${1:-orangepi5plus}"
shift 2>/dev/null || true

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ARMBIAN_DIR="$SCRIPT_DIR/../armbian-build"

# ---------------------------------------------------------------------------
# Armbian build framework — pinned to a specific tag for reproducibility.
# Update ARMBIAN_TAG + ARMBIAN_COMMIT together when picking up a new release.
# Tags are listed at: https://github.com/armbian/build/releases
# Verified against: https://github.com/armbian/build (tag v25.2.1 / 2025-02)
# Commit SHA resolved 2026-06-08: git ls-remote --tags https://github.com/armbian/build v25.2.1
# ---------------------------------------------------------------------------
ARMBIAN_TAG="${ARMBIAN_TAG:-v25.2.1}"
# Immutable commit SHA for the tag above — guards against tag retargeting.
ARMBIAN_COMMIT="${ARMBIAN_COMMIT:-8e75c8ebd1e54f84cf55830de04f96937e388f9c}"

# Clone Armbian build framework if not present, pinned to tag
if [ ! -d "$ARMBIAN_DIR" ]; then
    echo ">>> Cloning Armbian build framework (tag $ARMBIAN_TAG)..."
    git clone --depth 1 --branch "$ARMBIAN_TAG" https://github.com/armbian/build "$ARMBIAN_DIR"
    # Verify the cloned commit matches the pinned SHA to detect tag retargeting.
    _armbian_actual="$(git -C "$ARMBIAN_DIR" rev-parse HEAD)"
    if [ "$_armbian_actual" != "$ARMBIAN_COMMIT" ]; then
        echo "ERROR: Armbian tag $ARMBIAN_TAG commit mismatch: expected $ARMBIAN_COMMIT, got $_armbian_actual" >&2
        echo "  Update ARMBIAN_COMMIT in build.sh if this upgrade is intentional." >&2
        exit 1
    fi
    echo ">>> Armbian build pinned to: $ARMBIAN_COMMIT (tag $ARMBIAN_TAG)"
fi

# Copy userpatches into the build tree
echo ">>> Copying TinyAgentOS userpatches..."
cp -r "$SCRIPT_DIR/userpatches" "$ARMBIAN_DIR/"

# Run the build
echo ">>> Building TinyAgentOS image for board: $BOARD"
cd "$ARMBIAN_DIR"
./compile.sh \
    BOARD="$BOARD" \
    BRANCH=vendor \
    RELEASE=bookworm \
    BUILD_DESKTOP=no \
    ENABLE_EXTENSIONS=tinyagentos \
    COMPRESS_OUTPUTIMAGE=sha,gpg,xz \
    "$@"
