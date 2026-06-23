"""Agent-as-a-Model OpenAI-compatible surface (decision 19).

An external OpenAI-compatible client points at https://<taos>/v1 with a consent
key (the bearer token IS the consent grant, minted only through the user
consent flow) and gets THEIR agent(s) exposed as models. The key maps to
{issuing_user, agent_ids, scopes, expiry, rate_cap} via AgentModelKeyStore.

GET /v1/models lists the agents the caller's key is consented for, each as an
OpenAI model entry. POST /v1/chat/completions enforces the same consent contract
(valid key, requested model in the key's agent_ids) but does NOT yet run the
agent turn: that step drives the agent's harness and is the next slice, pending
the turn-seam choice (see ~/.taos-team/pending-decisions.md), so a valid request
returns 501. Scope (per-capability) enforcement and rate limiting also build on
this binding. Per the spec, the endpoint must never resolve a model without a
valid consent key, so the auth + scope contract lands first.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


def _openai_error(message: str, *, type: str, code: str, status: int) -> JSONResponse:
    """OpenAI-shaped error envelope so standard clients surface it correctly."""
    return JSONResponse(
        {"error": {"message": message, "type": type, "code": code}},
        status_code=status,
    )


def _unauthorized() -> JSONResponse:
    return _openai_error(
        "invalid or missing consent key",
        type="invalid_request_error",
        code="invalid_api_key",
        status=401,
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


@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """OpenAI /v1/chat/completions for an agent-as-a-model.

    Enforces the consent contract: a valid key is required, and the requested
    model must be one of the agents that key is consented for. Running the turn
    through the agent's harness is the next slice (pending the seam choice), so a
    contract-valid request returns 501 rather than a fabricated completion.

    The body is parsed manually AFTER the auth check (not via a Pydantic
    parameter) for two reasons: auth must take precedence so an unauthenticated
    caller always gets 401 rather than schema feedback, and every error stays in
    the OpenAI envelope rather than FastAPI's default 422 {"detail": ...}.
    """
    binding = await resolve_consent_key(request)
    if binding is None:
        return _unauthorized()

    def _bad_request(message: str) -> JSONResponse:
        return _openai_error(
            message,
            type="invalid_request_error",
            code="invalid_request_error",
            status=400,
        )

    try:
        body = await request.json()
    except Exception:
        return _bad_request("request body must be valid JSON")
    if not isinstance(body, dict):
        return _bad_request("request body must be a JSON object")

    model = body.get("model")
    if not isinstance(model, str) or not model:
        return _bad_request("you must provide a 'model' parameter")
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return _bad_request("'messages' must contain at least one message")

    if model not in binding.get("agent_ids", []):
        # OpenAI returns 404 model_not_found for a model the key cannot address;
        # this doubles as scope enforcement (the key is only consented for its
        # agent_ids), without leaking whether the agent exists for another user.
        return _openai_error(
            f"the model '{model}' does not exist or you do not have access to it",
            type="invalid_request_error",
            code="model_not_found",
            status=404,
        )
    # Contract satisfied; the turn execution is the next slice.
    return _openai_error(
        "agent turn execution is not yet implemented for this surface",
        type="server_error",
        code="not_implemented",
        status=501,
    )
