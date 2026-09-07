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


async def _run(args: list[str], cwd: Path, timeout: float = 30) -> tuple[int, str]:
    """Run a subprocess safely (no shell) and return (returncode, output).

    Raises ``asyncio.TimeoutError`` if the process does not finish within
    *timeout* seconds; the child is killed before the exception propagates.

    Raises ``RuntimeError`` if the subprocess exits with a non-zero return code.
    """
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(cwd),
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        raise
    if proc.returncode != 0:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        raise RuntimeError(
            f"Command {args[0]!r} exited with non-zero exit code {proc.returncode}"
        )
    return proc.returncode, (stdout.decode() if stdout else "")


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
    try:
        rc, out = await _run(["git", "fetch", "origin", "--", branch], project_dir)
    except RuntimeError as exc:
        logger.warning("update_runner: fetch failed: %s", exc)
        return UpdateResult(previous_sha="", new_sha="", ok=False,
                            message=f"Fetch failed — no changes applied. ({exc})")
    if rc != 0:
        logger.warning("update_runner: fetch returned non-zero: %s", out[:500])
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
        try:
            rc_gpg, gpg_out = await _run(
                ["git", "rev-parse", f"origin/{branch}"], project_dir,
            )
        except RuntimeError as exc:
            rc_gpg = 1
            gpg_out = str(exc)
        if rc_gpg == 0:
            remote_sha = gpg_out.strip()
            try:
                gpg_result = await verify_commit(project_dir, remote_sha, gpg_fingerprint)
            except RuntimeError as exc:
                gpg_result = type("GpgResult", (), {"ok": False, "status": str(exc)})()
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
                merge_target = remote_sha
        elif gpg_required:
            logger.warning("update_runner: could not resolve origin/%s for GPG check (required)", branch)
            return UpdateResult(
                previous_sha="",
                new_sha="",
                ok=False,
                message=f"GPG verification required but could not resolve origin/{branch} — switch blocked.",
            )
        else:
            logger.warning("update_runner: could not resolve origin/%s for GPG check", branch)

    try:
        _, cur_branch_out = await _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], project_dir)
        cur_branch = cur_branch_out.strip()
        _, sha_out = await _run(["git", "rev-parse", "HEAD"], project_dir)
        current_sha = sha_out.strip()
    except RuntimeError as exc:
        logger.warning("update_runner: state probe failed: %s", exc)
        return UpdateResult(previous_sha="", new_sha="", ok=False,
                            message=f"Could not read current branch/sha — no switch performed. ({exc})")
    short_sha = current_sha[:7]

    try:
        _, status_out = await _run(["git", "status", "--porcelain", "-u"], project_dir)
        dirty = bool(status_out.strip())
    except RuntimeError as exc:
        logger.warning("update_runner: status probe failed: %s", exc)
        dirty = False

    # After the dirty probe so the gitignored state file
    # never triggers a spurious stash.
    _record_rollback_target(project_dir, cur_branch, current_sha, ts)

    result = UpdateResult(previous_sha=current_sha, new_sha=current_sha)

    recovery_tag = f"taos-pre-switch-{short_sha}-{ts}"
    try:
        await _run(["git", "tag", recovery_tag, "HEAD"], project_dir)
    except RuntimeError as exc:
        logger.warning("update_runner: recovery tag failed: %s", exc)
        return UpdateResult(
            previous_sha=current_sha, new_sha=current_sha, ok=False,
            message=f"Could not create recovery tag — no switch performed. ({exc})",
        )
    result.recovery_tag = recovery_tag

    stash_msg = f"taos-switch-{ts}"
    if dirty:
        try:
            await _run(
                ["git", "stash", "push", "-u", "-m", stash_msg], project_dir,
            )
        except RuntimeError as exc:
            logger.warning("update_runner: stash failed: %s", exc)
            result.ok = False
            result.message = (
                f"Could not stash local changes — no switch performed. ({exc})"
            )
            return result
        result.stash_ref = "stash@{0}"

    try:
        rc_co, co_out = await _run(
            ["git", "checkout", "-B", branch, f"origin/{branch}"], project_dir,
        )
    except RuntimeError as exc:
        logger.warning("update_runner: checkout failed: %s", exc)
        if result.stash_ref:
            try:
                rc_pop, _ = await _run(["git", "stash", "pop"], project_dir)
                result.stash_restored = rc_pop == 0
            except RuntimeError:
                pass
        result.ok = False
        result.message = (
            f"Checkout to {branch} failed — no switch performed. ({exc})"
        )
        return result
    if rc_co != 0:
        logger.warning("update_runner: checkout returned non-zero: %s", co_out[:300])
        if result.stash_ref:
            try:
                rc_pop, _ = await _run(["git", "stash", "pop"], project_dir)
                result.stash_restored = rc_pop == 0
            except RuntimeError:
                pass
        result.ok = False
        result.message = (
            f"Checkout to {branch} failed — no switch performed. ({co_out.strip()[:200]})"
        )
        return result

    try:
        rc_merge, _ = await _run(["git", "merge", "--ff-only", merge_target], project_dir)
    except RuntimeError:
        rc_merge = 1

    if rc_merge != 0:
        try:
            rc_reset, reset_out = await _run(
                ["git", "reset", "--hard", merge_target], project_dir,
            )
        except RuntimeError as exc:
            logger.warning("update_runner: hard-reset raised: %s", exc)
            if result.stash_ref:
                try:
                    rc_pop, _ = await _run(["git", "stash", "pop"], project_dir)
                    result.stash_restored = rc_pop == 0
                except RuntimeError:
                    pass
            result.ok = False
            result.message = (
                f"Merge to {merge_target[:7]} failed and recovery hard-reset raised. "
                f"Previous tip saved as tag '{result.recovery_tag}'. "
            )
            return result
        if rc_reset != 0:
            logger.warning("update_runner: hard-reset returned non-zero: %s", reset_out[:300])
            if result.stash_ref:
                try:
                    rc_pop, _ = await _run(["git", "stash", "pop"], project_dir)
                    result.stash_restored = rc_pop == 0
                except RuntimeError:
                    pass
            result.ok = False
            result.message = (
                f"Merge to {merge_target[:7]} failed and recovery hard-reset also failed. "
                f"Previous tip saved as tag '{result.recovery_tag}'. "
                f"({reset_out.strip()[:200]})"
            )
            return result

    if result.stash_ref:
        try:
            rc_pop, pop_out = await _run(["git", "stash", "pop"], project_dir)
        except RuntimeError:
            rc_pop = 1
        if rc_pop == 0:
            result.stash_restored = True
        else:
            logger.warning(
                "update_runner: stash pop conflicts — left in place. %s", pop_out[:300],
            )

    _, new_sha_out = await _run(["git", "rev-parse", "HEAD"], project_dir)
    result.new_sha = new_sha_out.strip()
    result.message = f"Switched to {branch} ({result.previous_sha[:7]} -> {result.new_sha[:7]})."
    if result.recovery_tag:
        result.message += f" Previous tip saved as tag '{result.recovery_tag}'."
    if result.stash_ref and not result.stash_restored:
        result.message += f" Local changes preserved in stash ('{stash_msg}')."
    logger.info("update_runner: %s", result.message)
    return result
