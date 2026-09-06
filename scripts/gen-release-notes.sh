#!/usr/bin/env bash
# Generate draft release notes from git log between two refs.
#
# Usage:
#   scripts/gen-release-notes.sh v0.1.0 HEAD
#   scripts/gen-release-notes.sh v0.1.0 v0.2.0 > docs/release-notes/v0.2.md
#
# Groups commits by conventional-commit type and emits a Markdown
# document suitable for dropping into docs/release-notes/. It's a
# starting point — the human still writes the headline + upgrade notes.

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <from-ref> [<to-ref>]" >&2
  echo "  from-ref: git ref to start from (exclusive), e.g. v0.1.0" >&2
  echo "  to-ref:   git ref to end at (inclusive), default HEAD" >&2
  exit 1
fi

from="$1"
to="${2:-HEAD}"

# Sanity: the from-ref must exist. If not, bail with a useful message.
if ! git rev-parse --verify "$from" >/dev/null 2>&1; then
  echo "error: ref '$from' does not exist" >&2
  echo "hint: list tags with 'git tag --list'" >&2
  exit 1
fi

to_sha=$(git rev-parse --short "$to")
from_sha=$(git rev-parse --short "$from")
today=$(date +%Y-%m-%d)

emit_section() {
  local title="$1"
  local pattern="$2"
  local lines
  lines=$(git log --pretty=format:'- %s (%h)' "$from..$to" | grep -E "$pattern" || true)
  if [[ -n "$lines" ]]; then
    echo
    echo "## $title"
    echo
    echo "$lines"
  fi
}

cat <<EOF
# Release notes (draft)

Generated $today from \`$from_sha..$to_sha\`.

This is a draft. Edit the headline, add upgrade notes, fill in
context that the commit subjects leave out, then commit to
\`docs/release-notes/\`.
EOF

emit_section "Features" '^- feat'
emit_section "Fixes" '^- fix'
emit_section "Documentation" '^- docs'
emit_section "Refactoring" '^- refactor'
emit_section "Tests" '^- test'
emit_section "Build and chores" '^- (chore|build|ci)'

# Anything that didn't match any prefix — worth showing so nothing is
# silently dropped from the notes.
misc=$(git log --pretty=format:'- %s (%h)' "$from..$to" \
  | grep -Ev '^- (feat|fix|docs|refactor|test|chore|build|ci)' || true)
if [[ -n "$misc" ]]; then
  echo
  echo "## Other"
  echo
  echo "$misc"
fi

echo
echo "---"
echo "Total commits: $(git rev-list --count "$from..$to")"
