#!/usr/bin/env bash
# Bump version across Info.plist.in, pyproject.toml, frontend/package.json, CHANGELOG.md.
#
# Usage: bump_version.sh <NEW_VERSION>
set -euo pipefail

NEW_VER="${1:-}"
[[ -n "$NEW_VER" ]] || { echo "usage: bump_version.sh <X.Y.Z>" >&2; exit 2; }

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

# Info.plist.in: replace ${VERSION} marker is left for assemble_bundle. Real version in CFBundleShortVersionString is templated. We bump a sidecar file to track.
echo "$NEW_VER" > mac/build/.version

# pyproject.toml: update version = "..."
sed -i.bak -E "s/^version = \".*\"/version = \"$NEW_VER\"/" pyproject.toml
rm -f pyproject.toml.bak

# frontend/package.json
node -e "const f='frontend/package.json'; const j=require('./'+f); j.version='$NEW_VER'; require('fs').writeFileSync(f, JSON.stringify(j,null,2)+'\n');"

# CHANGELOG.md: prepend a new section if not present
if ! grep -q "^## $NEW_VER" CHANGELOG.md 2>/dev/null; then
  TMP="$(mktemp)"
  {
    echo "## $NEW_VER"
    echo ""
    echo "- TODO: fill in release notes"
    echo ""
    [[ -f CHANGELOG.md ]] && cat CHANGELOG.md
  } > "$TMP"
  mv "$TMP" CHANGELOG.md
fi

echo "[bump_version] -> $NEW_VER"
