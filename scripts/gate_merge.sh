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
#
# Exit status is the merge's own status, except: 64 for a usage error, and 65
# when the merge SUCCEEDED but its audit entry could not be completed (the
# mergeCommit OID or the repo slug could not be read, or no JSON encoder was
# available). 65 is deliberately not 0 -- an incomplete entry means
# check_merge_attribution.py will report that merge as unattributed, and the
# operator has to know before the checker tells them.

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
    # `gh --jq` prints the string "null" for an absent field, so an empty
    # result and a literal "null" both mean "not read".
    _read_field() {
        local _v
        _v="$("$@" 2>/dev/null || true)"
        if [ "$_v" = "null" ]; then
            _v=""
        fi
        printf '%s' "$_v"
    }

    MERGE_COMMIT="$(_read_field gh pr view "$PR_NUMBER" --json mergeCommit --jq '.mergeCommit.oid')"
    REPO="$(_read_field gh repo view --json nameWithOwner --jq '.nameWithOwner')"
    PR_NUM="$(_read_field gh pr view "$PR_NUMBER" --json number --jq '.number')"
    MERGED_BY="$(_read_field gh pr view "$PR_NUMBER" --json mergedBy --jq '.mergedBy.login')"
    [ -n "$PR_NUM" ] || PR_NUM="$PR_NUMBER"
    [ -n "$MERGED_BY" ] || MERGED_BY="unknown"
    TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

    # `sha` and `repo` are the two keys check_merge_attribution.py reconciles
    # on. A placeholder like "unknown" in either one is worse than an empty
    # field: the wrapper would exit 0, telling the operator the merge was
    # audited, while the checker still reports it as unattributed with nothing
    # pointing at the cause. Leave them empty (an empty `sha` can never stand
    # in as attribution) and say so loudly.
    AUDIT_RC=0
    if [ -z "$MERGE_COMMIT" ] || [ -z "$REPO" ]; then
        echo "ERROR: PR #${PR_NUMBER} merged, but its audit entry is INCOMPLETE:" >&2
        if [ -z "$MERGE_COMMIT" ]; then
            echo "       gh pr view --json mergeCommit returned no OID." >&2
        fi
        if [ -z "$REPO" ]; then
            echo "       gh repo view --json nameWithOwner returned no slug." >&2
        fi
        echo "       check_merge_attribution.py will report PR #${PR_NUMBER} as an" >&2
        echo "       unattributed merge until this entry is completed by hand." >&2
        AUDIT_RC=65
    fi

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
        AUDIT_RC=65
    fi

    # The merge succeeded; surface an incomplete audit entry in the exit code
    # so it cannot pass for a clean run.
    if [ "$AUDIT_RC" -ne 0 ]; then
        rc="$AUDIT_RC"
    fi
fi

exit "$rc"
