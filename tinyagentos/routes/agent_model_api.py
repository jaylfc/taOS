"""Agent-as-a-Model OpenAI-compatible surface (decision 19).

An external OpenAI-compatible client points at https://<taos>/v1 with a consent
key (the bearer token IS the consent grant, minted only through the user
consent flow) and gets THEIR agent(s) exposed as models. The key maps to
{issuing_user, agent_ids, scopes, expiry, rate_cap} via AgentModelKeyStore.

This slice is the AUTH-GATED surface: GET /v1/models lists the agents the
caller's key is consented for, each as an OpenAI model entry. POST
/v1/chat/completions (running a turn through the agent harness), scope
enforcement, and rate limiting are later slices built on this binding. Per the
spec, the endpoint must never resolve a model without a valid consent key, so
the auth binding lands first.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


def _unauthorized() -> JSONResponse:
    # OpenAI-shaped error so standard clients surface it correctly.
    return JSONResponse(
        {"error": {
            "message": "invalid or missing consent key",
            "type": "invalid_request_error",
            "code": "invalid_api_key",
        }},
        status_code=401,
    )


async def resolve_consent_key(request: Request) -> dict | None:
    """Resolve the Authorization: Bearer consent key to its binding, or None.

    None covers a missing/malformed header, an unknown store, an empty token,
    and a revoked/expired/unknown key (the store rejects those), so callers
    cannot distinguish failure modes.
    """
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    token = auth[7:].strip()
    store = getattr(request.app.state, "agent_model_keys", None)
    if store is None or not token:
        return None
    return await store.resolve(token)


@router.get("/v1/models")
async def list_models(request: Request):
    """OpenAI /v1/models: the agents this consent key may address, as models."""
    binding = await resolve_consent_key(request)
    if binding is None:
        return _unauthorized()
    try:
        created = int(datetime.fromisoformat(binding["created_at"]).timestamp())
    except (KeyError, ValueError, TypeError):
        created = 0
    data = [
        {"id": agent_id, "object": "model", "created": created, "owned_by": "taos-agent"}
        for agent_id in binding.get("agent_ids", [])
    ]
    return {"object": "list", "data": data}
