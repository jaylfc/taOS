#!/usr/bin/env python3
"""Gate-integrity check (pull_request_target).

Runs on the BASE ref via pull_request_target so the checkout is never the
merge ref. Inspects the PR diff via the GitHub API and fails when the PR
touches .github/workflows/ or a gate-checker script unless an explicit
allow-label is present on the PR.

This closes the class defect where a PR could modify its own gate checker
(and the workflow file itself) and make its own required check pass while
disabling detection.

Protected paths:
    .github/workflows/*.yml
    scripts/check_bot_review.py
    scripts/check_deleted_symbols.py
    scripts/check_doc_gate.py
    scripts/check_schema_migrations.py
    scripts/check_retrofit_migrations.py
    scripts/check_manifests.py
    scripts/check_secret_ignores.py
    scripts/check_store_wiring.py
    .github/scripts/check_all_skip.py

Allow label:
    gate-integrity-allowed -- a human can set this to waive the gate when
    a workflow or script change is intentional and reviewed.

Exit codes:
    0  PASS -- no protected paths touched, or allow-label present.
    1  FAIL -- protected paths touched without allow-label.
    2  ERROR -- infrastructure failure (network, auth, 404, etc.).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.request import Request, urlopen

API = "https://api.github.com"

ALLOW_LABEL = "gate-integrity-allowed"

PROTECTED_PATHS = [
    ".github/workflows/",
    "scripts/check_bot_review.py",
    "scripts/check_deleted_symbols.py",
    "scripts/check_doc_gate.py",
    "scripts/check_schema_migrations.py",
    "scripts/check_retrofit_migrations.py",
    "scripts/check_manifests.py",
    "scripts/check_secret_ignores.py",
    "scripts/check_store_wiring.py",
    ".github/scripts/check_all_skip.py",
]

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_ERROR = 2


def _get_token() -> str | None:
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or None


def _api_get(url: str, token: str | None = None) -> list | dict | None:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "taos-gate-integrity",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"error: GET {url} failed: {e}", file=sys.stderr)
        return None
    if isinstance(data, dict) and data.get("message"):
        print(f"error: API response message: {data['message']}", file=sys.stderr)
        return None
    return data


def _has_allow_label(pr_data: dict) -> bool:
    labels = pr_data.get("labels", [])
    return any(label.get("name", "").lower() == ALLOW_LABEL.lower() for label in labels)


def _is_protected_path(filename: str) -> bool:
    for protected in PROTECTED_PATHS:
        if protected.endswith("/"):
            if filename.startswith(protected):
                return True
        else:
            if filename == protected:
                return True
    return False


def _get_changed_files(owner: str, repo: str, pr_number: int, token: str | None = None) -> list[str] | None:
    url = f"{API}/repos/{owner}/{repo}/pulls/{pr_number}/files"
    data = _api_get(url, token)
    if data is None:
        return None
    if not isinstance(data, list):
        return []
    return [item.get("filename", "") for item in data if item.get("filename")]


def _detect_repo() -> tuple[str, str]:
    env_repo = os.environ.get("GITHUB_REPOSITORY")
    if env_repo and "/" in env_repo:
        parts = env_repo.split("/")
        if len(parts) >= 2:
            return parts[0], parts[1]
    return "jaylfc", "taOS"


def check_gate_integrity(
    owner: str, repo: str, pr_number: int, token: str | None = None,
) -> tuple[int, str]:
    token = token or _get_token()

    pr_url = f"{API}/repos/{owner}/{repo}/pulls/{pr_number}"
    pr_data = _api_get(pr_url, token)
    if pr_data is None:
        return EXIT_ERROR, (
            f"error: could not fetch PR #{pr_number} metadata (exit {EXIT_ERROR})"
        )

    allow_label = _has_allow_label(pr_data)
    changed_files = _get_changed_files(owner, repo, pr_number, token)
    if changed_files is None:
        return EXIT_ERROR, (
            f"error: could not fetch changed files for PR #{pr_number} (exit {EXIT_ERROR})"
        )

    protected_touched = [f for f in changed_files if _is_protected_path(f)]

    if not protected_touched:
        return EXIT_OK, (
            f"PASS: no protected paths touched in PR #{pr_number} (exit {EXIT_OK})"
        )

    if allow_label:
        return EXIT_OK, (
            f"PASS: allow-label '{ALLOW_LABEL}' present on PR #{pr_number}, "
            f"protected paths touched: {protected_touched} (exit {EXIT_OK})"
        )

    return EXIT_FAIL, (
        f"FAIL: PR #{pr_number} touches protected paths without allow-label "
        f"'{ALLOW_LABEL}': {protected_touched} (exit {EXIT_FAIL})"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Gate-integrity check: fail PRs that touch protected gate paths.",
    )
    parser.add_argument("pr_number", type=int, help="GitHub PR number to check")
    parser.add_argument("--owner", default=None, help="Repo owner (default: auto-detect)")
    parser.add_argument("--repo", default=None, help="Repo name (default: auto-detect)")
    parser.add_argument("--token", default=None, help="GitHub token (default: $GH_TOKEN or $GITHUB_TOKEN)")
    args = parser.parse_args(argv)

    owner = args.owner
    repo = args.repo
    if not owner or not repo:
        detected_owner, detected_repo = _detect_repo()
        owner = owner or detected_owner
        repo = repo or detected_repo

    exit_code, message = check_gate_integrity(owner, repo, args.pr_number, args.token)
    print(message)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
