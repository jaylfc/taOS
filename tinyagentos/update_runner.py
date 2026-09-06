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
    ok: bool = True  # False when a step failed and no (or partial) switch happened


def _record_rollback_target(project_dir, branch: str, sha: str, ts: int) -> None:
    """Persist the pre-update branch + commit for `taos rollback` (best effort).

    Always called before an update mutates the tree, so even a clean
    fast-forward (no recovery tag) leaves a restore point.
    """
    if not branch or not sha:
        return
    try:
        from tinyagentos.rollback import record_pre_update
        record_pre_update(project_dir, branch=branch, sha=sha, ts=ts)
    except Exception:  # noqa: BLE001
        logger.warning("update_runner: failed to record rollback target", exc_info=True)


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


async def update_to_master(
    project_dir: Path,
) -> UpdateResult:
    """Pull origin/master robustly, handling dirty trees, branches, and divergence.

    Returns an UpdateResult describing what happened. On network failure the
    result carries a descriptive message and no destructive action has been taken.

    GPG signature verification is handled upstream in ``switch_to_branch``
    (the production code path) and ``auto_update._verify_gpg`` (the notification
    path).  This function is an internal helper that does not duplicate those
    checks.
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

    merge_target = "origin/master"

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

    # Record the rollback target AFTER the dirty probe so the (gitignored) state
    # file never counts as a dirty working tree and triggers a spurious stash.
    _record_rollback_target(project_dir, branch, current_sha, ts)

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
    logger.info("update_runner: attempting ff-only merge to %s", merge_target)
    rc_merge, merge_out = await _run(
        ["git", "merge", "--ff-only", merge_target], project_dir
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
        rc_reset, reset_out = await _run(
            ["git", "reset", "--hard", merge_target], project_dir,
        )
        if rc_reset != 0:
            result.ok = False
            result.recovery_tag = recovery_tag
            result.message = (
                f"Merge failed (diverged) and recovery hard-reset also failed. "
                f"Local state preserved under tag '{recovery_tag}'. "
                f"({reset_out.strip()[:200]})"
            )
            return result
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


async def switch_to_branch(
    branch: str,
    project_dir: Path,
    gpg_fingerprint: Optional[str] = None,
    gpg_required: bool = False,
) -> UpdateResult:
    """Switch the install to origin/<branch> safely.

    Fetches the branch (bails non-destructively on failure), tags the current
    tip for recovery, stashes a dirty tree, checks out (creating a local
    tracking branch if needed), ff-merges or hard-resets to origin/<branch>
    (tagging divergence), then restores the stash best-effort.

    When *gpg_fingerprint* is provided, the tip of ``origin/<branch>`` is
    verified via ``git verify-commit`` after fetch and before any destructive
    step.  If *gpg_required* is True, a failed verification blocks the switch.
    """
    # Guard against flag-injection: `branch` reaches git argv (fetch/checkout)
    # and `origin/<branch>` refs. Callers validate too, but this is the unit
    # that actually runs git, so it validates as well (defence in depth).
    from tinyagentos.auto_update import is_valid_branch_name
    if not is_valid_branch_name(branch):
        return UpdateResult(previous_sha="", new_sha="", ok=False,
                            message=f"Refused to switch: invalid branch name {branch!r}.")

    ts = int(time.time())

    logger.info("update_runner: fetching origin/%s", branch)
    # `--` forces `branch` to be read as a refspec, never an option.
    rc, out = await _run(["git", "fetch", "origin", "--", branch], project_dir)
    if rc != 0:
        logger.warning("update_runner: fetch failed: %s", out[:500])
        return UpdateResult(previous_sha="", new_sha="", ok=False,
                            message=f"Fetch failed — no changes applied. ({out.strip()[:200]})")

    # ── GPG signature verification (defence-in-depth) ──────────────────
    merge_target = f"origin/{branch}"
    if gpg_required and not gpg_fingerprint:
        return UpdateResult(
            previous_sha="",
            new_sha="",
            ok=False,
            message=f"GPG verification required but no key fingerprint configured — switch blocked.",
        )
    if gpg_fingerprint:
        from tinyagentos.gpg_verify import verify_commit, ensure_key_available
        key_ok = await ensure_key_available(gpg_fingerprint)
        if not key_ok:
            logger.warning(
                "update_runner: cannot import GPG key %s — verification will fail",
                gpg_fingerprint[:16],
            )
        rc_gpg, gpg_out = await _run(
            ["git", "rev-parse", f"origin/{branch}"], project_dir,
        )
        if rc_gpg == 0:
            remote_sha = gpg_out.strip()
            gpg_result = await verify_commit(project_dir, remote_sha, gpg_fingerprint)
            if not gpg_result.ok:
                logger.warning("update_runner: GPG verification failed: %s", gpg_result.status)
                if gpg_required:
                    return UpdateResult(
                        previous_sha="",
                        new_sha="",
                        ok=False,
                        message=f"GPG signature verification failed — switch blocked. {gpg_result.status}",
                    )
                logger.warning("update_runner: GPG verification failed (warn-only) — proceeding")
            else:
                # Pin to the verified SHA so origin/<branch> cannot change
                # between verification and merge (no TOCTOU).
                merge_target = remote_sha
        elif gpg_required:
            # Could not resolve origin/<branch> — required GPG check is impossible,
            # abort rather than falling through to an unverified switch.
            logger.warning("update_runner: could not resolve origin/%s for GPG check (required)", branch)
            return UpdateResult(
                previous_sha="",
                new_sha="",
                ok=False,
                message=f"GPG verification required but could not resolve origin/{branch} — switch blocked.",
            )
        else:
            logger.warning("update_runner: could not resolve origin/%s for GPG check", branch)

    _, cur_branch_out = await _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], project_dir)
    cur_branch = cur_branch_out.strip()
    _, sha_out = await _run(["git", "rev-parse", "HEAD"], project_dir)
    current_sha = sha_out.strip()
    short_sha = current_sha[:7]

    _, status_out = await _run(["git", "status", "--porcelain", "-u"], project_dir)
    dirty = bool(status_out.strip())

    # After the dirty probe (see update_to_master) so the gitignored state file
    # never triggers a spurious stash.
    _record_rollback_target(project_dir, cur_branch, current_sha, ts)

    result = UpdateResult(previous_sha=current_sha, new_sha=current_sha)

    recovery_tag = f"taos-pre-switch-{short_sha}-{ts}"
    await _run(["git", "tag", recovery_tag, "HEAD"], project_dir)
    result.recovery_tag = recovery_tag

    stash_msg = f"taos-switch-{ts}"
    if dirty:
        rc_stash, stash_out = await _run(
            ["git", "stash", "push", "-u", "-m", stash_msg], project_dir
        )
        # A failed stash must NOT set stash_ref — otherwise the later `stash pop`
        # would apply an unrelated older stash. Abort before anything
        # destructive so the working tree is left exactly as we found it.
        if rc_stash != 0:
            logger.warning("update_runner: stash failed: %s", stash_out[:300])
            result.ok = False
            result.message = (
                f"Could not stash local changes — no switch performed. "
                f"({stash_out.strip()[:200]})"
            )
            return result
        result.stash_ref = "stash@{0}"

    rc_co, co_out = await _run(
        ["git", "checkout", "-B", branch, f"origin/{branch}"], project_dir
    )
    # A failed checkout must NOT fall through to merge/reset — a hard reset would
    # rewrite the CURRENT branch to origin/<target>. Restore the stash and bail.
    if rc_co != 0:
        logger.warning("update_runner: checkout failed: %s", co_out[:300])
        if result.stash_ref:
            rc_pop, _ = await _run(["git", "stash", "pop"], project_dir)
            result.stash_restored = rc_pop == 0
        result.ok = False
        result.message = (
            f"Checkout to {branch} failed — no switch performed. "
            f"({co_out.strip()[:200]})"
        )
        return result

    rc_merge, _ = await _run(["git", "merge", "--ff-only", merge_target], project_dir)
    if rc_merge != 0:
        rc_reset, reset_out = await _run(
            ["git", "reset", "--hard", merge_target], project_dir,
        )
        if rc_reset != 0:
            logger.warning("update_runner: hard-reset also failed: %s", reset_out[:300])
            if result.stash_ref:
                rc_pop, _ = await _run(["git", "stash", "pop"], project_dir)
                result.stash_restored = rc_pop == 0
            result.ok = False
            result.message = (
                f"Merge to {merge_target[:7]} failed and recovery hard-reset also failed. "
                f"Previous tip saved as tag '{result.recovery_tag}'. "
                f"({reset_out.strip()[:200]})"
            )
            return result

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
