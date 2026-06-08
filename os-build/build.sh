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
# Update ARMBIAN_TAG when you want to pick up a newer Armbian release.
# Tags are listed at: https://github.com/armbian/build/releases
# Verified against: https://github.com/armbian/build (tag v25.2 / 2025-02)
# ---------------------------------------------------------------------------
ARMBIAN_TAG="${ARMBIAN_TAG:-v25.2}"

# Clone Armbian build framework if not present, pinned to tag
if [ ! -d "$ARMBIAN_DIR" ]; then
    echo ">>> Cloning Armbian build framework (tag $ARMBIAN_TAG)..."
    git clone --depth 1 --branch "$ARMBIAN_TAG" https://github.com/armbian/build "$ARMBIAN_DIR"
    # Record the exact commit for reproducibility auditing
    echo ">>> Armbian build pinned to: $(git -C "$ARMBIAN_DIR" rev-parse HEAD)"
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
