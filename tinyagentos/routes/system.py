from __future__ import annotations

import asyncio
import logging
import os
import sys
from dataclasses import asdict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from tinyagentos.restart_orchestrator import write_pending_restart, read_pending_restart

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/system/prepare-shutdown")
async def prepare_shutdown(request: Request):
    """Gracefully prepare all agents for shutdown. Used by systemd stop hook."""
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is None:
        return JSONResponse({"error": "orchestrator not available"}, status_code=503)
    report = await orchestrator.prepare("all", "system-shutdown")
    return {"status": "ready", "report": report}


@router.post("/api/system/restart/prepare")
async def prepare_restart(request: Request):
    """Restart just the controller process.

    Agents and LiteLLM run independently and stay up across a controller
    restart, so there's nothing to drain — the restart is a ~5s uvicorn
    bounce. Framework-side retry/backoff (tracked separately) covers the
    brief window where controller-bound calls fail.
    """
    # Record target SHA so the boot-time check can confirm the update took.
    auto_updater = getattr(request.app.state, "auto_updater", None)
    target_sha = ""
    if auto_updater is not None:
        try:
            target_sha = await auto_updater._current_commit()
        except Exception:
            pass
    if target_sha:
        write_pending_restart(target_sha)

    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is not None:
        orchestrator._status = {
            "phase": "restarting",
            "reason": "update",
            "started_at": int(__import__("time").time()),
            "agents": {},
        }

    asyncio.create_task(_do_restart(request.app.state))
    return {"status": "restarting"}


async def _do_restart(app_state) -> None:
    await asyncio.sleep(2)

    notif = getattr(app_state, "notifications", None)

    async def _emit_fail(msg: str) -> None:
        if notif:
            await notif.add(
                title="Couldn't auto-restart — please restart manually",
                message=msg,
                level="error",
                source="system.lifecycle",
            )

    # 1. systemd
    if os.environ.get("INVOCATION_ID") or os.path.exists("/run/systemd/system"):
        for svc in ("taos.service", "tinyagentos.service"):
            for scope_args in (["systemctl", "--user", "is-active", svc], ["systemctl", "is-active", svc]):
                try:
                    proc = await asyncio.create_subprocess_exec(
                        *scope_args,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await proc.wait()
                    if proc.returncode == 0:
                        restart_args = (
                            ["systemctl", "--user", "restart", svc]
                            if "--user" in scope_args
                            else ["systemctl", "restart", svc]
                        )
                        restart_proc = await asyncio.create_subprocess_exec(
                            *restart_args,
                            stdout=asyncio.subprocess.DEVNULL,
                            stderr=asyncio.subprocess.DEVNULL,
                        )
                        await restart_proc.wait()
                        if restart_proc.returncode == 0:
                            # systemctl restart succeeded — it will kill and
                            # relaunch us; exit cleanly so the new invocation
                            # starts fresh under the same systemd unit.
                            os._exit(0)
                        # systemctl restart failed (e.g. interactive auth required).
                        # Fall through to os._exit so systemd's Restart=always
                        # picks us back up with the updated code on disk.
                        os._exit(1)
                except Exception:
                    pass

    # 2. Docker
    if os.path.exists("/.dockerenv"):
        os._exit(0)

    # 3. execv (no service manager — replace ourselves in-place)
    try:
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as exc:
        await _emit_fail(str(exc))


# AI-stack services a recovery action restarts (issue #1743). These are the
# local inference backends that can stall independently of the controller:
# rkllama (NPU model server, installed as a user unit) and qmd (shared model
# provider, a system unit). The controller itself is deliberately NOT here --
# bouncing it is the separate /api/system/restart/prepare path.
AI_STACK_UNITS = ("rkllama.service", "qmd.service")


def _is_admin_or_local_token(request: Request) -> bool:
    """True if the caller is an admin session or presented the host local token.

    Restarting host services is privileged (it runs ``systemctl restart``), so
    a plain non-admin user session must never reach it. ``AuthMiddleware`` sets
    both signals on ``request.state`` (see
    ``tinyagentos/routes/skill_exec.py::_is_admin_or_local_token`` for the full
    rationale). The ``system`` router is not gated at the router level, so this
    handler guards itself.
    """
    if getattr(request.state, "is_admin", False):
        return True
    return getattr(request.state, "via", None) == "local_token"


async def _systemctl_rc(args: list[str]) -> int:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
    return proc.returncode


async def _restart_ai_unit(unit: str) -> dict:
    """Restart one AI-stack systemd unit, resolving user vs system scope.

    Fails soft: a unit that is not installed, or that the service user lacks
    rights to restart (polkit / interactive auth), is reported rather than
    raised, so restarting one backend still helps when the other cannot be
    touched. Scope is resolved with ``systemctl [--user] cat`` (returns 0 iff
    the unit file exists) so a dead/crashed unit -- exactly what recovery
    targets -- is still restarted, unlike an is-active probe.
    """
    if not (os.environ.get("INVOCATION_ID") or os.path.exists("/run/systemd/system")):
        return {"unit": unit, "ok": False, "detail": "systemd not available on host"}

    # Resolve scope. Guarded so a missing/unusable systemctl is reported rather
    # than raised (keeps the documented "reported, not raised" contract).
    scope_flag: list[str] | None = None
    try:
        for candidate in (["--user"], []):
            if await _systemctl_rc(["systemctl", *candidate, "cat", unit]) == 0:
                scope_flag = candidate
                break
    except Exception as exc:  # pragma: no cover - defensive
        return {"unit": unit, "ok": False, "detail": f"systemctl unavailable: {str(exc)[:160]}"}
    if scope_flag is None:
        return {"unit": unit, "ok": False, "detail": "not installed"}

    scope_name = "user" if scope_flag else "system"
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemctl",
            *scope_flag,
            "restart",
            unit,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=45)
    except asyncio.TimeoutError:
        # Kill and reap the child so a hung systemctl is not orphaned.
        if proc is not None:
            try:
                proc.kill()
                await proc.wait()
            except Exception:  # pragma: no cover - defensive
                pass
        return {"unit": unit, "ok": False, "scope": scope_name, "detail": "restart timed out"}
    except Exception as exc:  # pragma: no cover - defensive
        return {"unit": unit, "ok": False, "scope": scope_name, "detail": str(exc)[:200]}

    if proc.returncode == 0:
        return {"unit": unit, "ok": True, "scope": scope_name}
    detail = (stderr.decode(errors="replace").strip() or f"exit code {proc.returncode}")[:200]
    return {"unit": unit, "ok": False, "scope": scope_name, "detail": detail}


@router.post("/api/system/ai-stack/restart")
async def restart_ai_stack(request: Request):
    """Restart the local AI inference stack (rkllama + qmd) -- issue #1743.

    A recovery action for edge devices where a model stalls (endless NPU
    generation, unresponsive inference) while the controller itself stays up.
    Restarts the backend services without bouncing the controller or agents.
    Admin (or host local token) only. Fails soft per service so a partial
    recovery still reports what was restarted. The units are restarted
    concurrently so worst-case latency is one unit's timeout, not their sum.
    """
    if not _is_admin_or_local_token(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)

    results = list(await asyncio.gather(*(_restart_ai_unit(u) for u in AI_STACK_UNITS)))
    restarted = [r["unit"] for r in results if r.get("ok")]
    failed = [r for r in results if not r.get("ok")]
    return {
        "status": "ok" if restarted and not failed else "partial" if restarted else "failed",
        "restarted": restarted,
        "failed": failed,
        "results": results,
    }


@router.post("/api/system/hardware/refresh")
async def hardware_refresh(request: Request):
    """Re-probe hardware and update the cached profile.

    Useful when the user has installed new drivers or hardware (e.g. vulkan-tools)
    and wants taOS to pick up the change without a full restart. The new logic in
    get_hardware_profile already re-probes on every startup; this endpoint provides
    a self-service path between restarts.
    """
    from tinyagentos.hardware import get_hardware_profile

    data_dir = getattr(request.app.state, "data_dir", None)
    if data_dir is None:
        return JSONResponse({"error": "data_dir not available"}, status_code=503)

    cache_path = data_dir / "hardware.json"
    if cache_path.exists():
        cache_path.unlink()

    profile = get_hardware_profile(cache_path)
    request.app.state.hardware_profile = profile

    data = asdict(profile)
    data["profile_id"] = profile.profile_id
    return data


@router.get("/api/system/restart/status")
async def restart_status(request: Request):
    """Return current orchestrator phase and per-agent status."""
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is None:
        return JSONResponse({"error": "orchestrator not available"}, status_code=503)
    return orchestrator.get_status()
