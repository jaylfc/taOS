#!/usr/bin/env python3
"""Realtime PR state watcher: emit ONE line per meaningful STATE TRANSITION.

Jay 2026-07-27: a 2-hourly poll let finished PRs sit green for a day holding
fleet throttle slots. This watches continuously and speaks only when a PR
CHANGES to something actionable, so it is quiet by default and loud exactly
when a decision is needed.

Transitions reported:
  -> READY      mergeable + all required checks green   (merge it, frees a slot)
  -> RED        a required check failed                 (fix-forward or close)
  -> CONFLICT   branch conflicts with base              (rebase needed)
  -> DRAFTED    PR was drafted                          (keep watching)
  -> READY-FROM-DRAFT PR was undrafted                  (re-evaluate mergeability)
  -> GONE       merged or closed                        (slot freed)
First sighting of a PR is reported as NEW so nothing appears from nowhere.
Silence = nothing changed. Poll interval is 60s: effectively realtime for CI,
and ~60 API calls/hour against a 5000/hour budget.
"""
import json
import subprocess
import sys
import time

REPO = "jaylfc/taOS"
AUTHORS = {"jaylfc", "hognek"}      # fleet + collaborator PRs I merge
REQUIRED = ("test (3.1", "lint", "spa-build", "shards")
POLL_SECONDS = 60


def gh_json(args):
    try:
        out = subprocess.run(["gh"] + args, capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            return None
        return json.loads(out.stdout or "[]")
    except Exception:
        return None


def classify(pr):
    """Reduce a PR to one actionable state string."""
    checks = pr.get("statusCheckRollup") or []
    required = [c for c in checks
                if any(str(c.get("name") or c.get("context") or "").startswith(r) for r in REQUIRED)]
    concl = [str(c.get("conclusion") or c.get("status") or "") for c in required]
    if any(c in ("FAILURE", "TIMED_OUT", "ACTION_REQUIRED") for c in concl):
        return "RED"
    state = pr.get("mergeStateStatus")
    if state == "DIRTY":
        return "CONFLICT"
    if required and all(c == "SUCCESS" for c in concl) and state == "CLEAN":
        return "READY"
    return "PENDING"


def process_tick(prs, seen, reported, first_pass):
    messages = []
    if prs is None:
        return messages, seen, reported, first_pass

    live = {}
    for pr in prs:
        if (pr.get("author") or {}).get("login") not in AUTHORS:
            continue
        n = pr["number"]
        if pr.get("isDraft"):
            state = "DRAFT"
        else:
            state = classify(pr)
        oid = (pr.get("headRefOid") or "")[:8]
        live[n] = state
        title = (pr.get("title") or "")[:60]
        prev = seen.get(n)

        if prev is None and not first_pass:
            messages.append(f"PR #{n} NEW [{state}] {title}")
            reported[n] = (state, oid)
        elif prev is not None and prev != state and state != "PENDING":
            if reported.get(n) != (state, oid):
                if prev != "DRAFT" and state == "DRAFT":
                    msg = f"PR #{n} DRAFTED | {title}"
                elif prev == "DRAFT" and state != "DRAFT":
                    msg = f"PR #{n} READY-FROM-DRAFT | {title}"
                else:
                    msg = f"PR #{n} {prev} -> {state} | {title}"
                messages.append(msg)
                reported[n] = (state, oid)

    for n, prev in seen.items():
        if n not in live and prev != "GONE":
            messages.append(f"PR #{n} GONE (merged or closed) - throttle slot freed")
            reported.pop(n, None)

    return messages, live, reported, False


def main():
    seen = {}       # number -> last observed state (for GONE detection)
    reported = {}   # number -> (state, head_oid) of the last line actually EMITTED
    first_pass = True
    while True:
        prs = gh_json([
            "pr", "list", "--repo", REPO, "--state", "open", "--limit", "100",
            # headRefOid added 2026-07-28: emission is keyed on (state, commit) so a
            # CI re-run at the SAME commit is not reported as a new event.
            "--json", "number,title,author,mergeStateStatus,statusCheckRollup,isDraft,headRefOid",
        ])
        messages, seen, reported, first_pass = process_tick(
            prs, seen, reported, first_pass
        )
        for msg in messages:
            print(msg, flush=True)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
