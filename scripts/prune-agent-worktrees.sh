#!/usr/bin/env bash
# Prune stale agent worktrees under .claude/worktrees/ safely.
#
# The Agent tool creates a git worktree per isolated subagent and auto-removes
# it only when it is unchanged. Worktrees that accumulated commits persist and
# pile up. This prunes them, but NEVER destroys work: a worktree is removed only
# when its branch is either already on origin (pushed) or merged into
# origin/master. A worktree whose branch is unpushed AND unmerged is left in
# place and reported, because it may hold work that exists nowhere else. Locked
# worktrees are respected and skipped.
#
# Usage:
#   scripts/prune-agent-worktrees.sh            # prune safe worktrees
#   scripts/prune-agent-worktrees.sh --dry-run  # report only, remove nothing
set -euo pipefail

DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

cd "$(git rev-parse --show-toplevel)"
git fetch origin master --quiet 2>/dev/null || true

removed=0
kept_wip=0
kept_locked=0

# Walk worktrees via the porcelain form: each record is a "worktree <path>"
# line, optionally followed by "branch refs/heads/<name>" and "locked".
path=""; branch=""; locked=0
flush() {
    [ -z "$path" ] && return
    case "$path" in
        */.claude/worktrees/*) ;;   # only touch agent worktrees
        *) path=""; branch=""; locked=0; return ;;
    esac
    if [ "$locked" = "1" ]; then
        echo "SKIP  (locked)        $path"
        kept_locked=$((kept_locked + 1))
        path=""; branch=""; locked=0; return
    fi
    local safe=0 reason=""
    if [ -n "$branch" ]; then
        if git ls-remote --heads origin "$branch" | grep -q .; then
            safe=1; reason="pushed to origin"
        elif git merge-base --is-ancestor "refs/heads/$branch" origin/master 2>/dev/null; then
            safe=1; reason="merged to master"
        fi
    else
        # Detached worktree with no branch; nothing unique to lose.
        safe=1; reason="no branch"
    fi
    if [ "$safe" = "1" ]; then
        if [ "$DRY_RUN" = "1" ]; then
            echo "PRUNE ($reason)    $path"
        else
            git worktree remove --force "$path" && echo "REMOVED ($reason)  $path"
        fi
        removed=$((removed + 1))
    else
        echo "KEEP  (unpushed+unmerged WIP) $path [$branch]"
        kept_wip=$((kept_wip + 1))
    fi
    path=""; branch=""; locked=0
}

while IFS= read -r line; do
    case "$line" in
        "worktree "*) flush; path="${line#worktree }" ;;
        "branch refs/heads/"*) branch="${line#branch refs/heads/}" ;;
        "locked"*) locked=1 ;;
        "") : ;;
    esac
done < <(git worktree list --porcelain)
flush

[ "$DRY_RUN" = "0" ] && git worktree prune

echo "---"
echo "pruned=$removed  kept_wip=$kept_wip  kept_locked=$kept_locked"
