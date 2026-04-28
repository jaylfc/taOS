#!/usr/bin/env bash
# Sparkle-sign the DMG and produce an appcast snippet.
#
# Args: --dmg <PATH> --version <X.Y.Z> --output <DIR>
set -euo pipefail

DMG=""
VERSION=""
OUTPUT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dmg) DMG="$2"; shift 2 ;;
    --version) VERSION="$2"; shift 2 ;;
    --output) OUTPUT="$2"; shift 2 ;;
    *) echo "sparkle_sign.sh: unknown arg $1" >&2; exit 2 ;;
  esac
done

[[ -n "$DMG" && -n "$VERSION" && -n "$OUTPUT" ]] || { echo "all args required" >&2; exit 2; }

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PRIVATE_KEY="${SPARKLE_ED_PRIVATE_KEY:-$HOME/.taos/sparkle_ed_private.pem}"
[[ -f "$PRIVATE_KEY" ]] || { echo "Sparkle private key not at $PRIVATE_KEY" >&2; exit 1; }

# Locate Sparkle's sign_update tool — bundled with Sparkle release tarball
SIGN_UPDATE="$(command -v sign_update || true)"
if [[ -z "$SIGN_UPDATE" ]]; then
  for c in "$REPO_ROOT/mac/build/staging/Sparkle.framework/Versions/B/Resources/sign_update" \
           "/Applications/Sparkle.framework/Versions/B/Resources/sign_update"; do
    [[ -x "$c" ]] && { SIGN_UPDATE="$c"; break; }
  done
fi
[[ -n "$SIGN_UPDATE" ]] || { echo "sign_update tool not found" >&2; exit 1; }

SIGNATURE_LINE="$("$SIGN_UPDATE" "$DMG" "$PRIVATE_KEY")"
# sign_update prints e.g.:  sparkle:edSignature="..." length="N"
echo "[sparkle_sign] $SIGNATURE_LINE"

# Write the .sig sidecar
SIG_FIELD="$(echo "$SIGNATURE_LINE" | sed -nE 's/.*sparkle:edSignature="([^"]+)".*/\1/p')"
echo -n "$SIG_FIELD" > "${DMG}.sig"

# Build the appcast snippet
mkdir -p "$OUTPUT"
SNIPPET="$OUTPUT/appcast-snippet.xml"
NOTES_FILE="$REPO_ROOT/CHANGELOG.md"
NOTES="$(awk -v v="$VERSION" 'BEGIN{p=0} /^## /{p=($2==v)} p' "$NOTES_FILE" 2>/dev/null || echo "")"

cat > "$SNIPPET" <<XML
    <item>
      <title>v${VERSION}</title>
      <sparkle:version>${VERSION}</sparkle:version>
      <sparkle:minimumSystemVersion>26.0</sparkle:minimumSystemVersion>
      <description><![CDATA[${NOTES}]]></description>
      <enclosure
        url="https://taos.app/releases/$(basename "$DMG")"
        ${SIGNATURE_LINE}
        type="application/octet-stream"/>
    </item>
XML

echo "[sparkle_sign] done: $SNIPPET"
