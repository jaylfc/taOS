"""Cluster capability-map endpoints.

Thin HTTP surface over CapabilityMap (tinyagentos/cluster/capability_map.py):
workers push a hardware/status heartbeat; admins read the live map and adjust
node status. The scheduler and placement logic read the same store directly.

Auth mirrors the existing cluster routes: heartbeats are gated by the worker
HMAC (a node may only write its own row), reads and admin mutations require an
admin session. No new auth machinery is introduced here.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from tinyagentos.cluster.worker_auth import _HMACError, require_worker_hmac
from tinyagentos.routes.auth import _require_admin

router = APIRouter()

# Heartbeats older than this are considered stale and pruned on demand.
DEFAULT_STALE_S = 900


class CapabilityHeartbeat(BaseModel):
    node_id: str
    hostname: str = ""
    cpu: dict = Field(default_factory=dict)
    ram_mb: int = 0
    gpu: dict = Field(default_factory=dict)
    npu: dict = Field(default_factory=dict)
    status: str = "online"


class StatusUpdate(BaseModel):
    status: str


class PruneRequest(BaseModel):
    older_than_s: int = DEFAULT_STALE_S


def _store(request: Request):
    return getattr(request.app.state, "capability_map", None)


@router.post("/api/cluster/capability/heartbeat")
async def capability_heartbeat(request: Request, body: CapabilityHeartbeat):
    """Worker HMAC — a node upserts its own capability row + liveness."""
    try:
        await require_worker_hmac(request)
    except _HMACError as exc:
        return exc.response
    # A node may only write its own row: the authenticated worker name must
    # match the heartbeat's node_id (same rule as worker registration).
    if getattr(request.state, "hmac_worker_name", None) != body.node_id:
        return JSONResponse(
            {"error": "Worker name in header does not match node_id"},
            status_code=403,
        )
    store = _store(request)
    if store is None:
        return JSONResponse({"error": "capability map unavailable"}, status_code=503)
    payload = body.model_dump()
    # "draining" is an admin decision to stop scheduling new work on a node that
    # is still alive and heartbeating. A routine heartbeat (status defaults to
    # "online") must not silently clear that intent; only an explicit admin
    # set-status call clears draining. Preserve it across heartbeats.
    current = await store.get(body.node_id)
    if current is not None and current["status"] == "draining":
        payload["status"] = "draining"
    try:
        node = await store.upsert(payload)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return node


@router.get("/api/cluster/capability")
async def capability_list(request: Request, status: str | None = None):
    """Admin — list capability rows, optionally filtered by status."""
    ok, err = _require_admin(request)
    if not ok:
        return err
    store = _store(request)
    if store is None:
        return JSONResponse({"error": "capability map unavailable"}, status_code=503)
    return {"nodes": await store.list(status)}


@router.post("/api/cluster/capability/{node_id}/status")
async def capability_set_status(request: Request, node_id: str, body: StatusUpdate):
    """Admin — set a node's status (online/offline/draining)."""
    ok, err = _require_admin(request)
    if not ok:
        return err
    store = _store(request)
    if store is None:
        return JSONResponse({"error": "capability map unavailable"}, status_code=503)
    try:
        node = await store.set_status(node_id, body.status)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if node is None:
        return JSONResponse({"error": "unknown node"}, status_code=404)
    return node


@router.post("/api/cluster/capability/prune")
async def capability_prune(request: Request, body: PruneRequest):
    """Admin — drop rows whose last heartbeat is older than older_than_s."""
    ok, err = _require_admin(request)
    if not ok:
        return err
    store = _store(request)
    if store is None:
        return JSONResponse({"error": "capability map unavailable"}, status_code=503)
    removed = await store.prune_stale(body.older_than_s)
    return {"pruned": removed}


@router.post("/api/cluster/capability/sweep")
async def capability_sweep(request: Request, body: PruneRequest):
    """Admin — flip online nodes with no recent heartbeat to offline (row kept)."""
    ok, err = _require_admin(request)
    if not ok:
        return err
    store = _store(request)
    if store is None:
        return JSONResponse({"error": "capability map unavailable"}, status_code=503)
    flipped = await store.mark_stale_offline(body.older_than_s)
    return {"offlined": flipped}
