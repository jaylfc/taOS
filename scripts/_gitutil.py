"""Shared git helpers for the gate scripts.

All diff-producing commands pass ``-c core.quotePath=false -z`` so non-ASCII
paths arrive unquoted and records are NUL-separated, removing the two failure
modes described in tsk-5wcj4k:
  * non-ASCII characters in a path silently escaped every rule because git's
    default quoting wrapped the path in double-quotes with octal escapes;
  * a path containing a literal TAB shifted the ``split("\\t")`` that was
    previously used to parse ``git diff --name-status`` output.
"""
from __future__ import annotations

import subprocess


class GitCommandError(Exception):
    """Raised when a git command fails, so infrastructure failures are
    distinguishable from genuine rule violations."""


def _run_git(args: list[str], repo_root) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _diff_name_status(repo_root, ref: str | None = None) -> str:
    range_arg = f"{ref}...HEAD" if ref else "--cached"
    return _run_git(
        ["-c", "core.quotePath=false", "diff", "-z", "--name-status", range_arg],
        repo_root,
    )


def _parse_name_status(output: str) -> list[tuple[str, str]]:
    changed: list[tuple[str, str]] = []
    for record in output.split("\0"):
        record = record.strip()
        if not record:
            continue
        parts = record.split("\t")
        status = parts[0]
        path = parts[-1]
        changed.append((status[0], path))
    return changed


def git_changed_staged(repo_root) -> list[tuple[str, str]]:
    return _parse_name_status(_diff_name_status(repo_root))


def git_changed_base(repo_root, base_ref: str) -> list[tuple[str, str]]:
    return _parse_name_status(_diff_name_status(repo_root, base_ref))


def git_diff_unified(repo_root, base_ref: str | None, path: str) -> str:
    range_arg = "--cached" if base_ref is None else f"{base_ref}...HEAD"
    return _run_git(
        ["-c", "core.quotePath=false", "diff", "-z", "--unified=0", range_arg, "--", path],
        repo_root,
    )
