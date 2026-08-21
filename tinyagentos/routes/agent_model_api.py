"""Agent-as-a-Model OpenAI-compatible surface (decision 19).

An external OpenAI-compatible client points at https://<taos>/v1 with a consent
key (the bearer token IS the consent grant, minted only through the user
consent flow) and gets THEIR agent(s) exposed as models. The key maps to
{issuing_user, agent_ids, scopes, expiry, rate_cap} via AgentModelKeyStore.

GET /v1/models lists the agents the caller's key is consented for, each as an
OpenAI model entry. POST /v1/chat/completions enforces the same consent contract
(valid key, requested model in the key's agent_ids) and drives one non-streaming
agent turn through the opencode host-server seam, returning an OpenAI
ChatCompletion envelope. Scope (per-capability) enforcement and rate limiting
build on this binding. Per the spec, the endpoint must never resolve a model
without a valid consent key, so the auth + scope contract lands first.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

# Runtime calls referenced by module-level name so tests can monkeypatch them
# (no opencode binary required in CI; the live path uses the real servers).
try:
    from tinyagentos.opencode_runtime import drive_turn
except Exception:  # pragma: no cover - import shape guard
    drive_turn = None
try:
    from tinyagentos.taos_agent_runtime import ensure_taos_opencode_server
except Exception:  # pragma: no cover - import shape guard
    ensure_taos_opencode_server = None

logger = logging.getLogger(__name__)

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
    model must be one of the agents that key is consented for. The turn is then
    driven through the agent's opencode host-server seam and returned as an
    OpenAI ChatCompletion envelope.

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

    # --- Turn execution slice (tsk-gkh4mi) ---------------------------------
    # Consent + scope contract satisfied. Drive ONE non-streaming turn through
    # the agent's opencode host-server (seam: consent key -> agent -> that
    # agent's opencode server + LiteLLM virtual key -> one turn -> OpenAI shape).
    # The requested `model` is the agent_id; the host runs the taOS agent's
    # opencode server, so we resolve it the same way the chat endpoint does.
    # `stream` must be an explicit JSON boolean: a string like "false" must
    # not coerce to True (bool("false") is True), and a non-bool must be
    # rejected rather than silently degraded to non-streaming.
    raw_stream = body.get("stream")
    if raw_stream is not None and not isinstance(raw_stream, bool):
        return _bad_request("'stream' must be a boolean")
    stream = raw_stream is True
    try:
        reply_text = await _run_agent_turn(request.app.state, model, messages)
    except _BadRequest as e:
        return _openai_error(str(e), type="invalid_request_error", code="invalid_request", status=400)
    except _TurnError as e:
        return _openai_error(str(e), type="server_error", code="agent_error", status=502)
    except Exception as e:  # defensive: never leak internals as a 500 trace
        logger.exception("agent_model_api: turn failed")
        return _openai_error(
            "agent turn failed", type="server_error", code="agent_error", status=502
        )

    if stream:
        # Streaming not in the locked seam for this slice; return the completed
        # turn as a single SSE chunk so standard clients still work.
        return _chat_completion_stream(reply_text, model)
    return _chat_completion(reply_text, model)


class _TurnError(Exception):
    """Raised when the agent turn cannot be driven (server not ready, etc.)."""


class _BadRequest(Exception):
    """Raised when the request body is malformed (client error, -> 400)."""


async def _run_agent_turn(app_state, agent_id: str, messages: list) -> str:
    """Drive one non-streaming agent turn and return the final reply text.

    Reuses the host opencode server lifecycle (ensure_taos_opencode_server) and
    the opencode turn driver (drive_turn). Collects the 'final' reply from the
    sink; degrades to _TurnError on transport failure so the caller returns 502.

    The two runtime calls are referenced via module-level names so tests can
    monkeypatch them (no opencode binary required in CI).
    """
    # The last user message is the prompt; system/earlier messages are context
    # the agent harness already carries per-turn, so we pass the latest user text.
    # Validate content type before forwarding to drive_turn (Kilo finding: a
    # non-str / non-list content must not reach the adapter as a string).
    user_text: str | None = None
    for m in reversed(messages):
        if isinstance(m, dict) and m.get("role") == "user":
            content = m.get("content", "")
            if isinstance(content, str):
                user_text = content
            elif isinstance(content, list):  # content parts -> flatten to text
                parts = []
                for p in content:
                    if isinstance(p, dict):
                        if isinstance(p.get("text"), str):
                            parts.append(p["text"])
                        elif isinstance(p.get("text"), list):
                            parts.append(" ".join(str(x) for x in p["text"]))
                    elif isinstance(p, str):
                        parts.append(p)
                user_text = " ".join(parts)
            else:
                # content is int/null/object — malformed request.
                raise _BadRequest("message content must be a string or list of parts")
            break
    if not user_text:
        # Absent/empty user role is a client validation failure -> 400,
        # not a transport error (Kilo finding: was mapped to 502).
        raise _BadRequest("no user message found in request")

    server = await ensure_taos_opencode_server(app_state, agent_id)
    collected: dict = {"final": None}

    def _sink(reply: dict) -> None:
        if reply.get("kind") == "final":
            collected["final"] = reply.get("content", "")
        elif reply.get("kind") == "error" and collected["final"] is None:
            collected["_error"] = reply.get("error", "agent turn failed")

    await drive_turn(
        user_text,
        trace_id=None,
        sink=_sink,
        base_url=server.base_url,
        model_id=agent_id,
        model_provider_id="litellm",
        server_password=getattr(app_state, "taos_opencode_password", None),
    )
    if collected.get("_error") and collected["final"] is None:
        raise _TurnError(collected["_error"])
    if collected["final"] is None:
        raise _TurnError("agent returned no reply")
    return collected["final"]


def _chat_completion(content: str, model: str) -> JSONResponse:
    """OpenAI ChatCompletion (non-streaming) envelope."""
    return JSONResponse({
        "id": f"chatcmpl-{_short_id()}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    })


def _chat_completion_stream(content: str, model: str):
    """OpenAI SSE stream envelope (single completion chunk)."""
    from fastapi.responses import StreamingResponse

    def _gen():
        yield f"data: {json.dumps({'id': f'chatcmpl-{_short_id()}', 'object': 'chat.completion.chunk', 'created': int(time.time()), 'model': model, 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': content}, 'finish_reason': 'stop'}]})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream")


def _short_id() -> str:
    import secrets
    return secrets.token_hex(8)
