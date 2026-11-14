"""Secrets Broker routes (P1a).

API surface over :class:`tinyagentos.broker.BrokerService`. Backend only:
list providers, request access (which raises a desktop notification for the
operator), and the approve / deny / revoke grant lifecycle. The consent UI
(Secrets grant surface + Permissions app) is P1b. Auth is enforced by the
same session middleware that guards the secrets routes, so these routes carry
no per-route guard of their own.
"""
from __future__ import annotations

import time
from typing import Optional, Union

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from tinyagentos.broker.clients.keenable import (
    keenable_fetch,
    keenable_search,
    public_fallback_enabled,
)
from tinyagentos.broker.providers import PROVIDERS

router = APIRouter()


class AccessRequest(BaseModel):
    provider_id: str
    scopes: Optional[list[str]] = None
    reason: Optional[str] = None
    agent_identity: Optional[str] = None


def _local_token_caller(request: Request) -> tuple[bool, str | None]:
    """Check if the request is from a bound local token.

    Returns (True, agent_name) when request.state.via == "local_token" and
    request.state.agent_name is set (bound agent token). Returns (True, None)
    when request.state.via == "local_token" but no agent_name is bound
    (host/unbound token). Returns (False, None) otherwise.
    """
    if getattr(request.state, "via", None) == "local_token":
        return True, getattr(request.state, "agent_name", None)
    return False, None


class KeenableSearchRequest(BaseModel):
    query: str
    num_results: int = 5

    @field_validator("query")
    @classmethod
    def _validate_query(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("query must not be empty after stripping")
        if len(stripped) > 500:
            raise ValueError("query must be at most 500 characters after stripping")
        return stripped

    @field_validator("num_results")
    @classmethod
    def _validate_num_results(cls, v: int) -> int:
        if not (1 <= v <= 10):
            raise ValueError("num_results must be between 1 and 10")
        return v


class KeenableFetchRequest(BaseModel):
    url: str
    max_chars: Optional[int] = None

    @field_validator("url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("url must start with http:// or https://")
        return v

    @field_validator("max_chars")
    @classmethod
    def _validate_max_chars(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and not (1 <= v <= 100000):
            raise ValueError("max_chars must be between 1 and 100000")
        return v


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


@router.post("/api/broker/keenable/search")
async def keenable_search_route(request: Request, body: KeenableSearchRequest):
    return await _keenable_search(request, body)


async def _keenable_search(request: Request, body: KeenableSearchRequest) -> dict:
    return await _keenable_call(request, "search", body=body)


@router.post("/api/broker/keenable/fetch")
async def keenable_fetch_route(request: Request, body: KeenableFetchRequest):
    return await _keenable_fetch(request, body)


async def _keenable_fetch(request: Request, body: KeenableFetchRequest) -> dict:
    return await _keenable_call(request, "fetch", body=body)


async def _keenable_call(
    request: Request, action: str, body: Union[KeenableSearchRequest, KeenableFetchRequest]
) -> dict:
    is_local, agent_name = _local_token_caller(request)
    if not is_local or agent_name is None:
        return JSONResponse({"error": "agent token required"}, status_code=403)

    grant = await request.app.state.broker.store.find_active_grant(
        agent_name, "keenable", time.time()
    )
    if grant is None:
        return JSONResponse({"error": "no active keenable grant"}, status_code=403)
    if action not in grant["scopes"]:
        return JSONResponse(
            {"error": f"scope not granted: {action}"}, status_code=403
        )

    secret = await request.app.state.secrets.get("KEENABLE_API_KEY")
    api_key = secret["value"] if secret else ""
    allow_public = await public_fallback_enabled(request.app.state.desktop_settings)

    if not api_key and not allow_public:
        return JSONResponse({"error": "keenable not configured"}, status_code=409)

    try:
        if action == "search":
            results = await keenable_search(
                api_key, body.query, body.num_results, allow_public=allow_public
            )
            return {"results": results}
        results = await keenable_fetch(
            api_key, body.url, body.max_chars, allow_public=allow_public
        )
        return results
    except RuntimeError:
        return JSONResponse({"error": "keenable upstream error"}, status_code=502)
