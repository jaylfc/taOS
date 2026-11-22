"""Secrets Broker routes (P1a).

API surface over :class:`tinyagentos.broker.BrokerService`. Backend only:
list providers, request access (which raises a desktop notification for the
operator), and the approve / deny / revoke grant lifecycle. The consent UI
(Secrets grant surface + Permissions app) is P1b. Auth is enforced by the
same session middleware that guards the secrets routes, so these routes carry
no per-route guard of their own.
"""
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.broker.clients.keenable import (
    keenable_fetch,
    keenable_search,
    public_fallback_enabled,
)
from tinyagentos.auth_context import (
    agent_bound_name,
    can_access_agent,
    is_human_admin,
    require_agent_access,
)
from tinyagentos.broker.providers import PROVIDERS

router = APIRouter()


class AccessRequest(BaseModel):
    provider_id: str
    scopes: list[str] | None = None
    reason: str | None = None
    agent_identity: str | None = None


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


class ApproveRequest(BaseModel):
    window_seconds: float


def _require_consent_authority(request: Request) -> None:
    """Approve / deny: the install owner (an admin session or the host token).

    Every provider's real key lives in the install-wide secrets keystore, so
    the person whose secret it is, is the install owner. A member who owns an
    agent may ask for access for it but cannot grant it, and an agent's own
    token (which maps to the admin user) can never approve a grant for itself.
    """
    if not is_human_admin(request):
        raise HTTPException(status_code=403, detail="forbidden")


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
    await require_agent_access(request, body.agent_identity)
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
    if agent_identity:
        await require_agent_access(request, agent_identity)
    broker = request.app.state.broker
    grants = await broker.store.list_grants(
        agent_identity=agent_identity, status=status
    )
    if agent_identity or is_human_admin(request):
        return grants
    allowed: dict[str, bool] = {}
    visible = []
    for g in grants:
        who = g["agent_identity"]
        if who not in allowed:
            allowed[who] = await can_access_agent(request, who)
        if allowed[who]:
            visible.append(g)
    return visible


@router.post("/api/broker/grants/{grant_id}/approve")
async def approve_grant(request: Request, grant_id: str, body: ApproveRequest):
    _require_consent_authority(request)
    broker = request.app.state.broker
    try:
        grant = await broker.approve(grant_id, body.window_seconds)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return grant


@router.post("/api/broker/grants/{grant_id}/deny")
async def deny_grant(request: Request, grant_id: str):
    _require_consent_authority(request)
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
    # Taking access away is allowed to the install owner and to the owner of
    # the agent that holds the grant; an agent token never revokes.
    if not is_human_admin(request):
        grant = await broker.store.get_grant(grant_id)
        if grant is None or agent_bound_name(request):
            raise HTTPException(status_code=403, detail="forbidden")
        await require_agent_access(request, grant["agent_identity"])
    ok = await broker.revoke(grant_id)
    if not ok:
        return JSONResponse({"error": f"unknown grant_id: {grant_id!r}"}, status_code=404)
    return {"ok": True}


def _validate_search_body(body: dict) -> tuple[dict | None, JSONResponse | None]:
    query = body.get("query")
    if not isinstance(query, str):
        return None, JSONResponse({"error": "query must be a string"}, status_code=400)
    stripped = query.strip()
    if not stripped:
        return None, JSONResponse({"error": "query must not be empty"}, status_code=400)
    if len(stripped) > 500:
        return None, JSONResponse({"error": "query must be at most 500 characters"}, status_code=400)

    num_results = body.get("num_results", 5)
    if isinstance(num_results, bool) or not isinstance(num_results, int):
        return None, JSONResponse({"error": "num_results must be an integer"}, status_code=400)
    if not (1 <= num_results <= 10):
        return None, JSONResponse({"error": "num_results must be between 1 and 10"}, status_code=400)

    return {"query": stripped, "num_results": num_results}, None


def _validate_fetch_body(body: dict) -> tuple[dict | None, JSONResponse | None]:
    url = body.get("url")
    if not isinstance(url, str):
        return None, JSONResponse({"error": "url must be a string"}, status_code=400)
    if not url.startswith(("http://", "https://")):
        return None, JSONResponse({"error": "url must start with http:// or https://"}, status_code=400)

    max_chars = body.get("max_chars")
    if max_chars is not None:
        if isinstance(max_chars, bool) or not isinstance(max_chars, int):
            return None, JSONResponse({"error": "max_chars must be an integer"}, status_code=400)
        if not (1 <= max_chars <= 100000):
            return None, JSONResponse({"error": "max_chars must be between 1 and 100000"}, status_code=400)

    return {"url": url, "max_chars": max_chars}, None


@router.post("/api/broker/keenable/search")
async def keenable_search_route(request: Request):
    # First authenticate/authorize before validating the body
    # This ensures unauthenticated callers get 403, not 400 that reveals schema
    is_local, agent_name = _local_token_caller(request)
    if not is_local or agent_name is None:
        return JSONResponse({"error": "agent token required"}, status_code=403)

    grant = await request.app.state.broker.store.find_active_grant(
        agent_name, "keenable", time.time()
    )
    if grant is None:
        return JSONResponse({"error": "no active keenable grant"}, status_code=403)
    if "search" not in grant["scopes"]:
        return JSONResponse(
            {"error": "scope not granted: search"}, status_code=403
        )

    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    if not isinstance(body, dict):
        return JSONResponse({"error": "body must be a JSON object"}, status_code=400)

    validated, error = _validate_search_body(body)
    if error:
        return error

    secret = await request.app.state.secrets.get("KEENABLE_API_KEY")
    api_key = secret["value"] if secret else ""
    allow_public = await public_fallback_enabled(request.app.state.desktop_settings)

    if not api_key and not allow_public:
        return JSONResponse({"error": "keenable not configured"}, status_code=409)

    try:
        results = await keenable_search(
            api_key, validated["query"], validated["num_results"], allow_public=allow_public
        )
        return {"results": results}
    except RuntimeError:
        return JSONResponse({"error": "keenable upstream error"}, status_code=502)


@router.post("/api/broker/keenable/fetch")
async def keenable_fetch_route(request: Request):
    # First authenticate/authorize before validating the body
    is_local, agent_name = _local_token_caller(request)
    if not is_local or agent_name is None:
        return JSONResponse({"error": "agent token required"}, status_code=403)

    grant = await request.app.state.broker.store.find_active_grant(
        agent_name, "keenable", time.time()
    )
    if grant is None:
        return JSONResponse({"error": "no active keenable grant"}, status_code=403)
    if "fetch" not in grant["scopes"]:
        return JSONResponse(
            {"error": "scope not granted: fetch"}, status_code=403
        )

    # Now get and validate the body
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    if not isinstance(body, dict):
        return JSONResponse({"error": "body must be a JSON object"}, status_code=400)

    validated, error = _validate_fetch_body(body)
    if error:
        return error

    secret = await request.app.state.secrets.get("KEENABLE_API_KEY")
    api_key = secret["value"] if secret else ""
    allow_public = await public_fallback_enabled(request.app.state.desktop_settings)

    if not api_key and not allow_public:
        return JSONResponse({"error": "keenable not configured"}, status_code=409)

    try:
        results = await keenable_fetch(
            api_key, validated["url"], validated["max_chars"], allow_public=allow_public
        )
        return results
    except RuntimeError:
        return JSONResponse({"error": "keenable upstream error"}, status_code=502)
