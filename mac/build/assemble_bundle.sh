#!/usr/bin/env bash
# Build taOS.app/Contents/ from staging dirs.
#
# Args: --version <X.Y.Z> --staging <DIR> --launcher-binary <PATH> --output <DIR>
set -euo pipefail

VERSION=""
STAGING=""
LAUNCHER_BINARY=""
OUTPUT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) VERSION="$2"; shift 2 ;;
    --staging) STAGING="$2"; shift 2 ;;
    --launcher-binary) LAUNCHER_BINARY="$2"; shift 2 ;;
    --output) OUTPUT="$2"; shift 2 ;;
    *) echo "assemble_bundle.sh: unknown arg $1" >&2; exit 2 ;;
  esac
done

[[ -n "$VERSION" && -n "$STAGING" && -n "$LAUNCHER_BINARY" && -n "$OUTPUT" ]] \
  || { echo "all args required" >&2; exit 2; }

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APP="$OUTPUT/taOS.app"
CONTENTS="$APP/Contents"

rm -rf "$APP"
mkdir -p "$CONTENTS/MacOS" "$CONTENTS/Resources" "$CONTENTS/Frameworks"

# Info.plist
SU_PUBLIC_ED_KEY="$(cat "$REPO_ROOT/mac/appcast/ed_public.pem" | grep -v '^-----' | tr -d '\n')"
sed -e "s|\${VERSION}|$VERSION|g" \
    -e "s|\${SU_PUBLIC_ED_KEY}|$SU_PUBLIC_ED_KEY|g" \
    "$REPO_ROOT/mac/launcher/Sources/taOSLauncher/Resources/Info.plist.in" \
    > "$CONTENTS/Info.plist"

echo -n "APPL????" > "$CONTENTS/PkgInfo"

# Launcher binary
cp "$LAUNCHER_BINARY" "$CONTENTS/MacOS/taOS"
chmod +x "$CONTENTS/MacOS/taOS"

# Python distribution
cp -R "$STAGING/python" "$CONTENTS/Resources/python"

# taOS source tree
mkdir -p "$CONTENTS/Resources/taos"
cp -R "$REPO_ROOT/tinyagentos" "$CONTENTS/Resources/taos/tinyagentos"
find "$CONTENTS/Resources/taos" -type d -name __pycache__ -exec rm -rf {} +
find "$CONTENTS/Resources/taos" -type f -name "*.pyc" -delete
cp "$REPO_ROOT/pyproject.toml" "$CONTENTS/Resources/taos/pyproject.toml"

# Frontend
cp -R "$STAGING/frontend" "$CONTENTS/Resources/frontend"

# Apple container CLI
mkdir -p "$CONTENTS/Resources/bin"
cp "$STAGING/bin/container" "$CONTENTS/Resources/bin/container"
chmod +x "$CONTENTS/Resources/bin/container"

# Sparkle.framework — fetched/extracted by build.sh prior
if [[ -d "$STAGING/Sparkle.framework" ]]; then
  cp -R "$STAGING/Sparkle.framework" "$CONTENTS/Frameworks/Sparkle.framework"
fi

# AppIcon
if [[ -f "$STAGING/AppIcon.icns" ]]; then
  cp "$STAGING/AppIcon.icns" "$CONTENTS/Resources/AppIcon.icns"
fi

echo "[assemble_bundle] done: $APP"
