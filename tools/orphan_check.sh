#!/bin/bash
# ORPHAN CHECK: reconcile a BOARD against a REPO, in three directions.
#
# Usage:  ORPHAN_REPO=jaylfc/taOS ORPHAN_PID=prj-5y722y ./orphan_check.sh
# Exit:   0 = clean, 1 = findings, 3 = UNREADABLE (never silently "clean")
#
# WHY BOTH MUST BE PASSED (@taOS-website-dev, A2A 2230, 2026-08-06).
# v1 parameterised the REPO but took the BOARD from taos_team.pid(), which
# resolves from the SHARED ~/.taos-team/config. So `ORPHAN_REPO=jaylfc/taos-website`
# compared WEBSITE PRs against taOS CARDS. No website card id can appear in a taOS
# id set, so both directions were empty BY CONSTRUCTION and it exited 0 clean --
# on a board with real orphans on it. It was live in the hourly sweep for both
# repos, answering the question so nobody would look again.
#
# THE RED-PROOF DID NOT CATCH IT, and that is the lesson worth keeping. v1's
# proof inverted the CLOSED set and confirmed direction B fired. But both sets
# came from the same wrong board, so THE PROOF MOVED WITH THE BUG. Proving the
# matcher fires says nothing about whether it was handed the right inputs.
# Hence: the board is now required, pinned, and ASSERTED against what came back.
#
# THE THREE DIRECTIONS. Every check we had walks ONE list; orphans live in the gap.
#   A. OPEN card whose PR already MERGED    -> lane rebuilds a landed fix.
#      Can be LOAD-BEARING: tsk-bgznuk also blocked tsk-l6gqvw for 9 days.
#   B. OPEN PR whose card is CLOSED         -> board thinks it shipped while a PR,
#      often an EXTERNAL contributor's, sits untracked. PR #2208 (hognek), 9 days.
#   C. OPEN card whose ONLY guard is an OPEN PR (@taOS-website-dev's find).
#      next_card.py excludes cards with an open exec PR, but that exclusion is
#      INCIDENTAL: close the PR and the card is claimable again. If the card was
#      SUPERSEDED rather than finished, a lane then rebuilds the replacement.
#
# Read-only on purpose. Every direction has a legitimate benign explanation, so
# it prints and exits; it never closes or reopens anything.
#
# VERIFY FINDINGS ON ORIGIN, NEVER ON A LOCAL TREE: a tree 5 commits behind made
# a merged fix look unlanded (@taOS-website-dev, same day).
set -uo pipefail
REPO="${ORPHAN_REPO:-}"
PID="${ORPHAN_PID:-}"
if [ -z "$REPO" ] || [ -z "$PID" ]; then
  echo "usage: ORPHAN_REPO=<owner/repo> ORPHAN_PID=<prj-...> $0"
  echo "  Both are REQUIRED. ORPHAN_PID must never be allowed to default."
  exit 64

# EXIT CONTRACT (full): 0 = clean; 1 = A/B findings (real orphans);
# 2 = C watch-list only (cards guarded solely by an open PR - informational);
# 3 = REFUSED or unreadable; 64 = usage. Treat 3 and 64 as failures, never
# clean. 2 is NOT an anomaly - it was undocumented until 2026-08-09 and got
# itself investigated twice.
fi

# REPO <-> BOARD PAIRING, asserted.
#
# Requiring ORPHAN_PID is necessary but NOT SUFFICIENT, which I only found by
# testing the fix: passing the WRONG-but-valid pid reproduces the original false
# clean exactly. `ORPHAN_REPO=jaylfc/taos-website ORPHAN_PID=prj-5y722y` still
# printed "211 open / 419 closed ... none ... exit 0", because the tasks really
# do belong to the board that was asked for -- the mismatch is between the REPO
# and the BOARD, and no amount of validating the board against itself sees it.
# So the pairing itself has to be the assertion.
case "$REPO" in
  jaylfc/taOS)         EXPECT=prj-5y722y ;;
  jaylfc/taos-website) EXPECT=prj-utbsh7 ;;
  *)                   EXPECT="" ;;
esac
if [ -z "$EXPECT" ]; then
  echo "REFUSING: no known board for repo '$REPO'. Add the pairing to this script"
  echo "          rather than guessing; a wrong pairing reports a confident clean."
  exit 3
fi
if [ "$PID" != "$EXPECT" ]; then
  echo "REFUSING: repo '$REPO' pairs with board '$EXPECT', but ORPHAN_PID='$PID'."
  echo "          Comparing a repo against another repo's board is empty BY"
  echo "          CONSTRUCTION and exits 0 clean. That is the bug this guards."
  exit 3
fi

set -a; . "$HOME/.taos-team/config"; . "$HOME/.taos-team/taos-dev.cred"; set +a

python3 - "$REPO" "$PID" <<'PY'
import sys, os, pathlib, subprocess, json, re
repo, pid = sys.argv[1], sys.argv[2]
sys.path.insert(0, str(pathlib.Path.home()/".taos-team"))
import taos_team

# TEST SEAM (@taOS-website-dev, A2A 2234). Opt-in, ENV-ONLY, never read from a
# config file, and the cron must never set it -- it substitutes the input the
# whole check depends on, so if it ever leaked into a real run it would mask real
# data. It is a seam, not a switch.
#
# It exists because the refusal paths below are otherwise unreachable: no
# genuinely empty board is obtainable, and my token 404s on any board but my own,
# so the wrong-board and missing-field guards could never be watched refusing.
# I had asserted those "fail closed by construction" without ever seeing one
# fire, which is the exact shape we spent today finding in each other's tools.
fxt = os.environ.get("ORPHAN_FIXTURE_TASKS")
if fxt:
    # MARK EVERY OUTPUT *LINE*, NOT EVERY print CALL (@taOS-website-dev, A2A 2236).
    # A single banner line looked marked when eyeballed and was not: 10 of 11 lines
    # came out bare, and a fixture run still matched `grep '^orphan-check repo='`,
    # the exact grep the marking exists to defeat. Several prints here also start
    # with "\n" for spacing, so prefixing the CALL would put the marker on the blank
    # line and leave the verdict bare -- their failure, which mine was a worse
    # version of. Split on newlines and prefix each.
    # Test, whatever the implementation:  <fixture run> | grep -vc '^\[FIXTURE\]'  == 0
    _emit = print
    def print(*a, **k):  # noqa: A001 - deliberate shadow, fixture mode only
        text = " ".join(str(x) for x in a)
        _emit("\n".join("[FIXTURE] " + ln for ln in text.split("\n")), **k)
    print(f"board read replaced by {fxt} -- TEST MODE, not a real result")
    tasks = bare_tasks = json.load(open(fxt))
else:
    try:
        taos_team.login()
        res  = taos_team._req(taos_team.API, f"/api/projects/{pid}/tasks?limit=5000")
        bare = taos_team._req(taos_team.API, f"/api/projects/{pid}/tasks")
    except Exception as e:
        print(f"UNREADABLE board {pid}: {type(e).__name__}: {e}")
        print("An unreadable board is NOT an empty one. Refusing to report clean.")
        sys.exit(3)
    tasks, bare_tasks = res.get("items", []), bare.get("items", [])
if not tasks:
    print(f"UNREADABLE: board {pid} returned ZERO tasks. Refusing to report clean.")
    sys.exit(3)
if len(tasks) != len(bare_tasks):
    print(f"WARNING: limit=5000 returned {len(tasks)} but bare read returned "
          f"{len(bare_tasks)}; one of them is truncating. Using the larger.")
    if len(bare_tasks) > len(tasks):
        tasks = bare_tasks

# ASSERT THE BOARD IS THE ONE ASKED FOR. This is the check v1 lacked entirely.
wrong = {t.get("project_id") for t in tasks} - {pid}
if wrong:
    print(f"UNREADABLE: asked for board {pid} but tasks carry {sorted(wrong)}. "
          "Refusing to compare a repo against someone else's board.")
    sys.exit(3)

by_id  = {t.get("id"): t for t in tasks}
CLOSED = {"done", "closed", "cancelled"}
open_ids   = {i for i, t in by_id.items() if str(t.get("status", "")).lower() not in CLOSED}
closed_ids = {i for i, t in by_id.items() if str(t.get("status", "")).lower() in CLOSED}

def prs(state, limit):
    out = subprocess.run(["gh", "pr", "list", "--repo", repo, "--state", state,
                          "--limit", str(limit), "--json", "number,title,headRefName"],
                         capture_output=True, text=True).stdout
    return json.loads(out or "[]")

def cards_in(p):
    return set(re.findall(r"tsk-[a-z0-9]{6}",
                          (p.get("headRefName") or "") + " " + (p.get("title") or "")))

def dependents(cid):
    return [i for i, t in by_id.items()
            if i in open_ids and f"blocked-on:{cid}" in (t.get("labels") or [])]

# EXIT CODES SPLIT (@taOS-website-dev, A2A 2232). C was setting the same code as
# A and B, so on any board with several open exec PRs this went PERMANENTLY red
# -- 10 of 10 on taOS -- and a check that is always red teaches its reader to
# skip it. A signal that is always on is the same as no signal, which is the
# failure mode we keep finding arriving by a new road.
#   0 clean | 1 TRUE orphans (A/B), act | 2 only C's watch list, review | 3 unreadable
ab = 0
c_only = 0
print(f"orphan-check repo={repo} board={pid} ({len(open_ids)} open / {len(closed_ids)} closed)")

print("\nA. OPEN card whose PR already MERGED")
merged = {}
for p in prs("merged", 400):
    for c in cards_in(p):
        merged.setdefault(c, p["number"])
hits = sorted(open_ids & merged.keys())
for c in hits:
    dep = dependents(c)
    extra = f"  [LOAD-BEARING: also blocking {', '.join(dep)}]" if dep else ""
    print(f"   {c} <- PR #{merged[c]} MERGED :: {str(by_id[c].get('title'))[:52]}{extra}")
    ab = 1
print("   none" if not hits else "   -> confirm the merge commit is an ancestor of origin/dev before closing")

open_prs = prs("open", 200)

print("\nB. OPEN PR whose card is CLOSED")
hits2 = [(p["number"], c, p["title"][:52]) for p in open_prs
         for c in cards_in(p) if c in closed_ids]
for n, c, t in hits2:
    print(f"   PR #{n} OPEN <- card {c} CLOSED :: {t}")
    ab = 1
print("   none" if not hits2 else "   -> confirm the fix is ACTUALLY absent from origin/dev before reopening")

print("\nC. OPEN card whose ONLY guard against dispatch is an OPEN PR")
guarded = [(c, p["number"]) for p in open_prs for c in cards_in(p) if c in open_ids]
for c, n in sorted(set(guarded)):
    lbl = by_id[c].get("labels") or []
    claimable = any(str(x).endswith("claimable") for x in lbl)
    print(f"   {c} held only by open PR #{n}{' [CLAIMABLE the moment it closes]' if claimable else ''}"
          f" :: {str(by_id[c].get('title'))[:44]}")
    c_only = 1
print("   none" if not guarded else
      "   -> C is a WATCH LIST, not an alarm. Scan it for SUPERSESSION only; its LENGTH is not a finding count.")
sys.exit(1 if ab else (2 if c_only else 0))
PY
