"""Robust git update helper for taOS.

Replaces a bare ``git pull --ff-only`` with a sequence that handles the four
real-world failure modes without silently discarding local work:

1. HEAD not on master  — tags the branch tip and checks out master first.
2. Dirty working tree  — stashes (including untracked files) before merging,
   then attempts to restore after.
3. Diverged history    — tags local HEAD, hard-resets to origin/master.
4. Network failure     — returns early before any destructive action.

Every destructive step is preceded by a recovery tag so the user can always
``git checkout taos-pre-update-…`` to recover.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class UpdateResult:
    previous_sha: str
    new_sha: str
    recovery_tag: Optional[str] = None
    stash_ref: Optional[str] = None
    stash_restored: bool = False
    branch_tag: Optional[str] = None
    message: str = ""


async def _run(args: list[str], cwd: Path) -> tuple[int, str]:
    """Run a subprocess safely (no shell) and return (returncode, output)."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(cwd),
    )
    stdout, _ = await proc.communicate()
    return proc.returncode or 0, (stdout.decode() if stdout else "")


async def update_to_master(project_dir: Path) -> UpdateResult:
    """Pull origin/master robustly, handling dirty trees, branches, and divergence.

    Returns an UpdateResult describing what happened. On network failure the
    result carries a descriptive message and no destructive action has been taken.
    """
    ts = int(time.time())

    # 1. Fetch — bail early if unreachable so we never destroy local state
    logger.info("update_runner: fetching origin/master")
    rc, out = await _run(["git", "fetch", "origin", "master"], project_dir)
    if rc != 0:
        logger.warning("update_runner: fetch failed: %s", out[:500])
        return UpdateResult(
            previous_sha="",
            new_sha="",
            message=f"Fetch failed — no changes applied. ({out.strip()[:200]})",
        )

    # 2. Probe current state
    _, branch_out = await _run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], project_dir
    )
    branch = branch_out.strip()

    _, sha_out = await _run(["git", "rev-parse", "HEAD"], project_dir)
    current_sha = sha_out.strip()
    short_sha = current_sha[:7]

    # Use -u so untracked files also trigger a stash (matches git stash -u).
    _, status_out = await _run(
        ["git", "status", "--porcelain", "-u"], project_dir
    )
    dirty = bool(status_out.strip())

    result = UpdateResult(previous_sha=current_sha, new_sha=current_sha)

    # 3. Switch to master if on another branch
    if branch != "master":
        safe_branch = branch.replace("/", "-")
        branch_tag = f"taos-pre-update-{safe_branch}-{ts}"
        logger.info("update_runner: not on master (%s); tagging as %s", branch, branch_tag)
        await _run(["git", "tag", branch_tag, "HEAD"], project_dir)
        await _run(["git", "checkout", "master"], project_dir)
        result.branch_tag = branch_tag

    # 4. Stash dirty working tree
    stash_msg = f"taos-update-{ts}"
    if dirty:
        logger.info("update_runner: stashing dirty working tree as '%s'", stash_msg)
        await _run(
            ["git", "stash", "push", "-u", "-m", stash_msg], project_dir
        )
        result.stash_ref = "stash@{0}"

    # 5. Attempt fast-forward merge
    logger.info("update_runner: attempting ff-only merge")
    rc_merge, merge_out = await _run(
        ["git", "merge", "--ff-only", "origin/master"], project_dir
    )

    # 6. Diverged — tag local HEAD then hard-reset
    if rc_merge != 0:
        recovery_tag = f"taos-pre-update-{short_sha}-{ts}"
        logger.info(
            "update_runner: diverged; tagging %s as %s then hard-resetting",
            short_sha,
            recovery_tag,
        )
        await _run(["git", "tag", recovery_tag, "HEAD"], project_dir)
        await _run(["git", "reset", "--hard", "origin/master"], project_dir)
        result.recovery_tag = recovery_tag

    # 7. Stash restore (best-effort)
    if result.stash_ref:
        logger.info("update_runner: restoring stash")
        rc_pop, pop_out = await _run(["git", "stash", "pop"], project_dir)
        if rc_pop == 0:
            result.stash_restored = True
        else:
            logger.warning(
                "update_runner: stash pop had conflicts — leaving stash in place. %s",
                pop_out[:300],
            )
            # Do NOT drop the stash on conflict.

    # 8. Record new sha and build human-readable summary
    _, new_sha_out = await _run(["git", "rev-parse", "HEAD"], project_dir)
    result.new_sha = new_sha_out.strip()

    parts: list[str] = [
        f"Updated {result.previous_sha[:7]} -> {result.new_sha[:7]}."
    ]
    if result.branch_tag:
        parts.append(f"Previous branch tip saved as tag '{result.branch_tag}'.")
    if result.recovery_tag:
        parts.append(f"Diverged commits saved as tag '{result.recovery_tag}'.")
    if result.stash_ref and not result.stash_restored:
        parts.append(
            f"Your local changes are preserved in stash (use `git stash list` to find it,"
            f" message: '{stash_msg}')."
        )
    elif result.stash_ref and result.stash_restored:
        parts.append("Local changes restored from stash.")

    result.message = " ".join(parts)
    logger.info("update_runner: done — %s", result.message)
    return result


async def switch_to_branch(branch: str, project_dir: Path) -> UpdateResult:
    """Switch the install to origin/<branch> safely.

    Fetches the branch (bails non-destructively on failure), tags the current
    tip for recovery, stashes a dirty tree, checks out (creating a local
    tracking branch if needed), ff-merges or hard-resets to origin/<branch>
    (tagging divergence), then restores the stash best-effort.
    """
    # Guard against flag-injection: `branch` reaches git argv (fetch/checkout)
    # and `origin/<branch>` refs. Callers validate too, but this is the unit
    # that actually runs git, so it validates as well (defence in depth).
    from tinyagentos.auto_update import is_valid_branch_name
    if not is_valid_branch_name(branch):
        return UpdateResult(previous_sha="", new_sha="",
                            message=f"Refused to switch: invalid branch name {branch!r}.")

    ts = int(time.time())

    logger.info("update_runner: fetching origin/%s", branch)
    # `--` forces `branch` to be read as a refspec, never an option.
    rc, out = await _run(["git", "fetch", "origin", "--", branch], project_dir)
    if rc != 0:
        logger.warning("update_runner: fetch failed: %s", out[:500])
        return UpdateResult(previous_sha="", new_sha="",
                            message=f"Fetch failed — no changes applied. ({out.strip()[:200]})")

    _, sha_out = await _run(["git", "rev-parse", "HEAD"], project_dir)
    current_sha = sha_out.strip()
    short_sha = current_sha[:7]

    _, status_out = await _run(["git", "status", "--porcelain", "-u"], project_dir)
    dirty = bool(status_out.strip())

    result = UpdateResult(previous_sha=current_sha, new_sha=current_sha)

    recovery_tag = f"taos-pre-switch-{short_sha}-{ts}"
    await _run(["git", "tag", recovery_tag, "HEAD"], project_dir)
    result.recovery_tag = recovery_tag

    stash_msg = f"taos-switch-{ts}"
    if dirty:
        await _run(["git", "stash", "push", "-u", "-m", stash_msg], project_dir)
        result.stash_ref = "stash@{0}"

    await _run(["git", "checkout", "-B", branch, f"origin/{branch}"], project_dir)

    rc_merge, _ = await _run(["git", "merge", "--ff-only", f"origin/{branch}"], project_dir)
    if rc_merge != 0:
        await _run(["git", "reset", "--hard", f"origin/{branch}"], project_dir)

    if result.stash_ref:
        rc_pop, pop_out = await _run(["git", "stash", "pop"], project_dir)
        if rc_pop == 0:
            result.stash_restored = True
        else:
            logger.warning("update_runner: stash pop conflicts — left in place. %s", pop_out[:300])

    _, new_sha_out = await _run(["git", "rev-parse", "HEAD"], project_dir)
    result.new_sha = new_sha_out.strip()
    result.message = f"Switched to {branch} ({result.previous_sha[:7]} -> {result.new_sha[:7]})."
    if result.recovery_tag:
        result.message += f" Previous tip saved as tag '{result.recovery_tag}'."
    if result.stash_ref and not result.stash_restored:
        result.message += f" Local changes preserved in stash ('{stash_msg}')."
    logger.info("update_runner: %s", result.message)
    return result
