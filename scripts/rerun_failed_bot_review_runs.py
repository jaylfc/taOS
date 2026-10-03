#!/usr/bin/env python3
"""Re-run the failed bot-review-gate pull_request runs on one head SHA.

Defect (tsk-dzd77m, measured on #3394 and #3400):
`.github/workflows/bot-review-gate.yml` fires on BOTH `pull_request` and
`pull_request_review`, and each event produces a SEPARATE workflow run and
check suite on the same head SHA. The `pull_request` run fires on open,
BEFORE CodeRabbit has reviewed, and fails. When CodeRabbit does review, the
`pull_request_review` run passes -- but GitHub keeps BOTH check runs named
`bot-review-gate`, and the required-check rollup keys off ANY of them, so the
rollup stays FAILURE and mergeStateStatus stays BLOCKED forever. On #3394 the
rollup listed both `bot-review-gate FAILURE` (run 37080299756, event
pull_request) and `bot-review-gate SUCCESS` (run 37080574868, event
pull_request_review), both isRequired=true. `gh pr merge --auto` therefore
never fires on any PR whose first gate run predates the bot review; only an
admin merge (scripts/gate_merge.sh) gets through.

Fix: from the pull_request_review run, once the gate has PASSED, re-run the
failed pull_request runs of THIS workflow for the SAME head SHA. The re-run
re-reads review state from the GitHub API at run time, so it reaches the same
verdict as the run that just passed and supersedes the stale FAILURE.

This helper deliberately does not weaken `check_bot_review.py` and does not
drop either trigger: the fast red on open stays, it is just reconciled once
the review has actually landed.

Selection is narrow on purpose, because a wider net is worse than no net:

  - ONLY runs of THIS workflow, matched on the workflow display name
    ("Bot review gate"). Re-running another gate's failure is not ours to do
    and turns a green PR red for an unrelated reason.
  - ONLY runs on the SAME head_sha. A failure on an older push belongs to a
    SHA no merge decision reads.
  - ONLY conclusion=failure. Nothing to fix otherwise.
  - ONLY event=pull_request. The pull_request_review run is the one doing the
    re-running; re-running it would be a self-trigger loop.

Failure is best-effort: a refused re-run POST logs a warning and exits 0. The
run doing the healing is itself a required check, so failing it here would
recreate the very BLOCKED condition this script removes.

Usage:
    python scripts/rerun_failed_bot_review_runs.py --head-sha <sha> \
        [--owner OWNER] [--repo REPO] [--token TOKEN]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from urllib.request import Request, urlopen

API = "https://api.github.com"

# The workflow display name as GitHub reports it on an Actions workflow run
# (the `name:` field of .github/workflows/bot-review-gate.yml). This is the same
# field the workflow's own re-run-on-stub-comment job filters on.
WORKFLOW_NAME = "Bot review gate"

# The event whose runs go stale: it fires before CodeRabbit has reviewed.
RERUN_EVENT = "pull_request"

# The only conclusion worth re-running.
FAILED_CONCLUSION = "failure"

EXIT_OK = 0
EXIT_USAGE = 2


def _get_token() -> str | None:
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or None


def _headers(token: str | None) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "taos-bot-review-gate-rerun",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _api_get(url: str, token: str | None = None) -> dict | list | None:
    """GET a GitHub REST endpoint. Returns the decoded body, or None on any
    infrastructure failure (network, auth, 404, non-JSON)."""
    req = Request(url, headers=_headers(token))
    try:
        with urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"error: GET {url} failed: {e}", file=sys.stderr)
        return None


def _api_mutate(url: str, token: str | None = None) -> bool:
    """POST to a GitHub REST endpoint. True on 2xx, False otherwise."""
    req = Request(
        url,
        data=b"",
        headers={**_headers(token), "Content-Length": "0"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=30) as r:
            return 200 <= r.status < 300
    except Exception as e:
        print(f"error: POST {url} failed: {e}", file=sys.stderr)
        return False


def extract_workflow_runs(payload) -> list[dict]:
    """Return the run list from a GET /actions/runs response.

    The list endpoint wraps runs in `{"total_count": N, "workflow_runs": [...]}`
    and follows pagination with a Link header; this reads the first page's runs
    and tolerates a bare list, so a caller that passes either shape gets the
    same runs. Anything else yields no runs, which reads as "nothing to
    re-run" rather than as a silent mis-selection.
    """
    if isinstance(payload, dict):
        runs = payload.get("workflow_runs")
        if isinstance(runs, list):
            return [r for r in runs if isinstance(r, dict)]
        return []
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    return []


def select_failed_runs(
    runs: list[dict],
    head_sha: str,
    *,
    workflow_name: str = WORKFLOW_NAME,
    event: str = RERUN_EVENT,
) -> list[dict]:
    """Return the runs of this workflow to re-run, oldest id first.

    Keeps only runs where ALL of the following hold:
      - name == workflow_name   (this workflow and no other),
      - head_sha == head_sha    (the SHA branch protection evaluates),
      - event == event          (the pre-review trigger, never this run),
      - conclusion == "failure" (a red verdict is the only thing to fix).
    """
    selected = [
        r for r in runs
        if r.get("name") == workflow_name
        and r.get("head_sha") == head_sha
        and r.get("event") == event
        and r.get("conclusion") == FAILED_CONCLUSION
        and isinstance(r.get("id"), int)
    ]
    return sorted(selected, key=lambda r: r["id"])


def rerun_failed_jobs(owner: str, repo: str, run: dict, token: str | None = None) -> bool:
    """Ask GitHub to re-run the failed jobs of one completed workflow run."""
    url = f"{API}/repos/{owner}/{repo}/actions/runs/{run['id']}/rerun-failed-jobs"
    return _api_mutate(url, token)


def _detect_repo() -> tuple[str, str]:
    """Detect (owner, repo) from GITHUB_REPOSITORY or the git remote."""
    env_repo = os.environ.get("GITHUB_REPOSITORY")
    if env_repo and "/" in env_repo:
        parts = env_repo.split("/")
        if len(parts) >= 2:
            return parts[0], parts[1]
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5, check=True,
        )
        url = result.stdout.strip()
        if "github.com" in url:
            path = url.split("github.com/", 1)[1].replace(".git", "").strip()
            parts = path.split("/")
            if len(parts) >= 2:
                return parts[0], parts[1]
    except (subprocess.CalledProcessError, FileNotFoundError, IndexError):
        pass
    return "jaylfc", "taOS"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Re-run the failed bot-review-gate pull_request workflow runs for "
            "one head SHA so a stale pre-review FAILURE stops pinning the PR "
            "on BLOCKED."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--head-sha", default=None,
        help="Head SHA to reconcile (default: $PR_HEAD). Required: without a "
             "SHA there is no scope to select in, and a re-run of the whole "
             "workflow would be wrong.",
    )
    parser.add_argument("--owner", default=None, help="Repo owner")
    parser.add_argument("--repo", default=None, help="Repo name")
    parser.add_argument(
        "--token", default=None,
        help="GitHub token (default: $GH_TOKEN or $GITHUB_TOKEN)",
    )
    args = parser.parse_args(argv)

    head_sha = args.head_sha or os.environ.get("PR_HEAD")
    if not head_sha:
        print(
            "error: --head-sha (or $PR_HEAD) is required; refusing to re-run "
            "without a SHA to scope the selection",
            file=sys.stderr,
        )
        return EXIT_USAGE

    owner = args.owner
    repo = args.repo
    if not owner or not repo:
        detected_owner, detected_repo = _detect_repo()
        owner = owner or detected_owner
        repo = repo or detected_repo

    token = args.token or _get_token()
    url = (
        f"{API}/repos/{owner}/{repo}/actions/runs"
        f"?head_sha={head_sha}&event={RERUN_EVENT}&per_page=100"
    )
    payload = _api_get(url, token)
    if payload is None:
        print(
            f"warn: could not list workflow runs for {head_sha}; leaving the "
            "stale runs untouched",
            file=sys.stderr,
        )
        return EXIT_OK

    selected = select_failed_runs(extract_workflow_runs(payload), head_sha)
    if not selected:
        print(f"no failed {WORKFLOW_NAME} {RERUN_EVENT} runs on {head_sha}; nothing to re-run")
        return EXIT_OK

    for run in selected:
        run_id = run["id"]
        if rerun_failed_jobs(owner, repo, run, token):
            print(f"re-running failed jobs of {WORKFLOW_NAME} run {run_id} on {head_sha}")
        else:
            # Best effort by design: this run is itself a required check, so a
            # refused re-run must not turn it red and re-block the PR.
            print(
                f"warn: could not re-run {WORKFLOW_NAME} run {run_id}; the stale "
                "failure may keep the PR on BLOCKED",
                file=sys.stderr,
            )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())