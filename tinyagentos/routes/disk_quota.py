"""HTTP surface for the disk quota monitor."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()


def _quota_state(agent: dict) -> dict:
    """Derive the disk quota state dict for a single agent config entry."""
    from tinyagentos.disk_quota import DiskQuotaMonitor

    quota_gib = float(agent.get("disk_quota_gib") or DiskQuotaMonitor.DEFAULT_QUOTA_GIB)
    used_gib = float(agent.get("disk_usage_gib") or 0.0)
    percent = used_gib / quota_gib if quota_gib > 0 else 0.0

    if percent >= DiskQuotaMonitor.HARD_THRESHOLD:
        state = "hard"
    elif percent >= DiskQuotaMonitor.WARN_THRESHOLD:
        state = "warn"
    else:
        state = "ok"

    return {
        "used_gib": round(used_gib, 3),
        "quota_gib": quota_gib,
        "percent": round(percent, 4),
        "state": state,
        "last_checked_at": agent.get("disk_last_checked_at"),
    }


class QuotaResizeRequest(BaseModel):
    size_gib: int


@router.get("/api/agents/{name}/disk")
async def get_agent_disk(name: str, request: Request):
    """Return the last-sampled disk usage for an agent."""
    config = request.app.state.config
    agent = next((a for a in config.agents if a.get("name") == name), None)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"agent not found: {name}")
    return _quota_state(agent)


@router.post("/api/agents/{name}/quota")
async def resize_agent_quota(name: str, body: QuotaResizeRequest, request: Request):
    """Resize the disk quota for an agent.

    Returns the new quota state on success.
    Returns 409 if the storage pool does not support live resize (dir backend).
    """
    from tinyagentos.disk_quota import DiskQuotaMonitor

    config = request.app.state.config
    notifications = request.app.state.notifications

    monitor = _get_or_build_monitor(request, config, notifications)

    try:
        result = await monitor.resize_quota(name, body.size_gib)
    except ValueError as exc:
        msg = str(exc)
        if msg.startswith("dir-backend:"):
            raise HTTPException(status_code=409, detail=msg[len("dir-backend:"):].strip())
        if msg.startswith("agent not found:"):
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    agent = next((a for a in config.agents if a.get("name") == name), None)
    quota_state = _quota_state(agent) if agent else {}
    return {**result, "disk": quota_state}


@router.post("/api/disk-quota/scan")
async def trigger_scan(request: Request):
    """Admin endpoint: run a full disk quota scan immediately.

    Useful as the 'refresh' action for the UI or the systemd timer trigger.
    """
    config = request.app.state.config
    notifications = request.app.state.notifications

    monitor = _get_or_build_monitor(request, config, notifications)
    results = await monitor.scan_all()

    from tinyagentos.config import save_config_locked
    if config.config_path and config.config_path.exists():
        await save_config_locked(config, config.config_path)

    return {"scanned": len(results), "results": results}


def _get_or_build_monitor(request: Request, config, notifications):
    """Return the app-scoped DiskQuotaMonitor, building it if absent."""
    from tinyagentos.disk_quota import DiskQuotaMonitor

    monitor = getattr(request.app.state, "disk_quota_monitor", None)
    if monitor is None:
        try:
            from tinyagentos.containers.backend import get_backend
            backend = get_backend()
        except Exception:
            from tinyagentos.containers.lxc import LXCBackend
            backend = LXCBackend()
        monitor = DiskQuotaMonitor(config, backend, notifications)
        request.app.state.disk_quota_monitor = monitor
    return monitor
