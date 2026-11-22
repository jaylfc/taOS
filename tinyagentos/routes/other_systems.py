from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse


logger = logging.getLogger(__name__)

_ID_REGEX = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")

DEFAULT_HELPER = "/usr/local/sbin/taos-switch-os"


def _helper_path() -> str | None:
    helper = os.environ.get("TAOS_SWITCH_OS_HELPER", DEFAULT_HELPER)
    if not os.path.isabs(helper):
        logger.warning("TAOS_SWITCH_OS_HELPER is not an absolute path: %s", helper)
        return None
    return helper


def _validate_system_entry(entry: dict) -> bool:
    if not isinstance(entry, dict):
        return False
    id_val = entry.get("id")
    name = entry.get("name")
    ready = entry.get("ready")
    reason = entry.get("reason")
    if not isinstance(id_val, str) or not _ID_REGEX.match(id_val):
        return False
    if not isinstance(name, str):
        return False
    if not isinstance(ready, bool):
        return False
    if not isinstance(reason, str):
        return False
    return True


async def _run_list(helper: str) -> list[dict]:
    try:
        proc = await asyncio.create_subprocess_exec(
            helper,
            "list",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            logger.warning("taos-switch-os list timed out after 10s")
            return []
        if proc.returncode != 0:
            logger.warning("taos-switch-os list failed rc=%s: %s", proc.returncode, stderr.decode(errors="replace"))
            return []
        try:
            data = json.loads(stdout.decode())
        except json.JSONDecodeError:
            logger.warning("taos-switch-os list returned invalid JSON")
            return []
        if not isinstance(data, list):
            logger.warning("taos-switch-os list returned non-list JSON")
            return []
        valid: list[dict] = []
        for entry in data:
            if _validate_system_entry(entry):
                valid.append(entry)
            else:
                logger.warning("taos-switch-os list dropped invalid entry: %s", entry)
        return valid
    except FileNotFoundError:
        return []
    except Exception as exc:  # noqa: BLE001
        logger.exception("taos-switch-os list raised")
        return []


async def _get_lock_state(request: Request) -> bool:
    """Read the console lock state from /auth/lock-state. Returns True if locked."""
    # We need to call the internal endpoint. Since it's console-only with special headers,
    # we can't easily call it via HTTP from here. Instead, read the module globals directly.
    from tinyagentos.routes.auth import _CONSOLE_LOCKED
    return bool(_CONSOLE_LOCKED)


router = APIRouter()


@router.get("/api/system/other-systems")
async def get_other_systems(request: Request):
    """List other installed operating systems available for switching."""
    helper = _helper_path()
    if helper is None or not Path(helper).exists():
        return {"available": False, "systems": []}
    systems = await _run_list(helper)
    return {"available": True, "systems": systems}


async def _require_admin_no_local_token(request: Request) -> None:
    """Require admin session, but reject local token and agent token."""
    # Check for admin session (includes host local token mapping to primary user)
    if getattr(request.state, "is_admin", False):
        # But reject if it's explicitly a local token
        if getattr(request.state, "via", None) == "local_token":
            raise HTTPException(status_code=403, detail="forbidden")
        # Also reject agent tokens
        if getattr(request.state, "via", None) == "agent":
            raise HTTPException(status_code=403, detail="forbidden")
        return
    raise HTTPException(status_code=403, detail="forbidden")


@router.post("/api/system/other-systems/{sys_id}/switch")
async def switch_other_system(sys_id: str, request: Request):
    """Switch to another installed operating system."""
    # Validate id format
    if not _ID_REGEX.match(sys_id):
        raise HTTPException(status_code=400, detail="invalid system id")

    # Require admin (owner/admin), no local/agent tokens
    await _require_admin_no_local_token(request)

    # Check console is unlocked
    if await _get_lock_state(request):
        raise HTTPException(status_code=423, detail="console is locked")

    helper = _helper_path()
    if helper is None or not Path(helper).exists():
        raise HTTPException(status_code=503, detail="this phone is not set up to switch systems")

    # Get fresh list and validate the id is present and ready
    systems = await _run_list(helper)
    target = next((s for s in systems if s["id"] == sys_id), None)
    if target is None:
        raise HTTPException(status_code=409, detail="system not found")
    if not target["ready"]:
        raise HTTPException(status_code=409, detail=target["reason"])

    # Parse body
    try:
        body = await request.json()
    except Exception:
        body = {}
    confirm = body.get("confirm", False)
    force = body.get("force", False)
    if not confirm:
        raise HTTPException(status_code=400, detail="confirmation required")

    # Graceful agent drain (same as restart route) - use shared orchestrator
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is None:
        raise HTTPException(status_code=503, detail="orchestrator not available")
    drain_report = await orchestrator.prepare("all", "system-shutdown")
    drain_failed = any(v.get("status") in ("timeout", "error") for v in drain_report.values())

    if drain_failed and not force:
        raise HTTPException(status_code=409, detail="agents not ready; use force to override")

    # Audit/notification BEFORE calling helper (same path as restart)
    notif = getattr(request.app.state, "notifications", None)
    if notif:
        await notif.add(
            title=f"Switching to {target['name']}",
            message=f"System switch to {target['name']} initiated",
            level="info",
            source="system.lifecycle",
        )

    # Call the helper
    try:
        proc = await asyncio.create_subprocess_exec(
            "sudo",
            "-n",
            helper,
            "go",
            sys_id,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise HTTPException(status_code=502, detail="switch command timed out")
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="this phone is not set up to switch systems")
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("taos-switch-os go raised")
        raise HTTPException(status_code=502, detail="switch command failed")

    # Map exit codes
    if proc.returncode == 2:
        raise HTTPException(status_code=400, detail="bad switch request")
    if proc.returncode == 3:
        reason = stderr.decode(errors="replace").strip()
        raise HTTPException(status_code=409, detail=reason or target["reason"])
    if proc.returncode == 1:
        raise HTTPException(status_code=503, detail="this phone is not set up to switch systems")
    if proc.returncode != 0:
        raise HTTPException(status_code=502, detail="switch failed")

    # Success: return helper JSON
    try:
        result = json.loads(stdout.decode())
    except json.JSONDecodeError:
        result = {"ok": True, "id": sys_id, "in_seconds": 2}

    return result