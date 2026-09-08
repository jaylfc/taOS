#!/usr/bin/env bash
# Tests for Sparkle appcast snippet generation.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT="$REPO_ROOT/mac/build/sparkle_sign.sh"

# Resolve current version from CHANGELOG.md so the test stays correct across releases.
CURRENT_VERSION="$(grep -m1 '^## \[.*\] - ' "$REPO_ROOT/CHANGELOG.md" | sed 's/.*\[\(.*\)\].*/\1/')"

setup_fakes() {
  TMPDIR="$(mktemp -d)"
  DMG="$TMPDIR/test.dmg"
  touch "$DMG"
  KEY="$TMPDIR/key.pem"
  printf '%s\n' "fake-key" > "$KEY"
  OUTPUT="$TMPDIR/output"
  mkdir -p "$OUTPUT"
  export SPARKLE_ED_PRIVATE_KEY="$KEY"
  # Provide a stub sign_update so sparkle_sign.sh can produce a snippet.
  SIGN_STUB="$TMPDIR/sign_update"
  cat > "$SIGN_STUB" <<'SIGN'
#!/usr/bin/env bash
echo 'sparkle:edSignature="fake-ed-signature" length="1234"'
SIGN
  chmod +x "$SIGN_STUB"
  export PATH="$TMPDIR:$PATH"
}

cleanup() {
  rm -rf "${TMPDIR:-}"
}
trap cleanup EXIT

echo "test: the appcast description is non-empty for the current version"
setup_fakes
"$SCRIPT" --dmg "$DMG" --version "$CURRENT_VERSION" --output "$OUTPUT"
SNIPPET="$OUTPUT/appcast-snippet.xml"

DESCRIPTION="$(python3 -c "
import re
with open('$SNIPPET') as f:
    content = f.read()
m = re.search(r'<description><!\[CDATA\[(.*?)\]\]></description>', content, re.DOTALL)
print(m.group(1) if m else '')
")"

if [[ -z "$DESCRIPTION" ]]; then
  echo "FAIL: <description><![CDATA[]]></description>"
  echo "  expected the \"## [$CURRENT_VERSION]\" section body"
  exit 1
fi

echo "test: a CDATA terminator in the changelog does not break the XML"
NOTES="$TMPDIR/NOTES.md"
cat > "$NOTES" <<EOF
## [$CURRENT_VERSION] - 2026-08-21

Release notes with a ]]> CDATA terminator inside.
EOF

rm -rf "$OUTPUT"
mkdir -p "$OUTPUT"
"$SCRIPT" --dmg "$DMG" --version "$CURRENT_VERSION" --output "$OUTPUT" --notes-file "$NOTES"

python3 -c "
import xml.etree.ElementTree as ET
with open('$SNIPPET') as f:
    xml = f.read()
xml = xml.replace('<item>', '<item xmlns:sparkle=\"http://www.andymatuschak.org/xml-namespaces/sparkle\">')
ET.fromstring(xml)
"

echo "all tests passed"
