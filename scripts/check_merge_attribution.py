#!/usr/bin/env python3
"""Reconcile fleet merge audit log against actual merges from the GitHub API.

Enumerates merged PRs (since the cutoff) via `gh pr list --state merged` and
asserts each has a matching audit entry. An unmatched merge is the signal that
an unattributed action occurred -- e.g. a squash merge with no audit line.

Reconciliation is on the mergeCommit OID from the GitHub API (`gh pr view <n>
--json mergeCommit`), which for a squash merge is the new commit on the base
branch. Squash merges are visible because we enumerate from the API, not from
`git log --merges` (which never sees them).

A --cutoff (ISO timestamp or git SHA) keeps pre-adoption merges out of scope.

Exit codes:
    0  PASS  -- all merges have matching audit entries
    1  FAIL  -- one or more merges lack audit entries
    2  ERROR -- infrastructure failure
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_ERROR = 2


def _gh(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["gh", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed (exit {result.returncode}): {result.stderr.strip()}")
    return result.stdout


def _resolve_cutoff(repo: Path, cutoff: str) -> str:
    """Resolve a cutoff to an ISO-8601 timestamp.

    If *cutoff* looks like a git SHA (resolvable by git), return its commit
    timestamp. Otherwise return *cutoff* unchanged (assumed to be a date or
    full ISO-8601 string).
    """
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%cI", cutoff],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        )
        ts = result.stdout.strip()
        if ts:
            return ts
    except subprocess.CalledProcessError:
        pass
    return cutoff


def find_merged_prs(repo: Path, cutoff: str | None = None) -> list[dict]:
    """Return merged PRs from the GitHub API, optionally after *cutoff*.

    Enumerates via `gh pr list --state merged` so squash merges (which produce
    no merge commit in `git log --merges`) are visible. If *cutoff* is given,
    only PRs merged at or after the cutoff timestamp are returned.
    """
    cutoff_ts: str | None = None
    if cutoff:
        cutoff_ts = _resolve_cutoff(repo, cutoff)

    args: list[str] = [
        "pr", "list",
        "--state", "merged",
        "--json", "number,mergeCommit,mergedAt",
        "--limit", "1000",
    ]
    if cutoff_ts:
        args.extend(["--search", f"merged:>= {cutoff_ts}"])

    out = _gh(*args, cwd=repo)
    prs = json.loads(out)

    # Client-side filter as a safety net (the --search flag may be ignored
    # by a stub gh in tests, or may differ in real gh's date parsing).
    if cutoff_ts:
        prs = [p for p in prs if (p.get("mergedAt") or "") >= cutoff_ts]

    return prs


def read_audit_log(audit_file: Path) -> list[dict]:
    """Read JSONL audit log entries."""
    if not audit_file.exists():
        return []
    entries: list[dict] = []
    for line in audit_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def _extract_merge_commit_oid(pr: dict) -> str:
    """Extract the mergeCommit SHA from a PR dict.

    `gh` returns mergeCommit as a Commit object {oid: ...}; handle both
    that and a bare string.
    """
    mc = pr.get("mergeCommit")
    if isinstance(mc, dict):
        return mc.get("oid", "")
    if isinstance(mc, str):
        return mc
    return ""


def reconcile(repo: Path, audit_file: Path, cutoff: str | None = None) -> list[str]:
    """Return descriptions of merged PRs that lack a matching audit entry.

    Each description is ``"#{pr} {short_sha}"`` so the caller can print
    a human-readable unmatched-merge report.
    """
    merged_prs = find_merged_prs(repo, cutoff)
    audit_entries = read_audit_log(audit_file)
    audit_shas = {e.get("sha", "") for e in audit_entries if e.get("sha")}

    unmatched: list[str] = []
    for pr in merged_prs:
        sha = _extract_merge_commit_oid(pr)
        if sha and sha not in audit_shas:
            pr_num = pr.get("number", "unknown")
            short = sha[:12] if len(sha) >= 12 else sha
            unmatched.append(f"#{pr_num} {short}")
    return sorted(unmatched)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile fleet merge audit log against actual merges from the GitHub API",
    )
    parser.add_argument("--repo", type=Path, required=True, help="Git repo root (for gh auto-detect and git log)")
    parser.add_argument(
        "--audit-file",
        type=Path,
        required=True,
        help="JSONL audit log path",
    )
    parser.add_argument(
        "--cutoff",
        type=str,
        default=None,
        help="Only reconcile merges after this date (ISO 8601) or git SHA",
    )
    args = parser.parse_args(argv)

    if not args.repo.is_dir():
        print(f"ERROR: repo not found: {args.repo}", file=sys.stderr)
        return EXIT_ERROR

    try:
        unmatched = reconcile(args.repo, args.audit_file, args.cutoff)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if unmatched:
        for desc in unmatched:
            print(f"UNMATCHED MERGE: {desc} -- no audit entry found")
        return EXIT_FAIL

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
