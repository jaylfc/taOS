"""Worker self-update orchestrator — checkpoint, install, restart, rollback.

Part of taOS #890: worker auto-update lifecycle. Coordinates with the
deploy helper (via passwordless sudo) for privileged operations and with
the worker agent for controller signaling.

Flow:
    checkpoint → drain → pull → install deps → restart
    (post-restart) → health-check → outcome signal | rollback
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# Path to the deploy helper installed by install-worker.sh.
DEPLOY_HELPER = "/usr/local/bin/taos-deploy-helper"

# How long to wait for the worker port to come back after restart (seconds).
POST_RESTART_HEALTH_TIMEOUT = 30

# How long after restart to wait before the first health probe (seconds).
# Gives backends (Ollama, llama.cpp, etc.) time to stabilise.
POST_RESTART_GRACE_PERIOD = 15

# Marker file written by the pre-restart phase so the post-restart
# startup hook knows an update was in progress.
_UPDATE_IN_PROGRESS_MARKER = "update-in-progress.json"


def _install_dir() -> Path:
    """Return the worker install directory (TAOS_INSTALL_DIR or default)."""
    env = os.environ.get("TAOS_INSTALL_DIR", "")
    if env.strip():
        return Path(env.strip())
    return Path.home() / ".local" / "share" / "tinyagentos-worker"


def _repo_dir() -> Path:
    """Return the taOS git checkout directory on this worker."""
    return _install_dir() / "tinyagentos"


def _venv_dir() -> Path:
    """Return the worker's virtualenv directory."""
    return _install_dir() / ".venv"


async def _run_helper(
    args: list[str],
    timeout: float = 600,
) -> dict:
    """Run a deploy-helper command via passwordless sudo.

    Uses ``asyncio.create_subprocess_exec`` (no shell) with a fixed
    binary path — same security pattern as ``deploy.py``.

    Returns a dict with keys: ok (bool), output (str), exit_code (int).
    """
    if not shutil.which(DEPLOY_HELPER):
        return {
            "ok": False,
            "output": f"deploy helper not found at {DEPLOY_HELPER}",
            "exit_code": -1,
        }

    cmd = ["sudo", DEPLOY_HELPER] + args
    logger.info("self-update: running %s", " ".join(cmd))

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout.decode("utf-8", errors="replace") if stdout else ""
        ok = proc.returncode == 0

        if ok:
            logger.info("self-update: '%s' completed", args[0])
        else:
            logger.error(
                "self-update: '%s' failed (exit %d): %s",
                args[0], proc.returncode, output[-500:],
            )

        return {
            "ok": ok,
            "output": output.strip(),
            "exit_code": proc.returncode or 0,
        }
    except asyncio.TimeoutError:
        logger.error("self-update: '%s' timed out after %.0fs", args[0], timeout)
        return {
            "ok": False,
            "output": f"timed out after {timeout:.0f}s",
            "exit_code": -1,
        }
    except Exception as exc:
        logger.error("self-update: '%s' failed: %s", args[0], exc)
        return {
            "ok": False,
            "output": str(exc),
            "exit_code": -1,
        }


async def _run_git(
    args: list[str],
    cwd: Path | None = None,
    timeout: float = 120,
) -> tuple[int, str]:
    """Run a git command safely (list of args, no shell).

    Returns (returncode, stdout_or_stderr).
    """
    repo = cwd or _repo_dir()
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(repo),
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except ProcessLookupError:
            pass
        logger.error("self-update: git %s timed out after %.0fs", args[0], timeout)
        return -1, f"git {args[0]} timed out after {timeout:.0f}s"
    return proc.returncode or 0, (stdout.decode("utf-8", errors="replace") if stdout else "")


def _detect_package_manager() -> str:
    """Return 'uv' if uv.lock exists in the repo, else 'pip'."""
    if (_repo_dir() / "uv.lock").exists():
        return "uv"
    return "pip"


def _write_update_marker(
    state_dir: Path,
    checkpoint_tag: str,
    from_sha: str,
    to_sha: str,
) -> None:
    """Write the in-progress marker so the post-restart hook knows to
    run health-check and signal the outcome."""
    import datetime
    marker = {
        "checkpoint_tag": checkpoint_tag,
        "from_sha": from_sha,
        "to_sha": to_sha,
        "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    marker_path = state_dir / _UPDATE_IN_PROGRESS_MARKER
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(json.dumps(marker, indent=2))


def read_update_marker(state_dir: Path) -> dict | None:
    """Read the in-progress update marker, if it exists. Returns None
    if no update is in progress."""
    marker_path = state_dir / _UPDATE_IN_PROGRESS_MARKER
    if not marker_path.exists():
        return None
    try:
        return json.loads(marker_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def clear_update_marker(state_dir: Path) -> None:
    """Delete the in-progress update marker — update complete (success
    or rollback handled)."""
    marker_path = state_dir / _UPDATE_IN_PROGRESS_MARKER
    marker_path.unlink(missing_ok=True)


async def create_checkpoint() -> dict:
    """Create a pre-update checkpoint via the deploy helper.

    Returns a dict with keys:
        ok (bool), checkpoint_tag (str), git_sha (str),
        output (str), exit_code (int).
    """
    result = await _run_helper(["checkpoint"])
    tag = ""
    if result["ok"]:
        # The deploy helper prints the tag to stdout.
        lines = result["output"].splitlines()
        # Last non-log line is the tag.
        for line in reversed(lines):
            line = line.strip()
            if line and not line.startswith("[taos-deploy]"):
                tag = line
                break

    # Get current SHA for the marker.
    rc, sha = await _run_git(["rev-parse", "HEAD"])
    current_sha = sha.strip() if rc == 0 else ""

    return {
        **result,
        "checkpoint_tag": tag,
        "git_sha": current_sha,
    }


async def pull_update(target_ref: str) -> dict:
    """Fetch and checkout the target ref (branch or tag).

    Args:
        target_ref: The git ref to check out (e.g. 'origin/master').

    Returns: dict with ok, output, exit_code.
    """
    repo = _repo_dir()
    branch = target_ref

    # Parse "origin/branch" into fetch + checkout.
    if "/" in target_ref:
        remote, remote_branch = target_ref.split("/", 1)
        rc, _ = await _run_git(
            ["fetch", "--quiet", remote, "--", remote_branch],
            timeout=120,
        )
        if rc != 0:
            return {
                "ok": False,
                "output": f"git fetch {remote} {remote_branch} failed",
                "exit_code": rc,
            }
        branch = target_ref
    else:
        # Plain branch name — fetch from origin, then check out
        # origin/<branch> so we get the remote's version, not a stale
        # local tracking branch.  No ``--`` separator: ``git fetch
        # origin refspec`` updates origin/refspec; ``git fetch origin
        # -- refspec`` may not in some git versions.
        rc, _ = await _run_git(
            ["fetch", "--quiet", "origin", target_ref],
            timeout=120,
        )
        if rc != 0:
            return {
                "ok": False,
                "output": f"git fetch origin {target_ref} failed",
                "exit_code": rc,
            }
        branch = f"origin/{target_ref}"

    rc, out = await _run_git(["checkout", "--quiet", branch])
    if rc != 0:
        return {"ok": False, "output": out, "exit_code": rc}

    # Fast-forward the local tracking branch so it matches the remote
    # we just checked out — avoids leaving the repo in detached HEAD
    # after a direct origin/<ref> checkout.
    if "/" in target_ref:
        local_branch = target_ref.split("/", 1)[1]
        await _run_git(
            ["branch", "-f", local_branch, branch],
            timeout=30,
        )
    elif target_ref and "/" not in target_ref:
        await _run_git(
            ["branch", "-f", target_ref, branch],
            timeout=30,
        )

    rc, sha_out = await _run_git(["rev-parse", "HEAD"])
    return {
        "ok": True,
        "output": f"checked out {branch} ({sha_out.strip()[:8]})",
        "exit_code": 0,
    }


async def update_dependencies() -> dict:
    """Install/update Python dependencies using the detected package manager.

    Detects uv vs pip and runs the appropriate install command.
    """
    pkg = _detect_package_manager()
    repo = _repo_dir()

    if pkg == "uv" and shutil.which("uv"):
        proc = await asyncio.create_subprocess_exec(
            "uv", "sync", "--frozen",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(repo),
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=300)
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass
            return {
                "ok": False,
                "output": "uv sync timed out after 300s",
                "exit_code": -1,
                "package_manager": "uv",
            }
        output = stdout.decode("utf-8", errors="replace") if stdout else ""
        return {
            "ok": proc.returncode == 0,
            "output": output,
            "exit_code": proc.returncode or 0,
            "package_manager": "uv",
        }

    # Default: pip
    venv = _venv_dir()
    pip = str(venv / "bin" / "pip")
    if not os.path.isfile(pip):
        return {
            "ok": False,
            "output": f"pip not found at {pip}",
            "exit_code": -1,
            "package_manager": "pip",
        }

    proc = await asyncio.create_subprocess_exec(
        pip, "install", "-q", "-e", f"{repo}[worker]",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=300)
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except ProcessLookupError:
            pass
        return {
            "ok": False,
            "output": "pip install timed out after 300s",
            "exit_code": -1,
            "package_manager": "pip",
        }
    output = stdout.decode("utf-8", errors="replace") if stdout else ""
    return {
        "ok": proc.returncode == 0,
        "output": output,
        "exit_code": proc.returncode or 0,
        "package_manager": "pip",
    }


async def run_migrations() -> dict:
    """Execute any pending DB migrations.

    On the worker, migrations are typically handled by the main
    application startup. This is a no-op placeholder — worker-side
    DB schema changes are applied when the new code runs its lifespan
    after restart.
    """
    logger.info("self-update: migrations handled by post-restart startup")
    return {"ok": True, "output": "migrations deferred to post-restart startup", "exit_code": 0}


async def restart_service() -> dict:
    """Restart the worker service via the deploy helper.

    WARNING: This kills the current process. The return value is
    best-effort — if the restart succeeds, we never see the result.
    """
    return await _run_helper(["restart-self"])


async def run_health_check() -> dict:
    """Run the deploy-helper health-check command.

    Called post-restart to verify the worker is healthy.
    """
    return await _run_helper(["health-check"])


async def rollback_to_checkpoint(checkpoint_tag: str | None = None) -> dict:
    """Restore the worker to the pre-update checkpoint.

    Args:
        checkpoint_tag: Git tag from the checkpoint, or None to read
                        from the manifest file.

    Returns: dict with ok, output, exit_code.
    """
    args = ["rollback"]
    if checkpoint_tag:
        args.append(checkpoint_tag)
    return await _run_helper(args)


async def signal_update_outcome(
    controller_url: str,
    worker_name: str,
    outcome: str,
    from_version: str,
    to_version: str,
    failure_reason: str = "",
    rollback_to: str = "",
    signing_key: bytes | None = None,
) -> int:
    """POST the update outcome to the controller.

    Sends ``POST /api/cluster/workers/{name}/update-outcome`` with
    the outcome payload. HMAC-signed if a signing key is provided.

    Returns the HTTP status code, or 0 on connection failure.
    """
    import httpx
    from tinyagentos.worker.pairing import sign_request_headers

    path = f"/api/cluster/workers/{worker_name}/update-outcome"
    body_data = {
        "name": worker_name,
        "outcome": outcome,
        "from_version": from_version,
        "to_version": to_version,
    }
    if failure_reason:
        body_data["failure_reason"] = failure_reason
    if rollback_to:
        body_data["rollback_to"] = rollback_to

    body = json.dumps(body_data).encode()
    headers = {"content-type": "application/json"}
    if signing_key:
        auth_headers = sign_request_headers(
            signing_key, worker_name, "POST", path, body
        )
        headers.update(auth_headers)
        headers["content-type"] = "application/json"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{controller_url.rstrip('/')}{path}",
                content=body,
                headers=headers,
            )
            logger.info(
                "self-update: outcome=%s reported to controller (status=%d)",
                outcome, resp.status_code,
            )
            return resp.status_code
    except Exception as exc:
        logger.error("self-update: failed to signal outcome: %s", exc)
        return 0


async def run_full_update(
    target_ref: str,
    controller_url: str,
    agent,  # WorkerAgent instance (for signaling)
    state_dir: Path,
    graceful: bool = True,
) -> dict:
    """Run the full worker self-update lifecycle.

    Sequence:
        1. Create pre-update checkpoint (git tag + manifest).
        2. Signal update-available to controller.
        3. Initiate self-drain (stop accepting new work).
        4. Wait for in-flight leases to complete (if graceful).
        5. Pull new code.
        6. Update dependencies.
        7. Run migrations.
        8. Write in-progress marker for post-restart hook.
        9. Restart the service (this kills us).

    Args:
        target_ref: Git ref to update to (e.g. 'origin/master').
        controller_url: The controller's base URL.
        agent: WorkerAgent instance for heartbeat/signaling.
        state_dir: Worker state directory for the update marker.
        graceful: If True, wait for in-flight work to drain.

    Returns a dict describing each phase result. The restart phase
    is fire-and-forget — if it succeeds we never return.
    """
    import datetime

    results: dict[str, dict] = {}
    worker_name = agent.name

    # ── Phase 1: Checkpoint ───────────────────────────────────────
    logger.info("self-update: phase 1 — creating checkpoint")
    cp = await create_checkpoint()
    results["checkpoint"] = cp
    if not cp["ok"]:
        logger.error("self-update: checkpoint failed — aborting update")
        return {"ok": False, "error": "checkpoint failed", "phases": results}

    checkpoint_tag = cp.get("checkpoint_tag", "")
    from_sha = cp.get("git_sha", "")

    # ── Phase 2: Signal update-available ──────────────────────────
    logger.info("self-update: phase 2 — signaling update-available")
    status = await agent.report_update_available(reason=f"target={target_ref}")
    results["signal_update_available"] = {"status_code": status}
    if status not in (200, 0):
        logger.warning(
            "self-update: update-available signal returned %d — continuing anyway",
            status,
        )

    # ── Phase 3: Initiate self-drain ──────────────────────────────
    logger.info("self-update: phase 3 — initiating self-drain")
    status = await agent.initiate_self_drain(reason=f"update to {target_ref}")
    results["initiate_drain"] = {"status_code": status}
    if status not in (200, 0):
        logger.warning(
            "self-update: self-drain signal returned %d — continuing anyway",
            status,
        )

    # ── Phase 4: Wait for drain (if graceful) ─────────────────────
    if graceful:
        logger.info("self-update: phase 4 — waiting for in-flight work to drain")
        drain_ok = await _wait_for_drain(agent, timeout=120)
        results["drain_wait"] = {"ok": drain_ok}
        if not drain_ok:
            logger.warning(
                "self-update: drain wait incomplete — proceeding with update "
                "(leases will be released by monitor loop timeout)"
            )
    else:
        results["drain_wait"] = {"ok": True, "forced": True}

    # ── Phase 5: Pull new code ────────────────────────────────────
    logger.info("self-update: phase 5 — pulling %s", target_ref)
    pull = await pull_update(target_ref)
    results["pull"] = pull
    if not pull["ok"]:
        logger.error("self-update: pull failed — aborting update")
        # Don't rollback yet — we haven't installed anything.
        await agent.notify_drain_complete()
        return {"ok": False, "error": "pull failed", "phases": results}

    # ── Phase 6: Update dependencies ──────────────────────────────
    logger.info("self-update: phase 6 — updating dependencies")
    deps = await update_dependencies()
    results["dependencies"] = deps
    if not deps["ok"]:
        logger.error("self-update: dependency update failed — rolling back")
        await rollback_to_checkpoint(checkpoint_tag)
        await agent.notify_drain_complete()
        return {"ok": False, "error": "dependency update failed", "phases": results}

    # ── Phase 7: Run migrations ───────────────────────────────────
    logger.info("self-update: phase 7 — running migrations")
    migs = await run_migrations()
    results["migrations"] = migs

    # ── Phase 8: Write update marker ──────────────────────────────
    logger.info("self-update: phase 8 — writing update marker")
    to_sha = ""
    rc, sha_out = await _run_git(["rev-parse", "HEAD"])
    if rc == 0:
        to_sha = sha_out.strip()

    _write_update_marker(state_dir, checkpoint_tag, from_sha, to_sha)
    results["marker"] = {
        "ok": True,
        "checkpoint_tag": checkpoint_tag,
        "from_sha": from_sha,
        "to_sha": to_sha,
    }

    # ── Phase 9: Restart ──────────────────────────────────────────
    logger.info("self-update: phase 9 — restarting service")
    restart = await restart_service()
    results["restart"] = restart
    # If we reach here, restart failed or returned synchronously.
    # In normal operation, the process is killed by the restart.
    if not restart["ok"]:
        logger.error("self-update: restart failed — rolling back")
        await rollback_to_checkpoint(checkpoint_tag)
        clear_update_marker(state_dir)
        await agent.notify_drain_complete()
        return {"ok": False, "error": "restart failed", "phases": results}

    return {"ok": True, "phases": results}


async def _wait_for_drain(agent, timeout: float = 120) -> bool:
    """Wait for in-flight work to complete before updating.

    Polls the heartbeat response; the controller stops routing new
    work once the worker is in draining status.  Each heartbeat
    response now includes a ``drain_complete`` field (taOS #890 C3)
    so the worker can detect when all leases are released and proceed
    without waiting the full timeout.

    Returns True when the drain is confirmed complete by the
    controller.  Returns False if the controller was never reachable
    during the wait window — the caller should decide whether to
    proceed.
    """
    logger.info("self-update: waiting up to %.0fs for drain", timeout)
    import httpx
    from tinyagentos.worker.pairing import sign_request_headers
    import json as _json

    controller = agent.controller_url
    name = agent.name
    key = agent._signing_key

    elapsed = 0.0
    interval = 5.0
    saw_ok = False
    while elapsed < timeout:
        await asyncio.sleep(interval)
        elapsed += interval

        # Send a heartbeat to keep the controller updated on our drain
        # status — this also lets us detect if the controller is still
        # reachable.
        try:
            status = await agent.heartbeat(status="draining", drain_reason="update")
            if status == 200:
                logger.debug(
                    "self-update: drain heartbeat ok (%.0fs elapsed)", elapsed
                )
                saw_ok = True
            else:
                logger.warning(
                    "self-update: drain heartbeat returned %d", status
                )
        except Exception:
            logger.warning("self-update: drain heartbeat failed — continuing")
            continue

        # Check the heartbeat response body for drain_complete.
        # We re-post a lightweight request so we can read the response
        # payload (heartbeat() only returns the status code).
        try:
            path = "/api/cluster/heartbeat"
            payload = _json.dumps({
                "name": name,
                "load": 0.0,
                "status": "draining",
                "drain_reason": "update",
            }).encode()
            headers = sign_request_headers(key, name, "POST", path, payload) if key else {}
            headers["content-type"] = "application/json"

            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.post(
                    f"{controller.rstrip('/')}{path}",
                    content=payload,
                    headers=headers,
                )
                if resp.status_code == 200:
                    body = resp.json()
                    if body.get("drain_complete"):
                        logger.info(
                            "self-update: drain confirmed complete by controller "
                            "(%.0fs elapsed)", elapsed,
                        )
                        return True
        except Exception:
            logger.debug("self-update: drain-complete check failed — continuing")

    logger.info(
        "self-update: drain wait complete (%.0fs elapsed, controller_reachable=%s)",
        elapsed, saw_ok,
    )
    return saw_ok


async def post_update_startup(
    controller_url: str,
    agent,  # WorkerAgent
    state_dir: Path,
) -> dict | None:
    """Run the post-restart health-check and outcome signaling.

    Called during worker startup when an update-in-progress marker
    is found. Returns the outcome dict or None if no update was
    in progress.

    On health-check failure, initiates a rollback.
    """
    marker = read_update_marker(state_dir)
    if marker is None:
        return None

    logger.info(
        "self-update: post-restart hook — update in progress: %s -> %s",
        marker.get("from_sha", "?")[:8],
        marker.get("to_sha", "?")[:8],
    )

    checkpoint_tag = marker.get("checkpoint_tag", "")
    from_sha = marker.get("from_sha", "")
    to_sha = marker.get("to_sha", "")

    # Wait for grace period to let backends stabilise.
    logger.info(
        "self-update: waiting %ds grace period for backends",
        POST_RESTART_GRACE_PERIOD,
    )
    await asyncio.sleep(POST_RESTART_GRACE_PERIOD)

    # ── Health check ──────────────────────────────────────────────
    logger.info("self-update: running post-restart health check")
    health = await run_health_check()

    if not health["ok"]:
        logger.error(
            "self-update: health check FAILED — rolling back to %s",
            checkpoint_tag,
        )
        # Attempt rollback
        rollback_result = await rollback_to_checkpoint(checkpoint_tag)

        # Signal rollback outcome (best-effort — we may not reach the
        # controller if networking is the problem).
        await signal_update_outcome(
            controller_url=controller_url,
            worker_name=agent.name,
            outcome="rollback",
            from_version=from_sha,
            to_version=to_sha,
            failure_reason=f"health-check: {health.get('output', 'unknown')}",
            rollback_to=from_sha,
            signing_key=agent._signing_key,
        )

        clear_update_marker(state_dir)
        return {
            "ok": False,
            "outcome": "rollback",
            "rollback": rollback_result,
            "health": health,
        }

    # ── Health check passed ───────────────────────────────────────
    logger.info("self-update: health check PASSED")
    clear_update_marker(state_dir)

    # Re-registering happens naturally via the agent's run loop —
    # the agent calls register() after heartbeat 404s are resolved.
    # We just need to signal the outcome.
    status = await signal_update_outcome(
        controller_url=controller_url,
        worker_name=agent.name,
        outcome="success",
        from_version=from_sha,
        to_version=to_sha,
        signing_key=agent._signing_key,
    )

    logger.info(
        "self-update: outcome=success reported (status=%d)", status
    )
    return {
        "ok": True,
        "outcome": "success",
        "from_sha": from_sha,
        "to_sha": to_sha,
        "signal_status": status,
    }
