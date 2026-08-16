#!/usr/bin/env bash
# PROVE red_first.sh in every direction, against the LIVE script, unmodified.
# Same PATH-shim discipline as prove_gate_merge.sh / prove_test_delta.sh /
# prove_health.sh. This was the LAST tool in this pack with no harness.
#
# WHY A REAL GIT REPO AND NOT A SHIM. Every other prover in this pack shims the
# outside world. This one cannot: red_first.sh's entire mechanism IS git
# (worktree, merge-base, checkout-a-path-at-a-rev), so a git shim would prove the
# shim. Only `gh` is shimmed, for the one baseRefName read. Each case builds a
# throwaway repo with a real bare "origin" carrying a real refs/pull/N/head.
#
# WHAT THIS HARNESS EXISTS TO SETTLE (tsk-wyfp6q): red_first.sh proves red by
# reverting SOURCE to the merge-base, so a PR that changes NO source has no path
# to satisfy a red-first demand. Proven live on #144, where a correct test-only
# build was refused. The two shapes are cases 12 and 13 below.
#
# Run after ANY edit to red_first.sh. Exit 0 = all paths proved, 1 = regression.
#   bash prove_red_first.sh                     prove the live script
#   bash prove_red_first.sh --red A             prove the harness catches break A
#   bash prove_red_first.sh --red all           every break, must all be caught
set -uo pipefail
SRC="${RF_SRC:-$(cd "$(dirname "$0")" && pwd)/red_first.sh}"
[ -r "$SRC" ] || { echo "FAILED: no readable $SRC"; exit 3; }

T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin"

# ---------------------------------------------------------------- gh shim
# red_first.sh makes exactly one gh call: pr view --json baseRefName. The shim
# records EVERY invocation, so a shim that is never reached is loud rather than
# silently bypassed (the sweep.sh PATH-prepend trap: a shim that is silently
# bypassed is indistinguishable from a shim that works).
cat > "$T/bin/gh" <<'SHIM'
#!/usr/bin/env bash
echo "$*" >> "$GH_CALLS"
case "$*" in
  *"pr view"*baseRefName*) cat "$FX/baseref"; exit "$(cat "$FX/baseref.rc" 2>/dev/null || echo 0)" ;;
  *) echo "SHIM: unhandled gh call: $*" >&2; exit 97 ;;
esac
SHIM
chmod +x "$T/bin/gh"
export GH_CALLS="$T/gh.calls"

fail=0; passes=0
check() { if [ "$2" = "$3" ]; then printf "  PASS  %-52s exit %s\n" "$1" "$3"; passes=$((passes+1))
  else printf "  FAIL  %-52s exit %s (wanted %s)\n" "$1" "$3" "$2"
       sed 's/^/          /' "$T/out" | head -6; fail=1; fi }
has() { n=$(grep -cF -- "$2" "$T/out" 2>/dev/null); n=${n:-0}
  if [ "$n" -ge 1 ]; then printf "  PASS  %-52s\n" "$1"; passes=$((passes+1))
  else printf "  FAIL  %-52s (absent: %s)\n" "$1" "$2"
       sed 's/^/          /' "$T/out" | head -6; fail=1; fi }
hasnt() { n=$(grep -cF -- "$2" "$T/out" 2>/dev/null); n=${n:-0}
  if [ "$n" -eq 0 ]; then printf "  PASS  %-52s\n" "$1"; passes=$((passes+1))
  else printf "  FAIL  %-52s (present, wanted absent: %s)\n" "$1" "$2"; fail=1; fi }

# ------------------------------------------------------------ repo fixture
# Builds: origin.git (bare) + repo (working). Commit M is the merge-base on main,
# commit P is the PR head, published as refs/pull/99/head exactly as GitHub does.
#   $1 = src.py content at M    $2 = src.py content at P
#   $3 = t.sh content at P      $4 = extra eval'd before P    $5 = extra eval'd before M
# t.sh does not exist at M, so every fixture here is a PR that adds its own test,
# which is the normal shape and the one the tool was written for.
mkrepo() {
  local m_src="$1" p_src="$2" p_test="$3" extra="${4:-}" extra_m="${5:-}"
  rm -rf "$T/repo" "$T/origin.git"
  git init -q --bare "$T/origin.git"
  git init -q -b main "$T/repo"
  (
    cd "$T/repo"
    git config user.email t@t; git config user.name t; git config commit.gpgsign false
    printf '%s' "$m_src" > src.py
    [ -n "$extra_m" ] && eval "$extra_m"
    git add -A && git commit -qm "M: merge-base"
    git remote add origin "$T/origin.git"
    git push -q origin main
    # --- the PR head, a child of M
    printf '%s' "$p_src" > src.py
    printf '%s' "$p_test" > t.sh
    [ -n "$extra" ] && eval "$extra"
    git add -A && git commit -qm "P: the PR"
    git push -q origin "HEAD:refs/pull/99/head"
    git checkout -q main
    git reset -q --hard origin/main
  )
  mkdir -p "$T/fx"; printf 'main' > "$T/fx/baseref"; rm -f "$T/fx/baseref.rc"
  : > "$GH_CALLS"
}

# Invoke the script under test FROM the repo, with only gh shimmed.
run() {
  ( cd "$T/repo" && FX="$T/fx" PATH="$T/bin:$PATH" bash "$RUN_SRC" "$@" ) >"$T/out" 2>&1
  echo $?
}

# t.sh variants. GREP: genuinely exercises src.py. TRUE: exercises nothing.
T_GREP='grep -q FIXED src.py
'
T_TRUE='true
'
T_FALSE='false
'
# Fails on every call after the first. Lets run 3 diverge from run 1 with the
# same tree, which is the only honest way to reach the restore-failed path.
T_ONCE='n=$(cat "$RF_CNT" 2>/dev/null || echo 0); n=$((n+1)); echo $n > "$RF_CNT"; [ "$n" -eq 1 ]
'

# ---------------------------------------------------------------- the cases
prove() {
RUN_SRC="$1"
echo "proving $(basename "$SRC") ${2:-}   ($(stat -c %y "$SRC" | cut -d. -f1))"
echo

echo "THE CYCLE ITSELF"
mkrepo 'BUGGY' 'FIXED' "$T_GREP"
  check "pass/fail/pass on a real source change"        0 "$(run --suite r/r 99 src.py -- 'bash t.sh')"
  has   "...and names the merge-base it reverted to"    "merge-base"
  has   "...and prints all three runs"                  "run 3 restored"
  # assert the shim was reached rather than bypassed
  if [ -s "$GH_CALLS" ]; then printf "  PASS  %-52s\n" "...gh shim reached (not bypassed)"; passes=$((passes+1))
  else printf "  FAIL  %-52s\n" "...gh shim NEVER reached: PATH bypassed"; fail=1; fi

echo
echo "IT MUST REFUSE -- the dangerous direction is a wrong shape reading as a pass"
mkrepo 'BUGGY' 'FIXED' "$T_TRUE"
  check "DECORATIVE tests: green with the change reverted" 4 "$(run r/r 99 src.py -- 'bash t.sh')"
  has   "...and says green here means nothing"          "means nothing"
mkrepo 'BUGGY' 'FIXED' "$T_FALSE"
  check "PR is RED before the experiment starts"        3 "$(run r/r 99 src.py -- 'bash t.sh')"
  has   "...and says nothing was learned"               "red before the experiment"
RF_CNT="$T/cnt"; export RF_CNT
mkrepo 'BUGGY' 'FIXED' "$T_ONCE"
  rm -f "$T/cnt"
  check "RESTORE run does not return to green"          5 "$(run r/r 99 src.py -- 'bash t.sh')"
  has   "...and says run 2 is not attributable"         "not attributable"
unset RF_CNT

echo
echo "USAGE -- a malformed call must never look like a verdict"
mkrepo 'BUGGY' 'FIXED' "$T_GREP"
  check "no arguments at all"                           2 "$(run)"
  check "repo only"                                     2 "$(run r/r)"
  check "no -- separator (test cmd swallowed as a path)" 2 "$(run r/r 99 src.py 'bash t.sh')"
  check "-- present but no test command"                2 "$(run r/r 99 src.py --)"
  check "-- present but no source paths"                2 "$(run r/r 99 -- 'bash t.sh')"

echo
echo "CANNOT SEE -- setup failure must FAIL CLOSED, never 'pass'"
mkrepo 'BUGGY' 'FIXED' "$T_GREP"
  check "PR ref does not exist on origin"               6 "$(run r/r 4242 src.py -- 'bash t.sh')"
  has   "...and says which PR it could not fetch"       "cannot fetch PR"
mkrepo 'BUGGY' 'FIXED' "$T_GREP"; : > "$T/fx/baseref"
  check "gh cannot report the base ref"                 6 "$(run r/r 99 src.py -- 'bash t.sh')"
  has   "...and names the base-ref read"                "cannot read base ref"
mkrepo 'BUGGY' 'FIXED' "$T_GREP"; printf 'no-such-branch' > "$T/fx/baseref"
  check "base ref names a branch origin does not have" 6 "$(run r/r 99 src.py -- 'bash t.sh')"

echo
echo "THE SHAPES tsk-wyfp6q IS ABOUT -- a PR with NO source change to revert"
# 12. TEST-ONLY: src.py identical at M and P; the PR adds only a test. This is
#     #144's shape. Reverting src.py is a no-op, so run 2 passes and the tool
#     accuses a correct build of having decorative tests.
mkrepo 'FIXED' 'FIXED' "$T_GREP"
  check "TEST-ONLY PR is not accused of decorative tests" 7 "$(run r/r 99 src.py -- 'bash t.sh')"
  has   "...and says red-first does not apply here"     "does not apply"
  hasnt "...and does NOT claim the tests are decorative" "means nothing"
# 13. NEW-FILE: the PR adds a source file absent at the merge-base. The correct
#     revert is a DELETE, which `git checkout MB -- newfile` cannot express, so
#     the experiment used to be unreachable and reported as a setup failure.
#     t.sh greps the NEW file, so deleting it must genuinely go red.
mkrepo 'BUGGY' 'FIXED' 'grep -q NEW newmod.py
' "printf 'NEW' > newmod.py"
  check "NEW source file: revert is a delete, and runs"  0 "$(run --suite r/r 99 newmod.py -- 'bash t.sh')"
  has   "...and says it deleted rather than reverted"   "deleted (added by this PR)"
# 14. A path that exists at NEITHER end is a typo, and a typo must not be
#     absorbed by exit 7 as a clean 'not applicable'.
mkrepo 'BUGGY' 'FIXED' "$T_GREP"
  check "path exists at neither end (typo) is a usage error" 2 "$(run r/r 99 srk.py -- 'bash t.sh')"
  has   "...and names the path it could not find"       "srk.py"
  hasnt "...and does NOT read as not-applicable"        "NOT APPLICABLE"

echo
echo "AND IT MUST STILL FIRE AFTERWARDS -- a fix that refuses everything is worse"
mkrepo 'BUGGY' 'FIXED' "$T_GREP"
  check "a real source change still proves red-first"   0 "$(run --suite r/r 99 src.py -- 'bash t.sh')"
mkrepo 'BUGGY' 'FIXED' "$T_TRUE"
  check "decorative tests are still caught"             4 "$(run r/r 99 src.py -- 'bash t.sh')"
# One path changed, one identical: SOME source moved, so the cycle is expressible
# and must run normally. Only ALL-identical is inexpressible. other.py is present
# and byte-identical at BOTH ends, which is what makes this the mixed case rather
# than the new-file case (my first fixture created it only at P and therefore
# tested case 13 again under case 15's name).
mkrepo 'BUGGY' 'FIXED' "$T_GREP" "" "printf 'SAME' > other.py"
  check "mixed paths: one changed, one identical, runs"  0 "$(run --suite r/r 99 src.py other.py -- 'bash t.sh')"
# One changed, one ADDED: the added file used to fail the whole checkout and take
# a valid experiment down with it.
mkrepo 'BUGGY' 'FIXED' "$T_GREP" "printf 'NEW' > newmod.py"
  check "mixed paths: one changed, one ADDED, runs"      0 "$(run --suite r/r 99 src.py newmod.py -- 'bash t.sh')"

echo
echo "ATTRIBUTION: a SUITE-LEVEL red is not evidence about a NAMED test"
echo "  (this is why the tool returned 0 on #146 while carrying a test that passed"
echo "   against the correct and the broken implementation alike)"
mkrepo 'BUGGY' 'FIXED' "$T_GREP"
  check "a bare suite command does NOT read as proved"  8 "$(run r/r 99 src.py -- 'bash t.sh')"
  has   "...and says the red is not attributable"       "not attributable"
  has   "...and says how to discharge it"               "naming ONE test"
mkrepo 'BUGGY' 'FIXED' "$T_GREP"
  check "naming ONE test proves red-first"              0 "$(run r/r 99 src.py -- 'bash t.sh -k mytest')"
mkrepo 'BUGGY' 'FIXED' "$T_GREP"
  check "a pytest ::selector also counts as specific"   0 "$(run r/r 99 src.py -- 'bash t.sh tests/t.py::mytest')"
mkrepo 'BUGGY' 'FIXED' "$T_GREP"
  check "--suite accepts the weaker verdict explicitly" 0 "$(run --suite r/r 99 src.py -- 'bash t.sh')"
  has   "...and SAYS the verdict is weaker"             "NOT attributable to a named test"
# The attribution gate must weaken ONLY the success verdict. Every diagnostic code
# is exactly as informative at suite level, and preempting them would trade a real
# finding for a procedural complaint.
mkrepo 'BUGGY' 'FIXED' "$T_TRUE"
  check "decorative tests still exit 4, not 8"          4 "$(run r/r 99 src.py -- 'bash t.sh')"
mkrepo 'BUGGY' 'FIXED' "$T_FALSE"
  check "a red PR still exits 3, not 8"                 3 "$(run r/r 99 src.py -- 'bash t.sh')"
mkrepo 'FIXED' 'FIXED' "$T_GREP"
  check "a test-only PR still exits 7, not 8"           7 "$(run r/r 99 src.py -- 'bash t.sh')"
}

# ------------------------------------------------------------ red variants
# A break the harness does not catch is a path the harness does not test.
# Every variant asserts its own changed-line count first: 0 lines changed means
# the variant is INERT and proves nothing, which has bitten this pack five times.
red_variant() {
  local name="$1" sed_prog="$2" want_desc="$3"
  local V="$T/red_$name.sh"
  sed "$sed_prog" "$SRC" > "$V"
  local delta; delta=$(diff <(cat "$SRC") <(cat "$V") | grep -c '^[<>]')
  echo "=== RED $name: $want_desc"
  echo "    changed lines: $delta   (0 would mean the variant is INERT)"
  if [ "$delta" -eq 0 ]; then echo "    FAILED: variant changed nothing; it cannot prove anything"; return 1; fi
  local out; out=$(prove "$V" "[RED $name]" 2>&1)
  if printf '%s' "$out" | grep -q '^  FAIL'; then
    echo "    CAUGHT by: $(printf '%s' "$out" | grep '^  FAIL' | head -3 | sed 's/^  FAIL  /                /')"
    return 0
  fi
  echo "    NOT CAUGHT -- the harness passes a broken script. This is the finding."
  printf '%s\n' "$out" | tail -5
  return 1
}

if [ "${1:-}" = "--red" ]; then
  which="${2:-all}"; rfail=0
  # A: the decorative-test guard removed. The dangerous direction.
  [ "$which" = all ] || [ "$which" = A ] && { red_variant A 's/^if \[ \$rev_rc -eq 0 \]; then/if false; then/' \
      "exit 4 guard disabled: decorative tests read as a pass" || rfail=1; echo; }
  # B: the head-run guard removed, so a broken PR proceeds into the experiment.
  [ "$which" = all ] || [ "$which" = B ] && { red_variant B 's/^if \[ \$head_rc -ne 0 \]; then/if false; then/' \
      "exit 3 guard disabled: a red PR is experimented on anyway" || rfail=1; echo; }
  # C: the restore check removed, so run 2's failure stops being attributable.
  [ "$which" = all ] || [ "$which" = C ] && { red_variant C 's/^if \[ \$res_rc -ne 0 \]; then/if false; then/' \
      "exit 5 guard disabled: an unrestorable tree reads as a pass" || rfail=1; echo; }
  # D: the tsk-wyfp6q fix removed, reverting to the behaviour that refused #144.
  # D targets ONLY the exit, not the whole classification block: deleting the
  # block would break the script loudly under `set -u` (unset REVERT/NEWPATH),
  # which the harness would catch for the wrong reason and tell me nothing.
  [ "$which" = all ] || [ "$which" = D ] && { red_variant D 's/^  exit 7$/  : # RED D/' \
      "exit 7 neutered: a test-only PR is accused of decorative tests again" || rfail=1; echo; }
  # E: the typo guard neutered, so a mistyped path is absorbed by exit 7 and
  # reads as a clean 'not applicable' instead of a usage error.
  [ "$which" = all ] || [ "$which" = E ] && { red_variant E 's/^  exit 2$/  : # RED E/' \
      "typo guard neutered: a bad pathspec reads as not-applicable" || rfail=1; echo; }
  # F: the attribution gate neutered, so a suite-level red reads as proof about a
  # named test again -- the exact miss on #146.
  [ "$which" = all ] || [ "$which" = F ] && { red_variant F 's/^  exit 8$/  : # RED F/' \
      "attribution gate neutered: a suite-level red reads as proved" || rfail=1; echo; }
  [ "$rfail" -eq 0 ] && { echo "SUCCESS: every red variant was caught"; exit 0; }
  echo "FAILED: a red variant slipped through"; exit 1
fi

prove "$SRC"
echo
echo "  paths asserted: $passes"
if [ "$fail" -eq 0 ]; then echo "SUCCESS: every path proved against the CURRENT script"; exit 0; fi
echo "FAILED: a path regressed"; exit 1
