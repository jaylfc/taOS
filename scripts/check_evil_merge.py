#!/usr/bin/env python3
"""Evil-merge detection guard for test files.

For a PR whose head is a merge commit M with parents P1 and P2,
compare every file under tests/ at M against the blob git would
have produced automatically via ``git merge-tree --write-tree P1 P2``.
Content a human introduced during resolution fails; content git
produced by itself stays green.

Scope is tests/ only so ordinary semantic hand-merges in source do not
produce false positives.  A resolution that takes one side wholesale
or lets git auto-merge matches the baseline and stays green; a genuine
hand-merge is exactly the case that deserves a human read.

Usage:
    python3 scripts/check_evil_merge.py --head HEAD
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


class Violation:
    def __init__(
        self,
        path: str,
        merge_hash: str,
        parent1_hash: str,
        parent2_hash: str,
    ) -> None:
        self.path = path
        self.merge_hash = merge_hash
        self.parent1_hash = parent1_hash
        self.parent2_hash = parent2_hash


def _run_git(args: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd or REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _get_blob_hash(repo_root: Path, ref: str, path: str) -> str | None:
    """Return the blob hash of ``path`` at ``ref``, or None if the path does
    not exist at that ref."""
    try:
        out = _run_git(["rev-parse", f"{ref}:{path}"], cwd=repo_root)
        return out.strip()
    except subprocess.CalledProcessError:
        return None


def _get_parents(repo_root: Path, ref: str) -> list[str]:
    """Return the list of parent SHAs for ``ref``."""
    out = _run_git(["rev-parse", f"{ref}^@"], cwd=repo_root)
    return [line for line in out.splitlines() if line.strip()]


def _get_blob_hashes_for_ref(repo_root: Path, ref: str) -> dict[str, str]:
    """Return a mapping of path -> blob hash for every file under ``tests/``
    that exists at ``ref``.  Uses a single ``git ls-tree -r -z`` call."""
    try:
        out = _run_git(["ls-tree", "-r", "-z", ref, "tests/"], cwd=repo_root)
    except subprocess.CalledProcessError:
        return {}
    result: dict[str, str] = {}
    for entry in out.split("\0"):
        entry = entry.strip()
        if not entry:
            continue
        meta, path = entry.split("\t", 1)
        _, _, blob_hash = meta.split(" ", 2)
        result[path] = blob_hash
    return result


def check_evil_merge(
    repo_root: Path,
    head_ref: str = "HEAD",
) -> list[Violation]:
    """Detect evil merges in test files under ``head_ref``.

    Returns an empty list when ``head_ref`` is not a merge commit or when
    every tests/ blob at head matches the blob git would have produced
    automatically.
    """
    parents = _get_parents(repo_root, head_ref)
    if len(parents) < 2:
        return []

    merge_sha = _run_git(["rev-parse", head_ref], cwd=repo_root).strip()
    parent1 = parents[0]
    parent2 = parents[1]

    merge_blobs = _get_blob_hashes_for_ref(repo_root, head_ref)
    p1_blobs = _get_blob_hashes_for_ref(repo_root, parent1)
    p2_blobs = _get_blob_hashes_for_ref(repo_root, parent2)

    try:
        auto_tree = _run_git(
            ["merge-tree", "--write-tree", parent1, parent2],
            cwd=repo_root,
        ).strip()
    except subprocess.CalledProcessError:
        auto_tree = None

    if auto_tree:
        auto_blobs = _get_blob_hashes_for_ref(repo_root, auto_tree)
        use_new_predicate = True
    else:
        use_new_predicate = False

    violations: list[Violation] = []
    for path, actual_blob in merge_blobs.items():
        if use_new_predicate:
            auto_blob = auto_blobs.get(path)
            if auto_blob is None:
                p1_blob = p1_blobs.get(path)
                p2_blob = p2_blobs.get(path)
                if p1_blob is None and p2_blob is None:
                    continue
                violations.append(Violation(path, merge_sha, parent1, parent2))
            elif actual_blob != auto_blob:
                violations.append(Violation(path, merge_sha, parent1, parent2))
        else:
            p1_blob = p1_blobs.get(path)
            p2_blob = p2_blobs.get(path)
            if actual_blob != p1_blob and actual_blob != p2_blob:
                violations.append(Violation(path, merge_sha, parent1, parent2))

    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--head",
        default="HEAD",
        help="Git ref to check (default: HEAD)",
    )
    args = parser.parse_args(argv)

    try:
        violations = check_evil_merge(REPO_ROOT, args.head)
    except subprocess.CalledProcessError as exc:
        print(
            f"evil-merge guard: git error: {exc.stderr.strip()}",
            file=sys.stderr,
        )
        return 2

    if not violations:
        print("evil-merge guard: clean")
        return 0

    v = violations[0]
    print(f"EVIL-MERGE FAIL: {v.path} differs from automatic merge baseline")
    print(f"  merge   M  {v.merge_hash[:8]}")
    print(f"  parent1 P1 {v.parent1_hash[:8]}")
    print(f"  parent2 P2 {v.parent2_hash[:8]}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
