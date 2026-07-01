#!/bin/bash
# Enable the repo's local git hooks (currently: the documentation-drift
# gate). Idempotent -- safe to re-run.
#
# Usage: scripts/install-git-hooks.sh
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

git config core.hooksPath .githooks
chmod +x .githooks/pre-commit .githooks/commit-msg

echo "git hooks enabled: core.hooksPath = $(git config core.hooksPath)"
echo "  .githooks/pre-commit  -- runs the doc-gate against staged changes"
echo "  .githooks/commit-msg  -- accepts a 'Docs-Reviewed: <why>' trailer as an escape hatch"
echo "CI (.github/workflows/doc-gate.yml) enforces the same gate regardless of local hooks."
