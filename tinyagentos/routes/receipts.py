"""Action receipts API (#155): the per-action audit ledger at the OS boundary.

Append-only. Receipts link the records other stores already own (trace_id,
board_audit_event_id, decision_id) plus the per-action fields no single store
holds. Reads are scoped: an admin sees every receipt, a non-admin sees only the
receipts stamped with their own user id, so the ledger never leaks across users.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.auth_context import CurrentUser, current_user

router = APIRouter()


def _store(request: Request):
    return getattr(request.app.state, "receipt_store", None)


class ReceiptIn(BaseModel):
    # `handle` is deliberately NOT accepted from the caller: it is a display
    # convenience that must derive from the agent registry, never be writer-set,
    # so it cannot be spoofed against the authoritative canonical id. The capture
    # hooks (slice 2) fill it server-side from the registry.
    agent_canonical_id: str
    project_id: str = ""
    workspace_hash: str = ""
    capability: str = ""
    capability_granted_at: str | None = None
    tool_name: str = ""
    tool_args: dict | None = None
    input_refs: list | None = None
    output_ref: str = ""
    result_summary: str = ""
    files_changed: list | None = None
    stop_reason: str = ""
    redactions: list | None = None
    human_approval: dict | None = None
    trace_id: str = ""
    board_audit_event_id: str = ""
    decision_id: str = ""
    metadata: dict | None = None


@router.post("/api/receipts")
async def create_receipt(body: ReceiptIn, request: Request, user: CurrentUser = Depends(current_user)):
    """Append an action receipt. Any authenticated caller (an agent at the OS
    boundary, or a tool) may write one; the receipt is stamped with the caller's
    user id so reads can be scoped."""
    store = _store(request)
    if store is None:
        return JSONResponse({"error": "receipt store unavailable"}, status_code=503)
    if not body.agent_canonical_id.strip():
        return JSONResponse({"error": "agent_canonical_id is required"}, status_code=400)
    fields = body.model_dump()
    agent = fields.pop("agent_canonical_id")
    rid = await store.record(agent, created_by_user_id=user.user_id, **fields)
    return {"id": rid, "agent_canonical_id": agent}


@router.get("/api/receipts/{receipt_id}")
async def get_receipt(receipt_id: str, request: Request, user: CurrentUser = Depends(current_user)):
    store = _store(request)
    if store is None:
        return JSONResponse({"error": "receipt store unavailable"}, status_code=503)
    rec = await store.get(receipt_id)
    if rec is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    # Non-admins may only read their own receipts; do not leak existence either.
    if not user.is_admin and rec.get("created_by_user_id") != user.user_id:
        return JSONResponse({"error": "not found"}, status_code=404)
    return rec


@router.get("/api/receipts")
async def list_receipts(
    request: Request,
    user: CurrentUser = Depends(current_user),
    agent: str | None = None,
    project: str | None = None,
    trace: str | None = None,
    limit: int = 200,
):
    """List receipts newest-first, optionally filtered by agent/project/trace.
    Admins see all; everyone else is scoped to their own receipts."""
    store = _store(request)
    if store is None:
        return JSONResponse({"error": "receipt store unavailable"}, status_code=503)
    receipts = await store.list(
        agent_canonical_id=agent,
        project_id=project,
        trace_id=trace,
        created_by_user_id=None if user.is_admin else user.user_id,
        limit=limit,
    )
    return {"count": len(receipts), "receipts": receipts}
