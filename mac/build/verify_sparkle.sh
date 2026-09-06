#!/usr/bin/env bash
# Verify Sparkle.framework is properly linked in the built app.
#
# Args: --app <PATH> --version <X.Y.Z> --output <DIR> [--release]
#   --release: Exit 1 if verification fails; otherwise warning only
#
# Usage: verify_sparkle.sh --app taOS.app --version 1.2.3 --output dist

set -euo pipefail

APP=""
VERSION=""
OUTPUT=""
RELEASE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --app) APP="$2"; shift 2 ;;
    --version) VERSION="$2"; shift 2 ;;
    --output) OUTPUT="$2"; shift 2 ;;
    --release) RELEASE=1 ;;
    *) echo "verify_sparkle.sh: unknown arg $1" >&2; exit 2 ;;
  esac
done

[[ -n "$APP" && -n "$VERSION" && -n "$OUTPUT" ]] || {
  echo "verify_sparkle.sh: --app, --version, and --output are required" >&2
  exit 2
}

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CONTENTS_FRAMEWORKS="$APP/Contents/Frameworks"

# Check 1: Sparkle.framework exists
if [[ ! -d "$CONTENTS_FRAMEWORKS/Sparkle.framework" ]]; then
  if [[ $RELEASE -eq 1 ]]; then
    echo "[verify_sparkle] ERROR: $CONTENTS_FRAMEWORKS/Sparkle.framework missing" >&2
    exit 1
  else
    echo "[verify_sparkle] WARNING: $CONTENTS_FRAMEWORKS/Sparkle.framework missing" >&2
  fi
fi

# Check 2: Sparkle binary exists within the framework
SPARKLE_BINARY="$CONTENTS_FRAMEWORKS/Sparkle.framework/Versions/B/Sparkle"
if [[ ! -f "$SPARKLE_BINARY" ]]; then
  if [[ $RELEASE -eq 1 ]]; then
    echo "[verify_sparkle] ERROR: Sparkle binary not found at $SPARKLE_BINARY" >&2
    exit 1
  else
    echo "[verify_sparkle] WARNING: Sparkle binary not found at $SPARKLE_BINARY" >&2
  fi
fi

# Check 3: otool shows correct linking (check if otool is available)
if command -v otool >/dev/null 2>&1; then
  LAUNCHER_BINARY="$APP/Contents/MacOS/taOSLauncher"
  if [[ -f "$LAUNCHER_BINARY" ]]; then
    # Check for @rpath/Sparkle.framework/Versions/B/Sparkle in otool -L
    if ! otool -L "$LAUNCHER_BINARY" | grep -q "@rpath/Sparkle.framework/Versions/B/Sparkle"; then
      if [[ $RELEASE -eq 1 ]]; then
        echo "[verify_sparkle] ERROR: $LAUNCHER_BINARY not linked to Sparkle.framework" >&2
        exit 1
      else
        echo "[verify_sparkle] WARNING: $LAUNCHER_BINARY not linked to Sparkle.framework" >&2
      fi
    fi

    # Check for LC_RPATH @executable_path/../Frameworks in otool -l
    if ! otool -l "$LAUNCHER_BINARY" 2>/dev/null | grep -q "LC_RPATH.*@executable_path/../Frameworks"; then
      if [[ $RELEASE -eq 1 ]]; then
        echo "[verify_sparkle] ERROR: $LAUNCHER_BINARY missing LC_RPATH @executable_path/../Frameworks" >&2
        exit 1
      else
        echo "[verify_sparkle] WARNING: $LAUNCHER_BINARY missing LC_RPATH @executable_path/../Frameworks" >&2
      fi
    fi
  fi
else
  if [[ $RELEASE -eq 1 ]]; then
    echo "[verify_sparkle] ERROR: otool not found — cannot verify binary linking" >&2
    exit 1
  else
    echo "[verify_sparkle] WARNING: otool not found — skipping binary linking checks" >&2
  fi
fi

echo "[verify_sparkle] verification passed"
