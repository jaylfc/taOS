"""The G1 routes: ``GET /api/llm/v1/models`` and non-streaming ``POST /api/llm/v1/chat/completions``.

Auth and model permission come ONLY from the ``gateway_caller`` dependency
(see auth.py). Mounted by ``mount`` when ``TAOS_LLM_GATEWAY=1``.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from tinyagentos.llm_gateway.auth import GatewayCaller, gateway_caller
from tinyagentos.llm_gateway.errors import (
    GatewayError,
    bad_request,
    handle_gateway_error,
    model_not_permitted,
)
from tinyagentos.llm_gateway.forward import chat_completion, resolve_api_key
from tinyagentos.llm_gateway.resolve import TAOS_DEFAULT, model_names, resolve, routing_table

PREFIX = "/api/llm/v1"
OPENAI_PROVIDER = "openai"

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
    if stream:
        raise GatewayError(400, "streaming lands in G3", code="stream_unsupported")
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
    # Resolve ONCE: the route checked below is the route forwarded to, so a
    # default changed mid-request cannot slip between the check and the call.
    route = await resolve(state, requested)
    # Alias rule: a caller granted taos-default may use whatever it CURRENTLY
    # resolves to (only the owner sets the default, so the grant is "whatever
    # the owner chose"; a board keyed to ["taos-default"] keeps working when
    # the default changes). Every other resolution is checked on its own.
    alias_grant = requested == TAOS_DEFAULT and caller.may_use(TAOS_DEFAULT)
    if route.model_name != requested and not alias_grant and not caller.may_use(route.model_name):
        raise model_not_permitted(route.model_name)
    if route.provider != OPENAI_PROVIDER:
        raise GatewayError(
            501,
            f"model {route.model_name!r} is served by a {route.provider or 'unknown'!r} backend; "
            "the taOS gateway only forwards to OpenAI-compatible backends so far",
            code="backend_not_supported",
        )
    api_key = await resolve_api_key(state, route.api_key_ref)
    return JSONResponse(await chat_completion(route, body, api_key))
