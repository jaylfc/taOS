"""Agent-as-a-Model OpenAI-compatible surface (decision 19).

An external OpenAI-compatible client points at https://<taos>/v1 with a consent
key (the bearer token IS the consent grant, minted only through the user
consent flow) and gets THEIR agent(s) exposed as models. The key maps to
{issuing_user, agent_ids, scopes, expiry, rate_cap} via AgentModelKeyStore.

GET /v1/models lists the agents the caller's key is consented for, each as an
OpenAI model entry. POST /v1/chat/completions enforces the same consent contract
(valid key, requested model in the key's agent_ids) and runs ONE non-streaming
turn through the agent's host opencode server, translating the result into an
OpenAI ChatCompletion response.  The agent keeps its memory, tools and identity
because the turn runs through the same opencode harness the local taOS agent
uses, not through a lightweight direct LiteLLM call.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from tinyagentos.adapters.opencode_adapter import OpenCodeAdapter, OpenCodeConfig
from tinyagentos.opencode_runtime import OpenCodeBinaryNotFoundError
from tinyagentos.taos_agent_runtime import ensure_agent_opencode_server

router = APIRouter()
logger = logging.getLogger(__name__)


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


def _resolve_agent(config, agent_ref: str) -> dict | None:
    """Return the agent dict for *agent_ref* (matched by name first, then id) or None."""
    for agent in config.agents:
        if agent.get("name") == agent_ref:
            return agent
    for agent in config.agents:
        if agent.get("id") == agent_ref:
            return agent
    return None


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
    model must be one of the agents that key is consented for.  The turn runs
    through the agent's host opencode server (reusing the same harness the
    local taOS agent uses) so the agent keeps its memory, tools and identity.

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

    agent = _resolve_agent(request.app.state.config, model)
    if agent is None:
        return _openai_error(
            f"the model '{model}' does not exist or you do not have access to it",
            type="invalid_request_error",
            code="model_not_found",
            status=404,
        )

    agent_model = agent.get("model") or model
    llm_key = agent.get("llm_key")

    try:
        server = await ensure_agent_opencode_server(
            request.app.state, model, agent_model, llm_key,
        )
    except OpenCodeBinaryNotFoundError as exc:
        logger.error("agent_model_api: opencode binary not found: %s", exc)
        return _openai_error(
            "opencode is not installed. Install it with: "
            "curl -fsSL https://opencode.ai/install | bash -- then restart taOS.",
            type="server_error",
            code="service_unavailable",
            status=503,
        )
    except Exception as exc:
        logger.exception("agent_model_api: opencode server failed to start")
        return _openai_error(
            f"agent runtime failed to start: {exc}",
            type="server_error",
            code="service_unavailable",
            status=503,
        )

    app_state = request.app.state
    password = getattr(app_state, "opencode_server_passwords", {}).get(model, "")

    cfg = OpenCodeConfig(
        base_url=server.base_url,
        server_password=password,
        model_provider_id="litellm",
        model_id=agent_model,
    )

    result: dict = {"content": "", "error": None}

    def sink(reply: dict) -> None:
        kind = reply.get("kind")
        if kind == "delta":
            result["content"] += reply.get("content", "")
        elif kind == "final":
            result["content"] = reply.get("content", result["content"])
        elif kind == "error":
            result["error"] = reply.get("error")

    adapter = OpenCodeAdapter(cfg, sink)
    session_ids = getattr(app_state, "opencode_server_session_ids", {})
    adapter.session_id = session_ids.get(model)

    async def _drive() -> None:
        try:
            await adapter.ensure_session()
            session_ids[model] = adapter.session_id
            trace_id = uuid.uuid4().hex
            await adapter.prompt(messages[-1].get("content", ""), trace_id=trace_id)
            await adapter.close()
        except Exception as exc:
            logger.exception("agent_model_api: drive task error")
            result["error"] = str(exc)

    drive_task = asyncio.create_task(_drive())

    try:
        await asyncio.wait_for(drive_task, timeout=300.0)
    except asyncio.TimeoutError:
        if not drive_task.done():
            drive_task.cancel()
            try:
                await drive_task
            except (asyncio.CancelledError, Exception):
                pass
        result["error"] = "agent turn timed out (limit: 300s)"

    if result["error"]:
        return _openai_error(
            result["error"],
            type="server_error",
            code="service_unavailable",
            status=500,
        )

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": result["content"],
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }
