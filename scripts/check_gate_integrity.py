#!/usr/bin/env python3
"""Gate-integrity guard: a PR must not edit its own gate to green itself.

CLASS DEFECT being closed: every gate workflow triggered on `pull_request`
checks out the PR MERGE REF (`actions/checkout`'s default on that event) and
runs its checker script FROM THAT CHECKOUT. A PR can therefore edit the
checker -- and the workflow file itself, since `pull_request` runs the
workflow YAML from the merge ref too -- and make its own required check pass
while disabling detection. The gates affected by this class defect:

  - bot-review-gate        -> scripts/check_bot_review.py
  - deleted-symbols-gate     -> scripts/check_deleted_symbols.py
  - doc-gate                 -> scripts/check_doc_gate.py, check_schema_migrations.py,
                                check_retrofit_migrations.py, check_manifests.py
  - secret-ignores-gate      -> scripts/check_secret_ignores.py
  - store-wiring-gate        -> scripts/check_store_wiring.py
  - distrust-green-gate      -> .github/scripts/check_all_skip.py
  - security                 -> scripts/check_dependency_audit_ignores.py
  - evil-merge-gate          -> scripts/check_evil_merge.py (when present)

Fix direction #1 (the one that actually closes the class): this guard runs on
`pull_request_target`, which resolves BOTH the workflow file and this script
to the BASE ref (`origin/dev`). It does NOT check out or execute any PR code
-- it reads the PR's changed-files list and its labels through the GitHub REST
API only, using the base-ref version of this script. When the PR diff touches
a protected path, the run FAILS, unless the PR carries an explicit human-set
allow label so legitimate changes to CI/gates can still land.

Fix direction #2 (gates fetch their checker from the BASE ref) is NOT
sufficient on its own: it protects the checker script but not the workflow
file that drives it, which is why #1 is still required. This guard covers
both, so #2 is redundant for the covered surface.

Protected paths (the minimal gate surface a lane could corrupt):
  - `.github/workflows/`        the required-check workflow YAML itself
  - `.github/scripts/`          gate checkers collocated under `.github`
  - `scripts/check_*.py`        every repo gate checker lives here by convention
  - `docs/doc-gate.toml`        the doc-gate's rule DATA (a gate input, not code)
  - `pyproject.toml`            tool/test configuration the gates execute under
  - `tests/conftest.py`         fixture root every gate-adjacent test imports

Usage:
    python scripts/check_gate_integrity.py <pr-number> [--owner O] [--repo R] [--label LABEL]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

API = "https://api.github.com"
EXIT_OK = 0
EXIT_BLOCKED = 1
EXIT_ERROR = 2

# A human-placed label that explicitly waives the gate integrity check for a
# PR that legitimately edits CI/gates. It must be applied manually -- never by
# automation in a PR-lane -- so the waiver is a conscious human act.
DEFAULT_ALLOW_LABEL = "gate-integrity-allow"

# Protected path prefixes. A PR editing any file under these prefixes can
# silence or subvert a required check, so such edits are blocked unless the
# allow label is set.
PROTECTED_PREFIXES: tuple[str, ...] = (
    ".github/workflows/",
    ".github/scripts/",
    "docs/doc-gate.toml",
    "pyproject.toml",
    "tests/conftest.py",
)
# Every repo gate checker is named `scripts/check_*.py` by convention; that
# glob captures all current and future gate scripts in one rule so the guard
# never silently goes blind to a new gate.
GATE_SCRIPT_PREFIX = "scripts/check_"
GATE_SCRIPT_SUFFIX = ".py"

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class CheckResult:
    exit_code: int
    message: str


def _get_token() -> str | None:
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or None


def _api_get(url: str, token: str | None = None) -> list | None:
    """Fetch a paginated GitHub REST endpoint; None on infrastructure failure.

    None means "cannot see" (network error, auth failure, 404, bad JSON) so a
    cannot-see state is never mistaken for a clean pass -- the worker fails
    the run instead. A legitimately empty result returns []. Pagination is
    followed via the Link header, matching check_bot_review.py's contract.
    """
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "taos-gate-integrity",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    items: list = []
    page_url: str | None = url
    while page_url:
        req = urllib.request.Request(page_url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read().decode("utf-8")
                data = json.loads(raw)
                link = r.headers.get("Link", "")
        except urllib.error.HTTPError as e:
            print(f"error: GET {page_url} failed: HTTP {e.code} {e.reason}", file=sys.stderr)
            return None
        except Exception as e:  # any infra failure fails closed
            print(f"error: GET {page_url} failed: {e}", file=sys.stderr)
            return None
        if isinstance(data, dict) and data.get("message"):
            print(f"error: API response message: {data['message']}", file=sys.stderr)
            return None
        if isinstance(data, list):
            items.extend(data)
        elif isinstance(data, dict):
            items.append(data)
        page_url = None
        for part in link.split(","):
            if 'rel="next"' in part:
                match = re.search(r"<([^>]+)>", part)
                if match:
                    page_url = match.group(1)
                break
    return items


def is_protected(path: str) -> bool:
    """Return True if `path` is a gate file a PR must not edit without the
    allow label. Paths are normalised to forward slashes so Windows-style
    clients cannot dodge the check."""
    norm = path.replace("\\", "/")
    for prefix in PROTECTED_PREFIXES:
        if norm.startswith(prefix):
            return True
    if (
        norm.startswith(GATE_SCRIPT_PREFIX)
        and norm.endswith(GATE_SCRIPT_SUFFIX)
        and norm.count("/") == 1
    ):
        # `scripts/check_<x>.py` directly under scripts/ only; a nested file
        # like scripts/platform/check_foo.py is not matched by the flat
        # `scripts/check_*.py` convention.
        return True
    return False


def collect_pr_files(
    owner: str, repo: str, pr_number: int, token: str | None = None,
) -> tuple[list[str], int] | None:
    """Return (changed paths, API record count) for a PR (via the API, never
    a local checkout). A rename edits BOTH paths -- renaming a workflow out
    of .github/workflows/ disables it -- so `previous_filename` is included
    alongside `filename`. The record count is returned separately: the
    truncation check must compare records against `changed_files`, or every
    rename would read as a truncated listing. None on infrastructure
    failure."""
    data = _api_get(f"{API}/repos/{owner}/{repo}/pulls/{pr_number}/files", token)
    if data is None:
        return None
    records = [f for f in data if isinstance(f, dict)]
    paths: list[str] = []
    for f in records:
        paths.append(f.get("filename", ""))
        prev = f.get("previous_filename")
        if isinstance(prev, str) and prev:
            paths.append(prev)
    return paths, len(records)


def collect_pr_meta(
    owner: str, repo: str, pr_number: int, token: str | None = None,
) -> tuple[set[str], int] | None:
    """Return (label names, changed_files count) for the PR (via the API).

    None on infrastructure failure, and also when the payload carries no
    usable `changed_files` count: without it a truncated /files listing is
    undetectable, and cannot-see must never read as a clean pass."""
    data = _api_get(f"{API}/repos/{owner}/{repo}/pulls/{pr_number}", token)
    if data is None:
        return None
    pr = data[0] if isinstance(data, list) and data else {}
    if not isinstance(pr, dict):
        return None
    changed = pr.get("changed_files")
    if not isinstance(changed, int) or isinstance(changed, bool):
        return None
    labels = {
        lbl.get("name", "") for lbl in pr.get("labels", []) if isinstance(lbl, dict)
    }
    return labels, changed


def classify(
    files: list[str], labels: list[str] | set[str], allow_label: str,
) -> CheckResult:
    """Pure decision: given a PR's changed files and labels, decide pass/fail.

    RED  -- a protected gate file is in the diff and no allow label is set.
    GREEN -- no protected files in the diff, or the allow label waives them.
    """
    violations = sorted({f for f in files if is_protected(f)})
    label_set = set(labels or [])
    if not violations:
        return CheckResult(
            EXIT_OK,
            "gate-integrity: PASS -- PR touches no protected gate paths "
            f"({', '.join(PROTECTED_PREFIXES)} or {GATE_SCRIPT_PREFIX}*{GATE_SCRIPT_SUFFIX})",
        )
    if allow_label in label_set:
        return CheckResult(
            EXIT_OK,
            (
                f"gate-integrity: PASS -- {len(violations)} protected gate file(s) "
                f"touched but waived by human-set `{allow_label}` label: "
                f"{violations}"
            ),
        )
    return CheckResult(
        EXIT_BLOCKED,
        (
            f"gate-integrity: FAIL -- PR touches {len(violations)} protected "
            f"gate file(s) without the `{allow_label}` label: {violations}. "
            "A pull_request gate checks out the merge ref and runs its checker "
            "FROM that checkout, so a PR editing its own gate can green-pass "
            "the check that gates it. Set the label to acknowledge a human "
            "reviewed and approved the gate change."
        ),
    )


def check_gate_integrity(
    owner: str,
    repo: str,
    pr_number: int,
    allow_label: str = DEFAULT_ALLOW_LABEL,
    token: str | None = None,
) -> tuple[int, str]:
    """Fetch a PR's files + labels via the API and classify the integrity.

    Returns (exit_code, message). EXIT_ERROR (2) is returned when the GitHub
    API cannot be reached or returns an error, so a cannot-see state never
    reads as a clean pass.
    """
    token = token or _get_token()
    collected = collect_pr_files(owner, repo, pr_number, token)
    if collected is None:
        return EXIT_ERROR, (
            f"gate-integrity: error -- could not fetch changed files for "
            f"PR #{pr_number} (infrastructure failure, exit {EXIT_ERROR})"
        )
    meta = collect_pr_meta(owner, repo, pr_number, token)
    if meta is None:
        return EXIT_ERROR, (
            f"gate-integrity: error -- could not fetch labels/changed-file "
            f"count for PR #{pr_number} (infrastructure failure, exit {EXIT_ERROR})"
        )
    files, record_count = collected
    labels, changed_total = meta
    if record_count != changed_total:
        # GitHub's /files endpoint silently stops at 3,000 files; a diff the
        # gate cannot fully see cannot be cleared. Fail closed, not open.
        # Compared per-record, not per-path: renames contribute two paths
        # from one record.
        return EXIT_ERROR, (
            f"gate-integrity: error -- /files returned {record_count} record(s) but "
            f"the PR reports changed_files={changed_total}; the listing is "
            f"truncated or inconsistent, so unseen paths cannot be cleared "
            f"(exit {EXIT_ERROR})"
        )
    result = classify(files, labels, allow_label)
    return result.exit_code, result.message


def _detect_repo() -> tuple[str, str]:
    """Detect (owner, repo) from GITHUB_REPOSITORY env var or git remote.

    Falls back to ("jaylfc", "taOS") if neither is available. The repo is only
    needed to build the API URL, never for authentication.
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
            # Handles both https://github.com/owner/repo(.git) and
            # SCP-style git@github.com:owner/repo(.git) remotes.
            path = url.split("github.com", 1)[1].lstrip(":/").replace(".git", "").strip()
            parts = path.split("/")
            if len(parts) >= 2:
                return parts[0], parts[1]
    except (subprocess.CalledProcessError, FileNotFoundError, IndexError):
        pass

    return "jaylfc", "taOS"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "pr_number", type=int, help="GitHub PR number to inspect (files + labels only).",
    )
    parser.add_argument(
        "--owner", default=None, help="Repo owner (default: auto-detect).",
    )
    parser.add_argument(
        "--repo", default=None, help="Repo name (default: auto-detect).",
    )
    parser.add_argument(
        "--label", default=DEFAULT_ALLOW_LABEL,
        help=f"Allow label that waives the check (default: {DEFAULT_ALLOW_LABEL}).",
    )
    parser.add_argument(
        "--token", default=None,
        help="GitHub token (default: $GH_TOKEN or $GITHUB_TOKEN).",
    )
    args = parser.parse_args(argv)

    owner = args.owner
    repo = args.repo
    if not owner or not repo:
        detected_owner, detected_repo = _detect_repo()
        owner = owner or detected_owner
        repo = repo or detected_repo

    exit_code, message = check_gate_integrity(
        owner, repo, args.pr_number, args.label, args.token,
    )
    print(message)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
