#!/usr/bin/env python3
"""Evil-merge detection guard for test files.

For a PR whose head is a merge commit M with parents P1 and P2,
for every file under tests/ : compare the blob at M against the
blob produced by ``git merge-tree P1 P2``.  When they differ the
resolution invented content that neither side reviewed by the time
git would have produced on its own.

Scope is tests/ only so ordinary semantic hand-merges in source do not
produce false positives.  A resolution that takes one side wholesale
matches what git would produce and stays green; a genuine hand-merge
is exactly the case that deserves a human read.

Usage:
    python3 scripts/check_evil_merge.py --head HEAD
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


class OctopusMergeError(Exception):
    """Raised when ``head_ref`` is a merge commit with more than two parents.

    The guard's comparison logic (parent1 vs parent2 vs merge-tree baseline)
    is only defined for two-parent merges. An octopus merge (3+ parents) is
    not analyzable with that logic: content dropped or invented relative to
    the third-or-later parent would be invisible. Rather than silently
    checking only the first two parents, this is treated as a guard failure.
    """


class Violation:
    def __init__(
        self,
        path: str,
        merge_hash: str,
        parent1_hash: str,
        parent2_hash: str,
        merge_tree_hash: str,
    ) -> None:
        self.path = path
        self.merge_hash = merge_hash
        self.parent1_hash = parent1_hash
        self.parent2_hash = parent2_hash
        self.merge_tree_hash = merge_tree_hash


def _run_git(args: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd or REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _get_parents(repo_root: Path, ref: str) -> list[str]:
    """Return the list of parent SHAs for ``ref``."""
    out = _run_git(["rev-parse", f"{ref}^@"], cwd=repo_root)
    return [line for line in out.splitlines() if line.strip()]


def _parse_ls_tree_z(output: str) -> dict[str, str]:
    """Parse ``git ls-tree -r -z <ref>`` output into path-to-blob map.

    Each entry is ``<mode> SP <type> SP <hash> TAB <filename>\\0``.
    """
    result: dict[str, str] = {}
    for entry in output.split("\0"):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split("\t", 1)
        if len(parts) != 2:
            continue
        meta, name = parts
        blob_hash = meta.split(" ", 2)[2]
        result[name] = blob_hash
    return result


def _get_blob_hashes(repo_root: Path, ref: str, paths: list[str]) -> dict[str, str]:
    """Return a mapping of path to blob hash for every path in ``paths`` that
    exists at ``ref``, using a single ``git ls-tree`` call."""
    if not paths:
        return {}
    try:
        out = _run_git(
            ["ls-tree", "-r", "-z", ref, "tests/"],
            cwd=repo_root,
        )
    except subprocess.CalledProcessError:
        return {}
    all_blobs = _parse_ls_tree_z(out)
    return {p: all_blobs[p] for p in paths if p in all_blobs}


def _is_conflict_blob_line(line: str) -> bool:
    """Return True if ``line`` looks like a merge-tree conflict blob entry.

    Conflict entries have the form ``<mode> blob <hash> <stage_num>\\t<path>``
    where <stage_num> is 1, 2, or 3.
    """
    if not line.startswith("100"):
        return False
    parts = line.split(maxsplit=4)
    if len(parts) < 4 or parts[1] != "blob":
        return False
    try:
        int(parts[3])
    except ValueError:
        return False
    return True


def _parse_merge_tree_stdout(output: str, paths: list[str]) -> tuple[str, dict[str, str]]:
    """Parse ``git merge-tree <p1> <p2>`` output.

    Returns ``(tree_sha, path_to_blob_map)``.

    The first line is always the virtual tree SHA.  When parents conflict,
    subsequent lines are ``<mode> blob <hash> <stage>\\t<path>`` entries;
    stage-3 blobs (the result with conflict markers) are used as the
    per-file baseline.  For clean merges the blob map is empty and the
    caller should look up blobs from the tree SHA via ``ls-tree``.
    """
    lines = output.splitlines()
    if not lines:
        return "", {}

    tree_sha = lines[0].strip()

    wanted = set(paths)
    conflict_blobs: dict[str, str] = {}
    for line in lines[1:]:
        if not _is_conflict_blob_line(line):
            continue
        parts = line.split(maxsplit=4)
        if len(parts) < 5:
            continue
        blob_hash, stage_str, path = parts[2], parts[3], parts[4]
        if path not in wanted:
            continue
        if stage_str == "3":
            conflict_blobs[path] = blob_hash

    return tree_sha, conflict_blobs


def check_evil_merge(
    repo_root: Path,
    head_ref: str = "HEAD",
) -> list[Violation]:
    """Detect evil merges in test files under ``head_ref``.

    Returns an empty list when ``head_ref`` is not a merge commit or when
    every tests/ blob at head matches what ``git merge-tree`` would have
    produced.

    Raises ``OctopusMergeError`` when ``head_ref`` has more than two
    parents: the comparison below is only defined for two-parent merges,
    so an octopus merge is treated as a guard failure rather than silently
    analyzed against only the first two parents.
    """
    parents = _get_parents(repo_root, head_ref)
    if len(parents) < 2:
        return []

    merge_sha = _run_git(["rev-parse", head_ref], cwd=repo_root).strip()

    if len(parents) > 2:
        raise OctopusMergeError(
            f"octopus merge ({len(parents)} parents) is not analyzable; "
            f"refusing to pass: {merge_sha[:8]}"
        )

    parent1 = parents[0]
    parent2 = parents[1]

    # Candidate paths are the UNION of tests/ files at head and at both
    # parents. Scoping to head-only paths misses a merge that silently
    # deletes a test file both parents kept: that file is absent at head,
    # so a head-only path set would never even consider it.
    head_paths = _get_test_files(repo_root, head_ref)
    p1_paths = _get_test_files(repo_root, parent1)
    p2_paths = _get_test_files(repo_root, parent2)
    all_paths = sorted(set(head_paths) | set(p1_paths) | set(p2_paths))

    head_blobs = _get_blob_hashes(repo_root, head_ref, all_paths)
    p1_blobs = _get_blob_hashes(repo_root, parent1, all_paths)
    p2_blobs = _get_blob_hashes(repo_root, parent2, all_paths)

    merge_tree_result = subprocess.run(
        ["git", "merge-tree", parent1, parent2],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    merge_tree_out = merge_tree_result.stdout
    tree_sha, conflict_blobs = _parse_merge_tree_stdout(merge_tree_out, all_paths)

    if conflict_blobs:
        merge_tree_blobs = conflict_blobs
    elif tree_sha:
        merge_tree_blobs = _get_blob_hashes(repo_root, tree_sha, all_paths)
    else:
        merge_tree_blobs = {}

    # Capture --write-tree output for the tree SHA (may be empty on conflict).
    merge_tree_write = subprocess.run(
        ["git", "merge-tree", "--write-tree", parent1, parent2],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    merge_tree_sha = merge_tree_write.stdout.strip()

    violations: list[Violation] = []
    for path in all_paths:
        head_blob = head_blobs.get(path)
        if head_blob is None:
            # Absent at head. Clean only if it's an honest deletion: at
            # least one parent must have already dropped it. If BOTH
            # parents still carry it, the merge silently dropped a file
            # neither side removed - that's a violation.
            p1_blob = p1_blobs.get(path)
            p2_blob = p2_blobs.get(path)
            if p1_blob is not None and p2_blob is not None:
                violations.append(
                    Violation(path, merge_sha, parent1, parent2, merge_tree_sha)
                )
            continue

        expected = merge_tree_blobs.get(path)
        if expected is None:
            # Not in merge-tree output: deleted in both parents or absent
            # from conflict entries. Fall back to 'differs from both parents'.
            p1_blob = p1_blobs.get(path)
            p2_blob = p2_blobs.get(path)
            if head_blob != p1_blob and head_blob != p2_blob:
                violations.append(
                    Violation(path, merge_sha, parent1, parent2, merge_tree_sha)
                )
        elif head_blob != expected:
            violations.append(
                Violation(path, merge_sha, parent1, parent2, merge_tree_sha)
            )

    return violations


def _get_test_files(repo_root: Path, ref: str) -> list[str]:
    """Return every file path under ``tests/`` that exists at ``ref``."""
    try:
        out = _run_git(["ls-tree", "-r", "--name-only", ref, "tests/"], cwd=repo_root)
    except subprocess.CalledProcessError:
        return []
    return [line for line in out.splitlines() if line.strip()]


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
    except OctopusMergeError as exc:
        print(f"EVIL-MERGE FAIL: {exc}", file=sys.stderr)
        return 1
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
    print(f"EVIL-MERGE FAIL: {v.path} differs from merge-tree baseline")
    print(f"  merge         M  {v.merge_hash[:8]}")
    print(f"  merge-tree    T  {v.merge_tree_hash[:8]}")
    print(f"  parent1      P1  {v.parent1_hash[:8]}")
    print(f"  parent2      P2  {v.parent2_hash[:8]}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
