#!/usr/bin/env python3
"""Reconcile fleet merge audit log against actual merges.

For each merge commit in the fleet's repos, assert a matching local audit
line exists. An unmatched merge is the signal that an unattributed action
occurred.

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


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout


def find_merge_commits(repo: Path) -> list[str]:
    """Return SHAs of merge commits in the repo."""
    out = _git(repo, "log", "--merges", "--format=%H")
    return [line.strip() for line in out.strip().splitlines() if line.strip()]


def read_audit_log(audit_file: Path) -> list[dict]:
    """Read JSONL audit log entries."""
    if not audit_file.exists():
        return []
    entries = []
    for line in audit_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def reconcile(repo: Path, audit_file: Path) -> list[str]:
    """Return SHAs of merge commits that lack a matching audit entry."""
    merge_shas = set(find_merge_commits(repo))
    audit_entries = read_audit_log(audit_file)
    audit_shas = {e.get("sha", "") for e in audit_entries if e.get("sha")}
    return sorted(sha for sha in merge_shas if sha not in audit_shas)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile fleet merge audit log against actual merges",
    )
    parser.add_argument("--repo", type=Path, required=True, help="Git repo root")
    parser.add_argument(
        "--audit-file", type=Path, required=True, help="JSONL audit log path",
    )
    args = parser.parse_args(argv)

    if not args.repo.is_dir():
        print(f"ERROR: repo not found: {args.repo}", file=sys.stderr)
        return EXIT_ERROR

    unmatched = reconcile(args.repo, args.audit_file)

    if unmatched:
        for sha in unmatched:
            short = sha[:12] if len(sha) >= 12 else sha
            print(f"UNMATCHED MERGE: {short} — no audit entry found")
        return EXIT_FAIL

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
