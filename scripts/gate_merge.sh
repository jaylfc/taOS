#!/usr/bin/env bash
set -euo pipefail

AUDIT_LOG="${FLEET_AUDIT_LOG:-${HOME}/.fleet/merge-audit.jsonl}"
ACTOR="${FLEET_ACTOR:-${USER:-unknown}}"
SCRIPT_NAME="$(basename "$0")"

mkdir -p "$(dirname "$AUDIT_LOG")"

set +e
gh pr merge "$@"
rc=$?
set -e

if [ $rc -eq 0 ]; then
    PR_NUMBER="$(gh pr view --json number --jq '.number' 2>/dev/null || echo "0")"
    HEAD_SHA="$(gh pr view --json headRefOid --jq '.headRefOid' 2>/dev/null || echo "unknown")"
    MERGED_BY="$(gh pr view --json mergedBy --jq '.mergedBy.login' 2>/dev/null || echo "unknown")"
    REPO="$(gh repo view --json nameWithOwner --jq '.nameWithOwner' 2>/dev/null || echo "unknown")"
    TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

    echo "{\"actor\":\"${ACTOR}\",\"repo\":\"${REPO}\",\"pr\":${PR_NUMBER},\"sha\":\"${HEAD_SHA}\",\"merged_by\":\"${MERGED_BY}\",\"timestamp\":\"${TIMESTAMP}\",\"script\":\"${SCRIPT_NAME}\"}" >> "$AUDIT_LOG"
fi

exit $rc
