"""Preflight checks for taOS updates.

Performs validation before attempting git fetches or updates to prevent
partial updates, confusing error messages, or permission issues.

Checks for:
1. Tracked branch missing on remote origin
2. Narrow remote.origin.fetch refspec that excludes the tracked branch
3. Files not writable by the service user that would block git operations
"""
from __future__ import annotations
import os
import subprocess
from pathlib import Path
from typing import NamedTuple, List


class PreflightIssue(NamedTuple):
    """Represents a preflight validation problem."""
    code: str
    message: str
    repair: str | None = None


def get_project_dir() -> Path:
    """Get the project directory (taOS root)."""
    return Path(__file__).parent.parent


def _run_cmd(cmd: List[str], cwd: Path | None = None) -> tuple[int, str]:
    """Run a command and return (returncode, output)."""
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode, result.stdout.strip() + ("\n" + result.stderr.strip() if result.stderr else "")
    except subprocess.TimeoutExpired:
        return -1, f"[TIMEOUT] command: {' '.join(cmd[:3])}..."
    except Exception as e:
        return -1, f"[ERROR] {e}"


def _ls_remote_heads(remote: str, branch: str, project_dir: Path) -> tuple[bool, bool]:
    """Check if branch exists on remote and if remote is reachable.

    Returns (remote_reachable, branch_exists).
    """
    rc, out = _run_cmd(["git", "ls-remote", "--heads", remote, branch], cwd=project_dir)
    if rc != 0:
        # Git ls-remote failed - assume remote is unreachable, not that branch is missing
        return False, False
    return True, branch in out


def _get_fetch_refspecs(project_dir: Path) -> List[str]:
    """Get all remote.origin.fetch refspecs."""
    rc, out = _run_cmd(["git", "config", "--get-all", "remote.origin.fetch"], project_dir)
    if rc != 0:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def _covers_ref(ref: str, refspecs: List[str]) -> bool:
    """Check if any refspec covers the given ref.

    A refspec covers a ref if:
    1. It's an exact match (refs/heads/branch)
    2. It's a glob like refs/heads/*
    """
    for refspec in refspecs:
        # Handle patterns like +refs/heads/branch:refs/remotes/origin/branch
        # or +refs/heads/*:refs/remotes/origin/*
        if refspec.startswith("+"):
            refspec = refspec[1:]
        if ":" in refspec:
            src = refspec.split(":", 1)[0]
        else:
            src = refspec

        # Check if it's a glob covering refs/heads/<branch>
        if src == ref:
            return True
        if src == "refs/heads/*":
            # Check if ref is refs/heads/<branch>
            if ref.startswith("refs/heads/") and len(ref) > len("refs/heads/"):
                return True
    return False


def _find_foreign_owned_files(project_dir: Path) -> tuple[int, List[str]]:
    """Find files not writable by the current effective user.

    Excludes files under .venv, venv, node_modules, data, __pycache__,
    and .git trees. When running as root, returns (0, []) because root
    can write any file.
    """
    if os.geteuid() == 0:
        return 0, []

    count = 0
    paths = []
    prune_dirs = {".venv", "venv", "node_modules", "data", "__pycache__", ".git"}

    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in prune_dirs]
        for fname in files:
            path = Path(root) / fname
            if path.name.startswith(".") or path.name.endswith(".tmp") or path.name.endswith(".temp"):
                continue
            try:
                if not os.access(path, os.W_OK):
                    count += 1
                    if len(paths) < 5:
                        paths.append(str(path))
            except OSError:
                continue

    return count, paths


def check_preflight(project_dir: str | os.PathLike, branch: str | None = None) -> List[PreflightIssue]:
    """Perform all preflight checks and return a list of problems found.

    Args:
        project_dir: The taOS project directory (where .git/ lives).
            A ``str`` or ``os.PathLike`` is accepted and normalised to a
            ``Path`` internally, so callers may pass either.
        branch: The tracked update channel branch to validate. When supplied
            and non-empty, this is used directly. When omitted, the checked-out
            branch is resolved via ``git rev-parse --abbrev-ref HEAD`` with a
            fallback to ``master``.

    Returns:
        List of PreflightIssue objects. Empty list means no problems.
    """
    project_dir = Path(project_dir)

    issues: List[PreflightIssue] = []

    # Resolve the tracked branch that this install will update
    if not branch:
        rc, out = _run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], project_dir)
        if rc == 0:
            branch = out.strip() if out else ""
        if not branch or branch == "HEAD":
            branch = "master"

    if not branch:
        issues.append(
            PreflightIssue(
                code="invalid_tracked_branch",
                message=f"Cannot determine a valid tracked branch (found: {branch!r}).",
                repair="Set a valid tracked branch via 'taosctl set-update-channel <branch>'.",
            )
        )
        return issues

    rc, _ = _run_cmd(["git", "check-ref-format", "--branch", branch], project_dir)
    if rc < 0:
        issues.append(
            PreflightIssue(
                code="invalid_tracked_branch",
                message=f"Cannot validate tracked branch '{branch}' (git check-ref-format failed).",
                repair="Ensure git is installed and accessible, then set a valid tracked branch via 'taosctl set-update-channel <branch>'.",
            )
        )
        return issues
    if rc != 0:
        issues.append(
            PreflightIssue(
                code="invalid_tracked_branch",
                message=f"Cannot determine a valid tracked branch (found: {branch!r}).",
                repair="Set a valid tracked branch via 'taosctl set-update-channel <branch>'.",
            )
        )
        return issues

    # 1. Check if tracked branch exists on origin
    remote_reachable, branch_exists = _ls_remote_heads("origin", branch, project_dir)
    if remote_reachable and not branch_exists:
        issues.append(
            PreflightIssue(
                code="branch_not_on_origin",
                message=f"Tracked branch '{branch}' is not present on origin (refs/heads/{branch}).",
                repair=f"Ensure the branch '{branch}' exists on remote 'origin' (e.g., git push origin {branch}).",
            )
        )

    # 2. Check if remote.origin.fetch covers refs/heads/<branch>
    refspecs = _get_fetch_refspecs(project_dir)
    expected_ref = f"refs/heads/{branch}"

    # Default git config is '+refs/heads/*:refs/remotes/origin/*'
    default_refspec = "+refs/heads/*:refs/remotes/origin/*"
    is_default_config = refspecs == [default_refspec] or refspecs == ["refs/heads/*:refs/remotes/origin/*"]

    if not refspecs:
        issues.append(
            PreflightIssue(
                code="narrow_fetch_refspec",
                message="Remote origin has no configured fetch refspecs (git config remote.origin.fetch).",
                repair="Re-initialize the clone: git init && git remote add origin <remote-url>",
            )
        )
    elif is_default_config:
        # Default glob covers all branches, including our tracked branch
        pass
    elif not _covers_ref(expected_ref, refspecs):
        issues.append(
            PreflightIssue(
                code="narrow_fetch_refspec",
                message=f"Remote.origin.fetch does not cover refs/heads/{branch} (narrow refspec configuration).",
                repair=f"Run: git config --add remote.origin.fetch '+refs/heads/{branch}:refs/remotes/origin/{branch}'",
            )
        )

    # 3. Check for files not writable by the service user
    count, paths = _find_foreign_owned_files(project_dir)
    if count > 0:
        paths_str = "\n  ".join(paths[:5])
        if count > 5:
            paths_str += f"\n  ... and {count - 5} more files."

        issues.append(
            PreflightIssue(
                code="foreign_owned_files",
                message=f"{count} file(s) are not writable by the service user (including: {paths_str}).",
                repair="Make the files writable by the service user (e.g., chmod -R u+w . or chown -R <user>:<group> .).",
            )
        )

    return issues
