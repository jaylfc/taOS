"""Secrets Broker routes (P1a).

API surface over :class:`tinyagentos.broker.BrokerService`. Backend only:
list providers, request access (which raises a desktop notification for the
operator), and the approve / deny / revoke grant lifecycle. The consent UI
(Secrets grant surface + Permissions app) is P1b. Auth is enforced by the
same session middleware that guards the secrets routes, so these routes carry
no per-route guard of their own.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.broker.providers import PROVIDERS

router = APIRouter()


class AccessRequest(BaseModel):
    provider_id: str
    scopes: Optional[list[str]] = None
    reason: Optional[str] = None
    agent_identity: Optional[str] = None


class ApproveRequest(BaseModel):
    window_seconds: float


@router.get("/api/broker/providers")
async def list_providers(request: Request):
    return [
        {
            "id": p.id,
            "display_name": p.display_name,
            "kind": p.kind,
            "default_scopes": list(p.default_scopes),
        }
        for p in PROVIDERS.values()
    ]


@router.post("/api/broker/request")
async def request_access(request: Request, body: AccessRequest):
    if not body.agent_identity:
        return JSONResponse(
            {"error": "agent_identity is required"}, status_code=400
        )
    broker = request.app.state.broker
    try:
        grant_id = await broker.request_access(
            agent_identity=body.agent_identity,
            provider_id=body.provider_id,
            scopes=body.scopes,
            reason=body.reason,
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    message = f"{body.agent_identity} requests {body.provider_id}"
    if body.reason:
        message += f" - {body.reason}"
    await request.app.state.notifications.add(
        title="Agent access request",
        message=message,
        level="warning",
        source="broker",
    )
    return {"grant_id": grant_id}


@router.get("/api/broker/grants")
async def list_grants(
    request: Request,
    agent_identity: str | None = None,
    status: str | None = None,
):
    broker = request.app.state.broker
    return await broker.store.list_grants(
        agent_identity=agent_identity, status=status
    )


@router.post("/api/broker/grants/{grant_id}/approve")
async def approve_grant(request: Request, grant_id: str, body: ApproveRequest):
    broker = request.app.state.broker
    try:
        grant = await broker.approve(grant_id, body.window_seconds)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return grant


@router.post("/api/broker/grants/{grant_id}/deny")
async def deny_grant(request: Request, grant_id: str):
    broker = request.app.state.broker
    ok = await broker.deny(grant_id)
    # ok is False only when no grant matched the id; return 404 for consistency
    # with approve (which 400s on an unknown id) instead of a 200 {"ok": false}.
    if not ok:
        return JSONResponse({"error": f"unknown grant_id: {grant_id!r}"}, status_code=404)
    return {"ok": True}


@router.post("/api/broker/grants/{grant_id}/revoke")
async def revoke_grant(request: Request, grant_id: str):
    broker = request.app.state.broker
    ok = await broker.revoke(grant_id)
    if not ok:
        return JSONResponse({"error": f"unknown grant_id: {grant_id!r}"}, status_code=404)
    return {"ok": True}
