#!/usr/bin/env bash
# red_first.sh <repo> <pr> <src-path>... -- <test-cmd>
# Mechanizes the pass/fail/pass cycle. Spec: @taOS-dev tsk-3oaabh part 2 of 3,
# relayed verbatim on the bus at 1854; implementation notes from the same message
# so the fleet keeps ONE definition of red-first.
#
# WHAT IT GUARDS. The tests-green-through-the-regression class: on taOS,
# reintroducing a closed PR's exact defect left 87 tests green because the test
# helper bypassed the layer the fix lives in. A test that passes with the fix
# reverted is not testing the fix.
#
# CYCLE, in a throwaway worktree at the PR head:
#   1. run test-cmd            -> must PASS
#   2. revert ONLY the named source paths to the MERGE-BASE, rerun -> must FAIL
#   3. restore, rerun          -> must PASS
# Reverting to the merge-base (not the base branch tip) keeps unrelated drift on
# main out of the experiment, and checking out only the named paths leaves the
# PR's own tests in place, which is the whole point.
#
# Exit codes are deliberately distinct so a wrong-shape outcome can never read as
# a pass, and the first line is always SUCCESS:/FAILED: so a timeout cannot either.
#   0 = pass/fail/pass, the tests genuinely exercise the change
#   2 = usage error (includes a named path that exists at NEITHER end)
#   3 = head run FAILED (the PR is broken before we start; nothing was learned)
#   4 = revert run PASSED  <-- THE DANGEROUS ONE: tests are decorative
#   5 = restore run FAILED (the script left the tree dirty; do not trust run 2)
#   6 = setup failure (fetch/worktree/merge-base unreadable) -- FAIL CLOSED
#   7 = NOT APPLICABLE: no named source moved between merge-base and head, so
#       this cycle cannot be run at all. NOT a verdict about the tests.
#   8 = RED, BUT SUITE-LEVEL: the cycle passed and the red is not attributable to
#       any particular test. NOT a failure, and NOT proof about a named test.
#
# WHY 8 EXISTS, and it cost a real miss. `run()` evaluates ONE test command and
# judges the whole invocation by its exit code, so A SUITE-LEVEL RED IS SATISFIABLE
# BY A TEST OTHER THAN THE ONE THE ACCEPTANCE CRITERION IS ABOUT. On PR #146 I named
# three source paths and a suite command: other tests genuinely went red, the cycle
# returned 0, and the one test named for the property -- which passed against the
# correct AND the broken implementation alike -- hid behind its neighbours. The tool
# proved "this SUITE exercises this change" and I read it as "this TEST does".
#
# Discharge it by naming ONE test, one invocation per property. Pass --suite to
# accept the weaker verdict deliberately; that is a legitimate choice, but it must
# be a CHOICE and not the default reading of a 0.
#
# HONEST LIMIT OF THE DETECTION: this pattern-matches the command for a per-test
# selector. It does not understand the test runner, and a selector that matches many
# tests still reads as specific. It cannot be fooled in the direction that matters
# (a bare suite command is never mistaken for a named test), which is the only
# direction that produced the miss.
set -uo pipefail
[ "${1:-}" = "--help" ] && { echo "usage: red_first.sh [--suite] <repo> <pr> <src-path>... -- <test-cmd>"; exit 0; }
SUITE_OK=0
[ "${1:-}" = "--suite" ] && { SUITE_OK=1; shift; }
REPO="${1:-}"; PR="${2:-}"; shift 2 2>/dev/null || { echo "FAILED: usage: red_first.sh [--suite] <repo> <pr> <src-path>... -- <test-cmd>"; exit 2; }
SRC=(); while [ $# -gt 0 ] && [ "$1" != "--" ]; do SRC+=("$1"); shift; done
[ "${1:-}" = "--" ] && shift
TEST_CMD="$*"
[ -z "$REPO" ] || [ -z "$PR" ] || [ ${#SRC[@]} -eq 0 ] || [ -z "$TEST_CMD" ] && {
  echo "FAILED: usage: red_first.sh <repo> <pr> <src-path>... -- <test-cmd>"; exit 2; }

WT="$(mktemp -d -p "${TMPDIR:-/tmp}" redfirst-XXXXXX)"
cleanup(){ git worktree remove --force "$WT" >/dev/null 2>&1; git worktree prune >/dev/null 2>&1; }
trap cleanup EXIT

git fetch -q origin "refs/pull/$PR/head:redfirst-$PR" --force 2>/dev/null || { echo "FAILED: cannot fetch PR #$PR"; exit 6; }
BASE_REF="$(gh pr view "$PR" --repo "$REPO" --json baseRefName -q .baseRefName 2>/dev/null)"
[ -z "$BASE_REF" ] && { echo "FAILED: cannot read base ref for #$PR"; exit 6; }
git fetch -q origin "$BASE_REF" 2>/dev/null
MB="$(git merge-base "origin/$BASE_REF" "redfirst-$PR" 2>/dev/null)"
[ -z "$MB" ] && { echo "FAILED: cannot compute merge-base of origin/$BASE_REF and PR head"; exit 6; }
git worktree add -q --detach "$WT" "redfirst-$PR" 2>/dev/null || { echo "FAILED: cannot create worktree"; exit 6; }

run(){ ( cd "$WT" && eval "$TEST_CMD" ) >"$WT/.rf.log" 2>&1; return $?; }

# --- CLASSIFY THE NAMED PATHS BEFORE RUNNING ANYTHING ----------------------
# NO SOURCE MOVED is not the same as DECORATIVE TESTS, and conflating the two is
# how PR #144 -- a correct test-only build restoring a dropped test -- was told
# "the tests do NOT exercise this change". The revert was a no-op, so run 2
# passed, so the tool blamed the tests for the tool's own inability to express
# the experiment. A demand that cannot be satisfied is malformed; it needs its
# own exit code, not the nearest-looking verdict.
REVERT=(); NEWPATH=(); MISSING=()
for p in "${SRC[@]}"; do
  at_mb=0; at_head=0
  git cat-file -e "$MB:$p" 2>/dev/null && at_mb=1
  git cat-file -e "redfirst-$PR:$p" 2>/dev/null && at_head=1
  if [ $at_mb -eq 0 ] && [ $at_head -eq 0 ]; then MISSING+=("$p")
  elif [ $at_mb -eq 0 ]; then NEWPATH+=("$p")
  else REVERT+=("$p"); fi
done
# A typo must not be absorbed by exit 7 as "not applicable" -- that would turn a
# mistyped path into a clean-looking outcome, which is the failure mode this
# whole exit code exists to prevent.
if [ ${#MISSING[@]} -gt 0 ]; then
  echo "FAILED: named path(s) exist at NEITHER the merge-base nor the PR head: ${MISSING[*]}"
  echo "        Nothing to revert; check the path spelling against the PR's file list."
  exit 2
fi
# The PR ADDS these files, so the correct revert is to DELETE them. `git checkout
# <rev> -- <path>` cannot express a deletion, and it fails the WHOLE invocation,
# so one added file among N named paths used to kill an otherwise valid run.
if ! git diff --quiet "$MB" "redfirst-$PR" -- "${SRC[@]}" 2>/dev/null; then
  :
else
  echo "  merge-base ${MB:0:8} | named paths: ${SRC[*]}"
  echo "NOT APPLICABLE: none of the named source paths differ between the merge-base"
  echo "        and the PR head, so there is nothing to revert and the pass/fail/pass"
  echo "        cycle does not apply. This is NOT a finding about the tests."
  echo "        A test-only PR satisfies red-first by mutating the source the test"
  echo "        covers and pasting that run, not by reverting a change it never made."
  exit 7
fi

run; head_rc=$?
if [ $head_rc -ne 0 ]; then
  echo "FAILED: run 1 (PR head) FAILED rc=$head_rc. The PR is red before the experiment starts."
  tail -5 "$WT/.rf.log" | sed 's/^/    /'
  exit 3
fi

if [ ${#REVERT[@]} -gt 0 ]; then
  ( cd "$WT" && git checkout -q "$MB" -- "${REVERT[@]}" ) 2>/dev/null || {
    echo "FAILED: cannot revert named paths to merge-base ${MB:0:8}: ${REVERT[*]}"; exit 6; }
fi
if [ ${#NEWPATH[@]} -gt 0 ]; then
  ( cd "$WT" && rm -rf -- "${NEWPATH[@]}" ) || {
    echo "FAILED: cannot delete PR-added paths: ${NEWPATH[*]}"; exit 6; }
fi
run; rev_rc=$?

( cd "$WT" && git checkout -q "redfirst-$PR" -- "${SRC[@]}" ) 2>/dev/null || { echo "FAILED: cannot restore paths"; exit 5; }
run; res_rc=$?

echo "  merge-base ${MB:0:8} | reverted: ${REVERT[*]:-none}${NEWPATH:+ | deleted (added by this PR): ${NEWPATH[*]}}"
echo "  run 1 head    rc=$head_rc  (want pass)"
echo "  run 2 reverted rc=$rev_rc  (want FAIL)"
echo "  run 3 restored rc=$res_rc  (want pass)"

if [ $rev_rc -eq 0 ]; then
  echo "FAILED: run 2 PASSED with the change reverted. The tests do NOT exercise this change."
  echo "        This is the decorative-test case: green here means nothing."
  exit 4
fi
if [ $res_rc -ne 0 ]; then
  echo "FAILED: run 3 did not return to green (rc=$res_rc); run 2's failure is not attributable."
  exit 5
fi
# ATTRIBUTION, checked only here. Every diagnostic code above (3/4/5/6/7) stays
# exactly as informative at suite level, so this must not preempt them -- it only
# ever weakens the SUCCESS verdict, which is the one that was being over-read.
case " $TEST_CMD " in
  *"::"*|*" -k "*|*" -run "*|*" --test "*|*" --filter "*|*"--gtest_filter"*) SPECIFIC=1;;
  *) SPECIFIC=0;;
esac
if [ "$SPECIFIC" -eq 0 ] && [ "$SUITE_OK" -eq 0 ]; then
  echo "RED IS SUITE-LEVEL: pass/fail/pass held, but the test command names no single"
  echo "        test, so run 2's failure is not attributable to any particular one."
  echo "        A test that discriminates nothing passes run 2 unnoticed while its"
  echo "        neighbours go red. That is how #146 returned 0 carrying a test that"
  echo "        passed against the correct and the broken code alike."
  echo "        Re-run naming ONE test (one invocation per property), or pass --suite"
  echo "        to accept the weaker verdict deliberately."
  exit 8
fi
[ "$SUITE_OK" -eq 1 ] && [ "$SPECIFIC" -eq 0 ] && \
  echo "  NOTE: --suite given, so this red is NOT attributable to a named test."
echo "SUCCESS: pass/fail/pass. The tests genuinely exercise the reverted source."
exit 0
