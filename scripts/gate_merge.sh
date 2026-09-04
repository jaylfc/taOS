#!/usr/bin/env bash
set -uo pipefail

# Usage: gate_merge.sh [--check] <pr_number> [card_id] ["optional note"]
#
# Fleet merge wrapper that appends an audit entry for every merge.
#
# Delegates the merge + safety checks (lead-blocked, CI, red-proof, vitest)
# to ~/.taos-team/gate_merge.sh when that file exists, so adopting this
# wrapper never silently drops the gate. Falls back to a bare `gh pr merge`
# only when the fleet gate is absent (CI / tests with HOME isolated).
#
# After a successful merge (never in --check mode), the audit entry records
# the mergeCommit from `gh pr view <n> --json mergeCommit` -- the key both
# the producer and the checker observe. For a squash merge this is the new
# commit on the base branch; for a merge commit it is the merge-commit SHA.
# Both the PR number and the mergeCommit come from the GitHub API, not from
# the local branch.

# --- argument parsing (strict: unknown flags abort) ---
CHECK_ONLY=0
_posargs=()
for _a in "$@"; do
    case "$_a" in
        --check) CHECK_ONLY=1 ;;
        --*)     echo "ABORT: unknown flag '$_a'. Refusing to run rather than ignore it." >&2
                 exit 64 ;;
        *)       _posargs+=("$_a") ;;
    esac
done
set -- "${_posargs[@]+"${_posargs[@]}"}"
PR_NUMBER="${1:-}"
CARD_ID="${2:-}"
NOTE="${3:-}"

if [ -z "$PR_NUMBER" ]; then
    echo "usage: gate_merge.sh [--check] <pr_number> [card_id] [\"optional note\"]"
    exit 64
fi

AUDIT_LOG="${FLEET_AUDIT_LOG:-${HOME}/.fleet/merge-audit.jsonl}"
ACTOR="${FLEET_ACTOR:-${USER:-unknown}}"
SCRIPT_NAME="$(basename "$0")"
FLEET_GATE="${HOME}/.taos-team/gate_merge.sh"
GATE_REPO="${GATE_REPO:-}"

mkdir -p "$(dirname "$AUDIT_LOG")" 2>/dev/null || true

# --- --check: delegate or warn, no audit logging ---
if [ "$CHECK_ONLY" = "1" ]; then
    if [ -x "$FLEET_GATE" ]; then
        "$FLEET_GATE" --check "$PR_NUMBER" "$CARD_ID" "$NOTE"
        exit $?
    fi
    echo "GATE PASS (check-only): no fleet gate at $FLEET_GATE; cannot pre-check safety."
    exit 0
fi

# --- merge + safety checks ---
set +e
if [ -x "$FLEET_GATE" ]; then
    # Delegate the entire merge (lead-blocked, CI, red-proof checks) to the
    # real fleet gate. We only add the audit-log entry afterwards.
    "$FLEET_GATE" "$PR_NUMBER" "$CARD_ID" "$NOTE"
    rc=$?
else
    # Fallback: bare gh pr merge. Does NOT perform safety checks.
    echo "WARN: $FLEET_GATE not found; falling back to bare gh pr merge." >&2
    echo "      Safety checks (lead-blocked, CI, red-proof) are NOT performed." >&2
    if [ -n "$GATE_REPO" ]; then
        gh pr merge "$PR_NUMBER" --repo "$GATE_REPO" --merge --admin
    else
        gh pr merge "$PR_NUMBER" --merge --admin
    fi
    rc=$?
fi
set -e

# --- audit logging (only on a real merge) ---
if [ "$rc" -eq 0 ]; then
    # mergeCommit from the API -- the OID that the checker also reads.
    # For a squash merge this is the new commit on the base branch.
    MERGE_COMMIT="$(gh pr view "$PR_NUMBER" --json mergeCommit --jq '.mergeCommit.oid' 2>/dev/null || echo "unknown")"
    PR_NUM="$(gh pr view "$PR_NUMBER" --json number --jq '.number' 2>/dev/null || echo "$PR_NUMBER")"
    MERGED_BY="$(gh pr view "$PR_NUMBER" --json mergedBy --jq '.mergedBy.login' 2>/dev/null || echo "unknown")"
    REPO="$(gh repo view --json nameWithOwner --jq '.nameWithOwner' 2>/dev/null || echo "unknown")"
    TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

    # Encode with a real JSON encoder. ACTOR comes from the caller
    # (FLEET_ACTOR/USER) and MERGED_BY/REPO come from the API, so
    # interpolating them into a hand-written `{...}` lets a single quote or
    # backslash emit a line the checker's JSON parser cannot read -- and an
    # unreadable audit line is an unattributed merge.
    if command -v jq >/dev/null 2>&1; then
        AUDIT_LINE="$(jq -cn \
            --arg actor "$ACTOR" \
            --arg repo "$REPO" \
            --arg pr "$PR_NUM" \
            --arg sha "$MERGE_COMMIT" \
            --arg merged_by "$MERGED_BY" \
            --arg timestamp "$TIMESTAMP" \
            --arg script "$SCRIPT_NAME" \
            '{actor:$actor,repo:$repo,pr:(($pr|tonumber?) // $pr),sha:$sha,merged_by:$merged_by,timestamp:$timestamp,script:$script}')"
    else
        AUDIT_LINE="$(
            AUDIT_ACTOR="$ACTOR" AUDIT_REPO="$REPO" AUDIT_PR="$PR_NUM" \
            AUDIT_SHA="$MERGE_COMMIT" AUDIT_MERGED_BY="$MERGED_BY" \
            AUDIT_TIMESTAMP="$TIMESTAMP" AUDIT_SCRIPT="$SCRIPT_NAME" \
            python3 -c 'import json, os
pr = os.environ["AUDIT_PR"]
try:
    pr = int(pr)
except ValueError:
    pass
print(json.dumps({
    "actor": os.environ["AUDIT_ACTOR"],
    "repo": os.environ["AUDIT_REPO"],
    "pr": pr,
    "sha": os.environ["AUDIT_SHA"],
    "merged_by": os.environ["AUDIT_MERGED_BY"],
    "timestamp": os.environ["AUDIT_TIMESTAMP"],
    "script": os.environ["AUDIT_SCRIPT"],
}, separators=(",", ":")))'
        )"
    fi

    if [ -n "$AUDIT_LINE" ]; then
        printf '%s\n' "$AUDIT_LINE" >> "$AUDIT_LOG"
    else
        # Fail loud and closed: with no audit line the checker reports this
        # merge as unattributed, which is the correct outcome, but the
        # operator needs to know why.
        echo "ERROR: could not encode the audit entry (neither jq nor python3 is available)." >&2
        echo "       PR #${PR_NUMBER} merged but is UNATTRIBUTED in ${AUDIT_LOG}." >&2
    fi
fi

exit "$rc"
