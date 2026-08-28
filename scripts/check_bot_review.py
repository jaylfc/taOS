#!/usr/bin/env python3
"""Bot-review guard: rate-limited CodeRabbit stubs must not read as a review.

Detects PRs whose only CodeRabbit output is a rate-limit stub -- a comment
or review that only announces the plan quota was exhausted instead of
producing an actual review. This is the mechanism behind the fleet rule
"no merge on rate-limited fake green" from the 2026-08-16 bot-review
retrospective audit (33 merged PRs shipped fake-green in one week).

When CodeRabbit is rate-limited it posts a short stub comment (body matching
the rate-limit signature below) and a passing check titled "Review rate
limited" -- the check passes by design so it never blocks merging. This
guard inspects the comments instead and fails red when the stub is the
ONLY CodeRabbit output, so a merge gate can catch the fake-green condition.

Three exit cases, all printed with the exit code so they can be read off
the evidence at the granularity of the audit:

    0  PASS  -- a real CodeRabbit review exists, OR CodeRabbit is entirely
                absent (dependabot-class PRs; absence is not fake-green).
    1  FAIL  -- the only CodeRabbit output on the PR is a stub: a rate-limit
               stub, or CodeRabbit's own auto-generated scaffolding (the
               acknowledgement reply posted when a review trigger is accepted
               but produces no review, or the auto-summary comment). Neither
               is review content.
    2  ERROR -- infrastructure failure (network, auth, 404, etc.).

Usage:
    python scripts/check_bot_review.py <pr-number> [--owner OWNER] [--repo REPO] [--label LABEL]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.request import Request, urlopen

API = "https://api.github.com"

# CodeRabbit identifies itself via these GitHub App bot logins.
CODERABBIT_LOGINS = frozenset({
    "coderabbit[bot]",
    "coderabbitai",
    "coderabbitai[bot]",
})

# Signature of a CodeRabbit rate-limit stub. From the CodeRabbit docs
# (docs.coderabbit.ai/management/plans): "When a push is rate-limited,
# CodeRabbit posts a rate-limit comment ... and a passing check titled
# 'Review rate limited'". The .coderabbit.yaml config prose references
# "review limit reached". Both spellings are matched below, plus the
# "rate limit" / "quota" tokens with context words to avoid false positives
# on legitimate reviews that mention rate-limiting as a code smell.
RATE_LIMIT_RE = re.compile(
    r"review\s*limit\s*reached|"
    r"review\s*rate\s*limited|"
    r"rate\s*limit(?:ed| reached| exhausted| hit| paused)|"
    r"plan\s*quota\s*(?:reached|exhausted)|"
    r"review\s*quota\s*(?:reached|exhausted)",
    re.IGNORECASE,
)

# HTML comment markers that CodeRabbit injects into its own auto-generated
# scaffolding rather than review content: the acknowledgement reply posted
# when a @coderabbitai full review trigger is accepted but no review follows,
# and the auto-summary comment posted on merge/close. Both carry no findings
# and must never read as a review.
CODERABBIT_SCAFFOLDING_RE = re.compile(
    r"<!-- (?:This is an auto-generated comment: summarize by coderabbit\.ai"
    r"|CodeRabbit review command invocation:[^>]+) -->",
    re.IGNORECASE,
)

EXIT_OK = 0
EXIT_STUB = 1
EXIT_ERROR = 2

# A human-placed label that explicitly waives the bot-review gate for a PR
# whose only CodeRabbit output is a rate-limit stub or auto-generated
# scaffolding -- an infrastructure condition (CodeRabbit rate-limited, or a
# review trigger accepted with no review produced), not a defect in the PR.
# Applied by a lead; never by automation in a PR lane. Mirrors
# `gate-integrity-allow` for the gate-integrity guard. Read from the API at
# run time, never from a stale event payload.
DEFAULT_ALLOW_LABEL = "bot-review-allow"

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class CRItem:
    """A CodeRabbit review or comment fetched from the GitHub API."""
    id: int
    body: str | None
    is_review: bool
    review_state: str | None = None
    created_at: str | None = None


def _get_token() -> str | None:
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or None


def _api_get(url: str, token: str | None = None) -> list | None:
    """Fetch a paginated GitHub REST API endpoint.

    Returns None on infrastructure failure (network error, auth failure,
    404, 422). Returns the full de-paginated list of items on success.
    An empty list is returned for a legitimately empty result so callers
    can distinguish cannot-see (None) from empty ([]).
    """
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "taos-bot-review-gate",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    items: list = []
    page_url: str | None = url
    while page_url:
        req = Request(page_url, headers=headers)
        try:
            with urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode("utf-8"))
        except Exception as e:
            print(f"error: GET {url} failed: {e}", file=sys.stderr)
            return None
        if isinstance(data, dict) and data.get("message"):
            print(f"error: API response message: {data['message']}", file=sys.stderr)
            return None
        if isinstance(data, list):
            items.extend(data)
        elif isinstance(data, dict):
            items.append(data)
        # Follow pagination via Link header
        link = r.headers.get("Link", "")
        page_url = None
        for part in link.split(","):
            if 'rel="next"' in part:
                match = re.search(r"<([^>]+)>", part)
                if match:
                    page_url = match.group(1)
                break
    return items


def is_rate_limit_stub(body: str | None) -> bool:
    """Return True if a comment/review body matches the CodeRabbit
    rate-limit stub signature."""
    if not body:
        return False
    return bool(RATE_LIMIT_RE.search(body))


def is_coderabbit_scaffolding(body: str | None) -> bool:
    """Return True if a body is CodeRabbit's own auto-generated scaffolding
    rather than review content.

    Matches the HTML comment markers CodeRabbit appends to (a) the
    acknowledgement reply posted when a review trigger is accepted but no
    review follows, and (b) the auto-summary comment posted on merge/close.
    Neither carries findings; both are machine-identifiable stubs that must
    not read as a real review, exactly as a rate-limit stub does.
    """
    if not body:
        return False
    return bool(CODERABBIT_SCAFFOLDING_RE.search(body))


def is_real_item(item: CRItem) -> bool:
    """Return True if a CR item represents real review content.

    A rate-limit stub or CodeRabbit auto-generated scaffolding (acknowledgement
    reply / auto-summary) is never real -- these are bots posting that a
    trigger was accepted with no review content following. **The stub checks
    run FIRST and outrank the review state**: a review whose own body is a
    stub is not review content no matter what state it carries. This gate
    exists to catch fake-green, so where the two signals disagree it must
    fail closed; trusting the state over a stub body would be the one
    ordering that lets a fake-green through.

    Otherwise, review objects with state APPROVED or CHANGES_REQUESTED are
    real regardless of body content (the review state itself is then the
    substantive signal -- e.g. an APPROVED review with an empty body).
    Comments and COMMENTED reviews are real only when they carry non-empty,
    non-stub body text.
    """
    if is_rate_limit_stub(item.body):
        return False
    if is_coderabbit_scaffolding(item.body):
        return False
    if item.is_review:
        state = (item.review_state or "").upper()
        if state in ("APPROVED", "CHANGES_REQUESTED"):
            return True
    return bool(item.body and item.body.strip())


def _is_coderabbit(user: dict | None) -> bool:
    if not user:
        return False
    return (user.get("login") or "").lower() in CODERABBIT_LOGINS


def collect_coderabbit_items(
    owner: str, repo: str, pr_number: int, token: str | None = None,
) -> list[CRItem] | None:
    """Fetch all CodeRabbit reviews and comments on a PR.

    Queries three GitHub endpoints:
      1. Reviews (GET /repos/{owner}/{repo}/pulls/{pr}/reviews)
      2. Issue comments (GET /repos/{owner}/{repo}/issues/{pr}/comments)
      3. Review comments (GET /repos/{owner}/{repo}/pulls/{pr}/comments)

    Returns None on infrastructure failure, [] if no CR output exists.
    """
    token = token or _get_token()
    base = f"{API}/repos/{owner}/{repo}/pulls/{pr_number}"

    items: list[CRItem] = []

    # 1. Reviews
    reviews = _api_get(f"{base}/reviews", token)
    if reviews is None:
        return None
    for r in reviews:
        if _is_coderabbit(r.get("user")):
            items.append(CRItem(
                id=r.get("id", 0),
                body=r.get("body"),
                is_review=True,
                review_state=r.get("state"),
                created_at=r.get("created_at"),
            ))

    # 2. Issue comments (top-level PR comments)
    issue_comments = _api_get(
        f"{API}/repos/{owner}/{repo}/issues/{pr_number}/comments", token
    )
    if issue_comments is None:
        return None
    for c in issue_comments:
        if _is_coderabbit(c.get("user")):
            items.append(CRItem(
                id=c.get("id", 0),
                body=c.get("body"),
                is_review=False,
                created_at=c.get("created_at"),
            ))

    # 3. Review comments (line-level discussion threads)
    review_comments = _api_get(f"{base}/comments", token)
    if review_comments is None:
        return None
    for c in review_comments:
        if _is_coderabbit(c.get("user")):
            items.append(CRItem(
                id=c.get("id", 0),
                body=c.get("body"),
                is_review=False,
                created_at=c.get("created_at"),
            ))

    # Sort chronologically (oldest first). GitHub IDs are monotonically
    # increasing, and created_at is a reliable fallback for ordering.
    items.sort(key=lambda x: (x.created_at or "", x.id))
    return items


def collect_pr_labels(
    owner: str, repo: str, pr_number: int, token: str | None = None,
) -> set[str] | None:
    """Fetch the PR's label NAMES via the GitHub REST API.

    Returns None on infrastructure failure (network error, auth failure,
    404, bad JSON), so cannot-see is never mistaken for "no waiver label set".
    Returns a set of label names (possibly empty) on success. The label is
    read here, at run time, from the API -- never from a stale event payload,
    which is the contract that makes `labeled`/`unlabeled` activity reliable.
    """
    data = _api_get(f"{API}/repos/{owner}/{repo}/pulls/{pr_number}", token)
    if data is None:
        return None
    pr = data[0] if isinstance(data, list) and data else {}
    if not isinstance(pr, dict):
        return None
    return {
        lbl.get("name", "") for lbl in pr.get("labels", [])
        if isinstance(lbl, dict)
    }


def classify(items: list[CRItem]) -> tuple[int, str]:
    """Classify CR items and determine the exit code + message.

    Returns (exit_code, message):
    - (0, "PASS ...")            -- a real CodeRabbit review exists.
    - (0, "PASS (absent, ...)")  -- CodeRabbit is entirely absent.
    - (1, "FAIL ...")            -- only stubs exist, i.e. rate-limit stubs
                                  or CodeRabbit auto-generated scaffolding
                                  (acknowledgement / auto-summary), neither
                                  of which is review content.
    - (0, "PASS ...")            -- CR output exists but is neither a stub
                                  nor substantive (edge case, not fake-green).
    """
    if not items:
        return EXIT_OK, "PASS (absent, not stubbed): no CodeRabbit output on this PR"

    real_items = [i for i in items if is_real_item(i)]
    if real_items:
        return EXIT_OK, (
            f"PASS: {len(real_items)} real CodeRabbit review item(s) "
            f"(exit {EXIT_OK})"
        )

    # No real review threads. A trigger was accepted but produced no review
    # content: that is the fake-green condition. This covers both the
    # rate-limit stub and CodeRabbit's own auto-generated scaffolding
    # (acknowledgement reply / auto-summary), which the gate must treat
    # the same way -- it must not land on the absent/PASS path above.
    # Name every stub kind actually present. Reporting only the first match
    # would describe a PR carrying both as if it carried one, and the message
    # is the only thing a human reads off a red gate.
    has_rate_limit = any(is_rate_limit_stub(i.body) for i in items)
    has_scaffolding = any(is_coderabbit_scaffolding(i.body) for i in items)
    if has_rate_limit or has_scaffolding:
        kinds = []
        if has_rate_limit:
            kinds.append("a rate-limit stub")
        if has_scaffolding:
            kinds.append("auto-generated scaffolding")
        return EXIT_STUB, (
            f"FAIL: only CodeRabbit output is {' and '.join(kinds)} "
            f"-- no review content (exit {EXIT_STUB})"
        )

    # CR output exists but no real review threads and no recognizable stub.
    # Absence of a real review is still not fake-green: there is no stub
    # masquerading as a review.
    return EXIT_OK, (
        f"PASS (absent, not stubbed): CodeRabbit output present but not a "
        f"rate-limit stub (exit {EXIT_OK})"
    )


def check_bot_review(
    owner: str, repo: str, pr_number: int,
    allow_label: str = DEFAULT_ALLOW_LABEL,
    token: str | None = None,
) -> tuple[int, str]:
    """Check a PR's CodeRabbit output for rate-limit stubs.

    Returns (exit_code, message). EXIT_ERROR (2) is returned when the
    GitHub API cannot be reached or returns an error, so a cannot-see
    state is never mistaken for a clean pass.

    When the PR carries `allow_label` (read fresh from the GitHub API at
    run time) AND the only CodeRabbit output is a stub -- a rate-limit stub
    or CodeRabbit auto-generated scaffolding (acknowledgement reply /
    auto-summary) -- the FAIL verdict is waived to exit 0 with a message
    that explicitly says WAIVED. It is never reported as a genuine PASS, so
    a human reading the check output always knows the gate was overridden by
    a conscious lead act, not cleared by the bot.

    The waiver covers only the EXIT_STUB verdict class (both stub kinds,
    because both are infrastructural: CodeRabbit was rate-limited or its
    trigger was accepted with no review produced -- neither is a defect in
    the PR). It does NOT cover EXIT_ERROR (cannot fetch CR items), which
    must stay fail-closed on a genuine cannot-see.
    """
    items = collect_coderabbit_items(owner, repo, pr_number, token)
    if items is None:
        return EXIT_ERROR, (
            f"error: could not fetch CodeRabbit output for PR #{pr_number} "
            f"(exit {EXIT_ERROR})"
        )
    exit_code, message = classify(items)
    if exit_code == EXIT_STUB:
        labels = collect_pr_labels(owner, repo, pr_number, token)
        if labels is not None and allow_label in labels:
            return EXIT_OK, (
                f"bot-review-gate: WAIVED -- `{allow_label}` label overrides the "
                f"stub-only verdict (rate-limit stub / auto-generated scaffolding). "
                f"A lead confirmed this is an infrastructural CodeRabbit condition, "
                f"not a PR defect. Underlying verdict waived: {message}"
            )
    return exit_code, message


def _detect_repo() -> tuple[str, str]:
    """Detect (owner, repo) from GITHUB_REPOSITORY env var or git remote.

    Falls back to ("jaylfc", "taOS") if neither is available.
    """
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
        description="Check that the only CodeRabbit output on a PR is not a "
                    "rate-limit stub.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "pr_number", type=int,
        help="GitHub PR number to check",
    )
    parser.add_argument(
        "--owner", default=None,
        help="Repo owner (default: auto-detect from git remote)",
    )
    parser.add_argument(
        "--repo", default=None,
        help="Repo name (default: auto-detect from git remote)",
    )
    parser.add_argument(
        "--token", default=None,
        help="GitHub token (default: $GH_TOKEN or $GITHUB_TOKEN)",
    )
    parser.add_argument(
        "--label", default=DEFAULT_ALLOW_LABEL,
        help=f"Allow label that waives the stub verdict (default: {DEFAULT_ALLOW_LABEL}).",
    )
    args = parser.parse_args(argv)

    owner = args.owner
    repo = args.repo
    if not owner or not repo:
        detected_owner, detected_repo = _detect_repo()
        owner = owner or detected_owner
        repo = repo or detected_repo

    exit_code, message = check_bot_review(
        owner, repo, args.pr_number, args.label, args.token,
    )
    print(message)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
