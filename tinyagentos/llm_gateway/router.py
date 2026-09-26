from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from tinyagentos.llm_gateway.auth import GatewayCaller, gateway_caller
from tinyagentos.llm_gateway.errors import (
    GatewayError,
    bad_request,
    handle_gateway_error,
    model_not_found,
    model_not_permitted,
)
from tinyagentos.llm_gateway.forward import chat_completion, chat_completion_stream, resolve_api_key
from tinyagentos.llm_gateway.anthropic import chat_completion_anthropic
from tinyagentos.llm_gateway.resolve import TAOS_DEFAULT, find_routes, model_names, routing_table
import tinyagentos.llm_gateway.resolve as resolve_mod

PREFIX = "/api/llm/v1"
OPENAI_PROVIDER = "openai"
OLLAMA_PROVIDERS = ("ollama", "ollama_chat")

router = APIRouter(prefix=PREFIX)


def mount(app, dependencies=None) -> None:
    app.include_router(router, dependencies=dependencies or [])
    app.add_exception_handler(GatewayError, handle_gateway_error)


def _model_entry(name: str) -> dict:
    return {"id": name, "object": "model", "created": 0, "owned_by": "taos"}


@router.get("/models")
async def list_models(request: Request, caller: GatewayCaller = Depends(gateway_caller)):
    names = [TAOS_DEFAULT] + [
        n for n in model_names(routing_table(request.app.state)) if n != TAOS_DEFAULT
    ]
    return {"object": "list", "data": [_model_entry(n) for n in names if caller.may_use(n)]}


async def _read_body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - any parse failure is the caller's
        raise bad_request("request body must be a JSON object") from None
    if not isinstance(body, dict):
        raise bad_request("request body must be a JSON object")
    model = body.get("model")
    if not isinstance(model, str) or not model.strip():
        raise bad_request("'model' must be a non-empty string")
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise bad_request("'messages' must be a non-empty array")
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict) or not isinstance(msg.get("role"), str):
            raise bad_request(f"'messages[{i}]' must be an object with a string 'role'")
    stream = body.get("stream")
    if stream is not None and not isinstance(stream, bool):
        raise bad_request("'stream' must be a boolean")
    if body.get("tools") is not None and not isinstance(body["tools"], list):
        raise bad_request("'tools' must be an array")
    return body


@router.post("/chat/completions")
async def chat_completions(request: Request, caller: GatewayCaller = Depends(gateway_caller)):
    body = await _read_body(request)
    requested = body["model"]
    if not caller.may_use(requested):
        raise model_not_permitted(requested)
    state = request.app.state
    name = requested
    if requested == TAOS_DEFAULT:
        name = await resolve_mod.default_chat_model(state)
        if name is None:
            raise model_not_found(
                "taos-default has nothing behind it: no default chat model is set. "
                "Pick one in the taOS agent settings."
            )
    routes = find_routes(routing_table(state), name)
    if not routes:
        suffix = f" (taos-default points at it)" if name != requested else ""
        raise model_not_found(f"model {name!r} not found{suffix}")
    route = routes[0]
    alias_grant = requested == TAOS_DEFAULT and caller.may_use(TAOS_DEFAULT)
    if route.model_name != requested and not alias_grant and not caller.may_use(route.model_name):
        raise model_not_permitted(route.model_name)
    
    # Route through appropriate handler based on provider
    if route.provider == "anthropic":
        api_key = await resolve_api_key(state, route.api_key_ref)
        principal = caller.caller_id
        if body.get("stream"):
            from fastapi.responses import StreamingResponse
            from tinyagentos.llm_gateway.anthropic import chat_completion_stream_anthropic
            return StreamingResponse(
                chat_completion_stream_anthropic(routes, body, api_key, principal, state),
                media_type="text/event-stream",
                headers={"cache-control": "no-cache"}
            )
        return JSONResponse(await chat_completion_anthropic(routes, body, api_key, principal, state))
    
    # Default to OpenAI-compatible handler
    if route.provider != OPENAI_PROVIDER and route.provider not in OLLAMA_PROVIDERS:
        raise GatewayError(
            501,
            f"model {route.model_name!r} is served by a {route.provider or 'unknown'!r} backend; "
            "the taOS gateway only forwards to OpenAI-compatible backends so far",
            code="backend_not_supported",
        )
    
    api_key = await resolve_api_key(state, route.api_key_ref)
    principal = caller.caller_id
    if body.get("stream"):
        return await chat_completion_stream(routes, body, api_key, principal, state)
    return JSONResponse(await chat_completion(routes, body, api_key, principal, state))
