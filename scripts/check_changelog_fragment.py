#!/usr/bin/env python3
"""Changelog fragment gate.

Blocks PRs that make non-test changes under `tinyagentos/` or `desktop/src/`
without adding a changelog fragment (changelog.d/<pr>-<slug>.md or
changelog.d/tsk-<cardid>-<slug>.md) or editing CHANGELOG.md.

Escape hatches (must leave a record with a reason):
- PR label: `no-changelog-needed`
- PR body trailer: `Changelog-Not-Needed: <why>`

Usage:
    python scripts/check_changelog_fragment.py --base origin/dev
    python scripts/check_changelog_fragment.py --base origin/dev --pr-labels "label1,label2"
    python scripts/check_changelog_fragment.py --base origin/dev --pr-body "Changelog-Not-Needed: reason"
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gitutil import diff_name_status_z, run_git  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent

# Exit codes
EXIT_OK = 0
EXIT_VIOLATION = 1
EXIT_USAGE_ERROR = 2
EXIT_GIT_ERROR = 4

# Fragment filename patterns from docs/changelog-fragments.md
_FRAGMENT_PR_PATTERN = re.compile(r"^changelog\.d/\d+-[^/]+\.md$")
_FRAGMENT_TASK_PATTERN = re.compile(r"^changelog\.d/tsk-[^/]+-[^/]+\.md$")

# Escape hatch constants
PR_LABEL_ESCAPE = "no-changelog-needed"
PR_BODY_TRAILER = "Changelog-Not-Needed:"


def _is_test_path(path: str) -> bool:
    """A test file is never a structural feature change.

    Covers Python test_*.py modules, co-located __tests__/ directories,
    and frontend *.test.* / *.spec.* files. Matches check_doc_gate.py behavior.
    """
    base = path.rsplit("/", 1)[-1]
    if "/__tests__/" in path:
        return True
    if base.startswith("test_") and base.endswith(".py"):
        return True
    return base.endswith(
        (".test.tsx", ".test.ts", ".test.jsx", ".test.js", ".spec.tsx", ".spec.ts")
    )


def _is_fragment_file(path: str) -> bool:
    """Check if a path is a valid changelog fragment file.

    Valid patterns (from docs/changelog-fragments.md):
    - changelog.d/<pr>-<slug>.md
    - changelog.d/tsk-<cardid>-<slug>.md
    """
    return bool(_FRAGMENT_PR_PATTERN.match(path) or _FRAGMENT_TASK_PATTERN.match(path))


def _has_changelog_fragment_or_edit(changed: list[tuple[str, str]]) -> bool:
    """Check if the changeset adds a valid fragment or edits CHANGELOG.md."""
    for status, path in changed:
        if status in ("A", "M") and path == "CHANGELOG.md":
            return True
        if status == "A" and _is_fragment_file(path):
            return True
    return False


def _has_non_test_feature_change(changed: list[tuple[str, str]]) -> bool:
    """Check if the changeset has any non-test change under tinyagentos/ or desktop/src/."""
    for status, path in changed:
        if status in ("A", "M", "D", "R", "C"):
            if (path.startswith("tinyagentos/") or path.startswith("desktop/src/")) and not _is_test_path(path):
                return True
    return False


def _check_fragment_requirement(
    changed: list[tuple[str, str]],
    commit_messages: list[str],
    pr_labels: set[str],
    pr_body: str,
) -> list[str]:
    """Core logic: check if changelog fragment is required and present.

    Returns list of failure messages (empty = pass).
    """
    failures: list[str] = []

    # Check escape hatches first
    if PR_LABEL_ESCAPE in pr_labels:
        print(f"changelog-gate: escape hatch used via PR label '{PR_LABEL_ESCAPE}'")
        return failures

    if pr_body:
        for line in pr_body.splitlines():
            stripped = line.strip()
            if stripped.startswith(PR_BODY_TRAILER):
                reason = stripped[len(PR_BODY_TRAILER):].strip()
                if reason:
                    print(f"changelog-gate: escape hatch used via PR body trailer: {reason}")
                    return failures

    # Check if there's a non-test feature change
    if not _has_non_test_feature_change(changed):
        return failures  # No feature change, no fragment needed

    # Check if fragment or CHANGELOG.md edit is present
    if _has_changelog_fragment_or_edit(changed):
        return failures

    # Find the triggering paths for the error message
    trigger_paths = [
        path for status, path in changed
        if status in ("A", "M", "D", "R", "C")
        and (path.startswith("tinyagentos/") or path.startswith("desktop/src/"))
        and not _is_test_path(path)
    ]

    failures.append(
        f"non-test change under tinyagentos/ or desktop/src/ requires a changelog fragment: "
        f"{', '.join(trigger_paths)} (add changelog.d/<pr>-<slug>.md or changelog.d/tsk-<cardid>-<slug>.md, "
        f"or edit CHANGELOG.md, or use escape hatch: PR label '{PR_LABEL_ESCAPE}' or "
        f"PR body trailer '{PR_BODY_TRAILER} <why>')"
    )
    return failures


class GitCommandError(Exception):
    """Raised when a git command fails, so infrastructure failures are distinguishable."""


def _run_git(args: list[str], ref: str | None = None) -> str:
    try:
        return run_git(args, cwd=REPO_ROOT)
    except subprocess.CalledProcessError:
        msg = f"git {' '.join(args)} failed"
        if ref:
            msg += f" (ref: {ref})"
        raise GitCommandError(msg) from None


def _git_changed_base(base_ref: str) -> list[tuple[str, str]]:
    try:
        return diff_name_status_z(REPO_ROOT, base_ref=base_ref)
    except subprocess.CalledProcessError:
        raise GitCommandError(f"git diff --name-status {base_ref}...HEAD failed (ref: {base_ref})") from None


def _git_commit_messages(base_ref: str) -> list[str]:
    out = _run_git(["log", f"{base_ref}..HEAD", "--format=%B%x00"], ref=base_ref)
    return [m for m in out.split("\x00") if m.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="Base ref to compare against (e.g., origin/dev)")
    parser.add_argument("--pr-labels", default="", help="Comma-separated list of PR labels")
    parser.add_argument("--pr-body", default="", help="PR body text for trailer detection")
    args = parser.parse_args(argv)

    pr_labels = {label.strip() for label in args.pr_labels.split(",") if label.strip()}

    try:
        changed = _git_changed_base(args.base)
        commit_messages = _git_commit_messages(args.base)
    except GitCommandError as e:
        print(f"changelog-gate: git error: {e}", file=sys.stderr)
        return EXIT_GIT_ERROR

    failures = _check_fragment_requirement(changed, commit_messages, pr_labels, args.pr_body)

    if not failures:
        print("changelog-gate: clean")
        return EXIT_OK

    for failure in failures:
        print(f"CHANGELOG-GATE FAIL: {failure}")
    return EXIT_VIOLATION


if __name__ == "__main__":
    sys.exit(main())