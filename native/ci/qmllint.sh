#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# qmllint over the shell and app QML with the project's import paths. Errors fail
# the run; warnings are listed and counted (the tree has many, mostly unqualified
# access, so they are reported, not gated).
#   QMLLINT=<path to qmllint> native/ci/qmllint.sh [log file]
set -uo pipefail
cd "$(dirname "$0")/.."
QMLLINT=${QMLLINT:-qmllint}
LOG=${1:-qmllint.log}
files=$(find apps shell qt -name '*.qml' -not -path '*/tests/*' -not -path '*/build*' | sort)
# shellcheck disable=SC2086
"$QMLLINT" -I qt/style -I apps/common/qml -I apps/activity/qml $files >"$LOG" 2>&1
errors=$(grep -c '^Error:' "$LOG" || true)
warnings=$(grep -c '^Warning:' "$LOG" || true)
echo "qmllint: $(echo "$files" | wc -l) files, $errors error(s), $warnings warning(s) (full log: $LOG)"
grep '^Error:' "$LOG" | sed 's/^/  /' || true
[ "$errors" -eq 0 ]
