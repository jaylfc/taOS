from __future__ import annotations

import asyncio
import logging
import os
import sys
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from tinyagentos.auth_context import require_admin
from tinyagentos.cluster import backend_services
from tinyagentos.restart_orchestrator import write_pending_restart, read_pending_restart

logger = logging.getLogger(__name__)

# Restart/shutdown triggers are gated per handler (admin or host local token,
# see tinyagentos.auth_context.require_admin) rather than at router level:
# prepare-shutdown must also accept the loopback stop hook, and the status read
# stays open.
router = APIRouter()


def _require_admin_or_loopback(request: Request) -> None:
    """Gate for the shutdown drain: the loopback systemd stop hook (no session,
    ``via == "loopback"`` stamped by AuthMiddleware) or an admin/local-token
    caller. A remote non-admin member session drains every agent otherwise.
    """
    if getattr(request.state, "via", None) == "loopback":
        return
    require_admin(request)


@router.post("/api/system/prepare-shutdown", dependencies=[Depends(_require_admin_or_loopback)])
async def prepare_shutdown(request: Request):
    """Gracefully prepare all agents for shutdown. Used by systemd stop hook."""
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is None:
        return JSONResponse({"error": "orchestrator not available"}, status_code=503)
    report = await orchestrator.prepare("all", "system-shutdown")
    return {"status": "ready", "report": report}


@router.post("/api/system/restart/prepare", dependencies=[Depends(require_admin)])
async def prepare_restart(request: Request):
    """Restart just the controller process. Admin or host local token only.

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


# Core AI backends taOS manages as systemd services that have NO store manifest
# (installed by install-server.sh, not via the store): qmd, the shared model
# provider. Store-installed managed backends (rkllama and any future NPU/GPU
# runtime) are discovered from their catalog manifests instead
# (lifecycle.auto_manage), so new managed backends join #1743 recovery
# automatically without editing this list. The controller itself is deliberately
# NOT here -- bouncing it is the separate /api/system/restart/prepare path.
# Value is the preferred systemctl scope for scope resolution.
CORE_MANAGED_UNITS: dict[str, str] = {"qmd.service": "system"}


def _catalog_root(request: Request) -> Path:
    """Locate the app-catalog root the same way the app wires the registry."""
    registry = getattr(request.app.state, "registry", None)
    catalog_dir = getattr(registry, "catalog_dir", None)
    if catalog_dir is not None:
        return Path(catalog_dir)
    return Path(__file__).resolve().parent.parent.parent / "app-catalog"


async def _managed_ai_units(request: Request) -> list[tuple[str, str | None]]:
    """Return (unit, prefer_scope) for every managed AI backend installed here.

    The target set for #1743 recovery is the core-managed units (no store
    manifest) unioned with the store-declared managed backends
    (lifecycle.auto_manage in their catalog manifest), then narrowed to units
    whose systemd file actually exists on THIS node. A backend that has migrated
    to another cluster node is skipped rather than reported as a spurious
    failure, and a newly installed managed backend is picked up with no code
    change. Membership is resolved with ``systemctl cat`` (via
    ``backend_services.unit_state``) so a dead/crashed unit -- exactly what
    recovery targets -- still counts as installed.
    """
    prefer: dict[str, str | None] = dict(CORE_MANAGED_UNITS)
    try:
        for mb in backend_services.load_managed_backends(_catalog_root(request)):
            prefer.setdefault(mb.unit, mb.scope)
    except Exception:  # pragma: no cover - defensive
        logger.exception("failed to load managed backends from catalog")

    async def _installed(unit: str, scope: str | None):
        state = await backend_services.unit_state(unit, scope)
        return (unit, scope) if state.get("installed") else None

    checks = await asyncio.gather(*(_installed(u, s) for u, s in prefer.items()))
    return [c for c in checks if c is not None]


@router.post("/api/system/ai-stack/restart", dependencies=[Depends(require_admin)])
async def restart_ai_stack(request: Request):
    """Restart the local AI inference stack -- issue #1743.

    A recovery action for edge devices where a model stalls (endless NPU
    generation, unresponsive inference) while the controller itself stays up.
    Restarts the managed backend services without bouncing the controller or
    agents. The target set is derived from the managed-backend contract (core
    units plus store manifests) and narrowed to backends actually installed on
    this node, so it stays correct as backends are added or migrated across
    cluster nodes. Admin (or host local token) only. Fails soft per service so a
    partial recovery still reports what was restarted. Units are restarted
    concurrently so worst-case latency is one unit's timeout, not their sum.
    """
    targets = await _managed_ai_units(request)
    if not targets:
        return {
            "status": "noop",
            "restarted": [],
            "failed": [],
            "results": [],
            "detail": "no managed AI backends installed on this node",
        }

    results = list(await asyncio.gather(
        *(backend_services.service_action(unit, "restart", prefer)
          for unit, prefer in targets)
    ))
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


# --- Session mode (phone/kiosk devices only) --------------------------------
#
# On a handset running taOS as its shell (postmarketOS + Plasma Mobile), the
# device can be in one of two graphical sessions:
#   * "kiosk"  — taos-kiosk.service: a Wayland compositor running taOS
#                full-screen, so the phone looks like it boots into taOS;
#   * "plasma" — plasma-mobile.service: the stock KDE phone shell.
# The two units Conflict with each other, so switching is ONE `systemctl start`
# and systemd stops the other side for us.
#
# Privilege comes from a polkit rule scoped to exactly these two units (see
# packaging/postmarketos/50-taos-session.rules), NOT from sudo — the controller
# runs as the unprivileged `taos` user and must not be able to manage arbitrary
# system services just to offer a UI toggle.
#
# AVAILABILITY IS THE DEVICE GATE. `taos-kiosk.service` only exists on a device
# provisioned as a taOS handset, so `available: false` everywhere else and the
# frontend hides the app rather than shipping a button that cannot work. This
# is deliberately a capability probe, not a hardcoded device list.

_SESSION_UNITS = {"kiosk": "taos-kiosk.service", "plasma": "plasma-mobile.service"}


async def _systemctl(*args: str) -> tuple[int, str]:
    """Run systemctl and return (rc, combined output). Never raises."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemctl", *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
        return proc.returncode or 0, (out or b"").decode(errors="replace").strip()
    except FileNotFoundError:
        return 127, "systemctl not found"
    except asyncio.TimeoutError:
        return 124, "systemctl timed out"
    except Exception as exc:  # noqa: BLE001
        return 1, str(exc)


async def _session_mode_state() -> dict:
    kiosk_rc, kiosk_state = await _systemctl("is-active", _SESSION_UNITS["kiosk"])
    # `is-active` exits non-zero for inactive units, so the OUTPUT is the signal
    # and rc is not. An unknown unit reports "inactive" too, so availability is
    # probed separately with `cat`, which fails only when the unit is absent.
    cat_rc, _ = await _systemctl("cat", _SESSION_UNITS["kiosk"])
    available = cat_rc == 0
    _, plasma_state = await _systemctl("is-active", _SESSION_UNITS["plasma"])
    if kiosk_state == "active":
        current = "kiosk"
    elif plasma_state == "active":
        current = "plasma"
    else:
        current = "unknown"
    return {
        "available": available,
        "current": current,
        "kiosk": kiosk_state,
        "plasma": plasma_state,
    }


@router.get("/api/system/session-mode")
async def get_session_mode():
    """Report which graphical session is running, and whether this device can
    switch at all. Open to any authenticated caller: it is a read of local
    service state that the UI needs before deciding to render the control."""
    return await _session_mode_state()


@router.post("/api/system/session-mode", dependencies=[Depends(require_admin)])
async def set_session_mode(request: Request):
    """Switch the device between the taOS kiosk and Plasma Mobile.

    Admin only — this replaces what is on the user's screen.
    """
    body = await request.json() if await request.body() else {}
    mode = (body or {}).get("mode", "")
    if mode not in _SESSION_UNITS:
        return JSONResponse(
            {"error": f"mode must be one of {sorted(_SESSION_UNITS)}"}, status_code=400,
        )
    state = await _session_mode_state()
    if not state["available"]:
        return JSONResponse(
            {"error": "this device has no taOS kiosk session installed"}, status_code=404,
        )
    # Starting the target stops the other: the units Conflict. Doing it as two
    # jobs (stop-then-start) would leave a window with NO session on screen.
    rc, out = await _systemctl("start", _SESSION_UNITS[mode])
    if rc != 0:
        logger.warning("session-mode switch to %s failed rc=%s: %s", mode, rc, out)
        return JSONResponse(
            {"error": "switch failed", "detail": out or f"rc={rc}"}, status_code=500,
        )
    return {"ok": True, "mode": mode}
