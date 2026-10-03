"""Preflight checks for taOS updates.

Performs validation before attempting git fetches or updates to prevent
partial updates, confusing error messages, or permission issues.

Checks for:
1. Tracked branch missing on remote origin
2. Narrow remote.origin.fetch refspec that excludes the tracked branch
3. Files owned by non-service user that would block git operations
"""
from __future__ import annotations
import os
import subprocess
from pathlib import Path
from typing import NamedTuple, List
from tinyagentos.auto_update import resolve_tracked_branch


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


def _ls_remote_heads(remote: str, branch: str) -> tuple[bool, bool]:
    """Check if branch exists on remote and if remote is reachable.

    Returns (remote_reachable, branch_exists).
    """
    rc, out = _run_cmd(["git", "ls-remote", "--heads", remote, branch])
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


def _find_foreign_owned_files(project_dir: Path, excluded_prefixes: tuple[str, ...] = (".venv", "node_modules")) -> tuple[int, List[str]]:
    """Find files owned by a user other than the current effective user.

    Excludes files under excluded prefixes, files in data dirs (./data),
    and files owned by root (UID 0).
    """
    from os import geteuid
    from pwd import getpwuid

    current_uid = geteuid()
    if current_uid == 0:
        # Running as root - all files would be foreign
        return count_and_paths(project_dir, excluded_prefixes)

    count = 0
    paths = []

    for path in project_dir.rglob("*"):
        if path.is_file():
            try:
                # Skip excluded patterns
                if any(path.name.startswith(prefix) or f"/{prefix}/" in str(path) or str(path).endswith(f"/{prefix}") for prefix in excluded_prefixes):
                    continue

                # Skip files in data directory
                if "data" in path.parts:
                    continue

                # Skip temporary files and hidden files
                if path.name.startswith(".") or path.name.endswith(".tmp") or path.name.endswith(".temp"):
                    continue

                stat = path.stat()
                if stat.st_uid != current_uid:
                    count += 1
                    if len(paths) < 5:
                        try:
                            owner = getpwuid(stat.st_uid).pw_name
                        except (KeyError, ImportError):
                            owner = str(stat.st_uid)
                        paths.append(f"{path} (owned by {owner})")
            except (OSError, PermissionError):
                # Skip files we can't stat
                continue

    return count, paths


def count_and_paths(root: Path, excluded_prefixes: tuple[str, ...] = (".venv", "node_modules")) -> tuple[int, List[str]]:
    """Fallback when pwd module is not available."""
    from os import geteuid

    current_uid = geteuid()
    if current_uid == 0:
        return _count_all_files(root, excluded_prefixes)

    return _count_foreign_files_fallback(root, excluded_prefixes, current_uid)


def _count_all_files(root: Path, excluded_prefixes: tuple[str, ...]) -> tuple[int, List[str]]:
    """Count all files excluding excluded patterns."""
    count = 0
    paths = []

    for path in root.rglob("*"):
        if path.is_file():
            if any(path.name.startswith(prefix) or f"/{prefix}/" in str(path) or str(path).endswith(f"/{prefix}") for prefix in excluded_prefixes):
                continue
            if "data" in path.parts:
                continue
            if path.name.startswith(".") or path.name.endswith(".tmp") or path.name.endswith(".temp"):
                continue
            count += 1
            if len(paths) < 5:
                paths.append(str(path))

    return count, paths


def _count_foreign_files_fallback(root: Path, excluded_prefixes: tuple[str, ...], current_uid: int) -> tuple[int, List[str]]:
    """Count files owned by users other than current_uid."""
    count = 0
    paths = []

    for path in root.rglob("*"):
        if path.is_file():
            if any(path.name.startswith(prefix) or f"/{prefix}/" in str(path) or str(path).endswith(f"/{prefix}") for prefix in excluded_prefixes):
                continue
            if "data" in path.parts:
                continue
            if path.name.startswith(".") or path.name.endswith(".tmp") or path.name.endswith(".temp"):
                continue

            try:
                stat = path.stat()
                if stat.st_uid != current_uid:
                    count += 1
                    if len(paths) < 5:
                        paths.append(str(path))
            except (OSError, PermissionError):
                continue

    return count, paths


def check_preflight(project_dir: str | os.PathLike) -> List[PreflightIssue]:
    """Perform all preflight checks and return a list of problems found.

    Args:
        project_dir: The taOS project directory (where .git/ lives).

    Returns:
        List of PreflightIssue objects. Empty list means no problems.
    """
    project_dir = Path(project_dir)

    issues: List[PreflightIssue] = []

    # Resolve the tracked branch that this install will update
    branch = ""
    try:
        from tinyagentos.config import get_config_store
        settings_store = get_config_store()
        branch = resolve_tracked_branch(settings_store, project_dir)
    except Exception:
        # Try to get from git status if config store fails
        rc, out = _run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], project_dir)
        if rc == 0:
            branch = out.strip() if out else ""
        if not branch or branch == "HEAD":
            branch = "master"

    if not branch or not branch.isalnum():
        issues.append(
            PreflightIssue(
                code="invalid_tracked_branch",
                message=f"Cannot determine a valid tracked branch (found: {branch!r}).",
                repair="Set a valid tracked branch via 'taosctl set-update-channel <branch>'.",
            )
        )
        return issues

    # 1. Check if tracked branch exists on origin
    remote_reachable, branch_exists = _ls_remote_heads("origin", branch)
    if not remote_reachable:
        issues.append(
            PreflightIssue(
                code="branch_not_on_origin",
                message=f"Tracked branch '{branch}' is not present on origin (git ls-remote --heads origin {branch} failed).",
                repair=f"Ensure the branch '{branch}' exists on remote 'origin' (e.g., git push origin {branch}).",
            )
        )
    elif not branch_exists:
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
                repair=f"Auto-repairing: git config --add remote.origin.fetch 'refs/heads/{branch}:refs/remotes/origin/{branch}'",
            )
        )

    # 3. Check for foreign-owned files
    count, paths = _find_foreign_owned_files(project_dir)
    if count > 0:
        paths_str = "\n  ".join(paths[:5])
        if count > 5:
            paths_str += f"\n  ... and {count - 5} more files."

        issues.append(
            PreflightIssue(
                code="foreign_owned_files",
                message=f"{count} file(s) are owned by a different user (including: {paths_str}).",
                repair="Change file ownership to the service user (e.g., chown -R <user>:<group> .).",
            )
        )

    return issues


def auto_repair_narrow_refspec(project_dir: Path) -> bool:
    """Auto-repair narrow_fetch_refspec issue by adding the branch refspec.

    Returns True if repaired, False if already OK or cannot repair.
    """
    from tinyagentos.config import load_config
    config = load_config(project_dir / "config.yaml")
    branch = getattr(config, "tracked_branch", "master")

    if not branch:
        return False

    refspecs = _get_fetch_refspecs(project_dir)
    expected_ref = f"refs/heads/{branch}"

    # Check if already covered
    for refspec in refspecs:
        if refspec.startswith("+"):
            refspec = refspec[1:]
        if ":" in refspec:
            src = refspec.split(":", 1)[0]
        else:
            src = refspec

        if _covers_ref(expected_ref, [refspec]):
            return True

    # Add the refspec
    new_refspec = f"refs/heads/{branch}:refs/remotes/origin/{branch}"
    rc, out = _run_cmd(["git", "config", "--add", "remote.origin.fetch", new_refspec], project_dir)
    if rc == 0:
        return True
    else:
        return False
