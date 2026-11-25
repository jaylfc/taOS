"""Ollama-compatible surface (#130): taOS agents behind Ollama's API shape.

An external Ollama client (Open WebUI, the Ollama CLI, ...) points at
https://<taos> with the SAME consent key the OpenAI-compatible surface
(#129, routes/agent_model_api.py) uses, and gets the same model set — the
agents that key is consented for — behind Ollama's wire shapes:

- GET  /api/tags     -> {"models": [...]} in Ollama's format
- POST /api/generate -> one agent turn, Ollama generate shape
- POST /api/chat     -> one agent turn, Ollama chat shape

Auth and turn execution are NOT re-implemented here: resolve_consent_key
and _run_agent_turn are imported from agent_model_api, so the consent
contract (valid key, requested model in the key's agent_ids) and the
opencode turn seam are shared with /v1 by construction. Errors use
Ollama's envelope ({"error": "<message>"}) rather than OpenAI's object
envelope, because that is what Ollama clients parse.

Deliberate parity choices where Ollama and OpenAI differ:
- `stream` DEFAULTS TO TRUE, as in Ollama's API (OpenAI defaults false).
  A non-boolean `stream` is still a 400, mirroring the /v1 contract.
- Streaming answers are NDJSON (one JSON object per line), Ollama-style,
  not SSE. The shared turn seam is non-streaming, so a streamed reply is
  emitted as one content chunk followed by the terminal done chunk —
  the same shape agent_model_api uses for its single-chunk SSE stream.

Out of scope for this slice: /api/pull and /api/delete. Both mutate the
model catalog/downloads, which is a different consent question than
driving a turn with a key; they are flagged as follow-ups in the PR.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

# The consent-key guard and the turn seam are shared with the
# OpenAI-compatible surface on purpose (see module docstring): one key
# store, one scope contract, one turn path, two wire shapes.
from tinyagentos.routes.agent_model_api import (
    _BadRequest,
    _run_agent_turn,
    _TurnError,
    resolve_consent_key,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _ollama_error(message: str, *, status: int) -> JSONResponse:
    """Ollama-shaped error envelope: a plain string message."""
    return JSONResponse({"error": message}, status_code=status)


def _unauthorized() -> JSONResponse:
    return _ollama_error("invalid or missing consent key", status=401)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _stats(started: float) -> dict:
    """Ollama's duration/count fields. The agent turn seam reports no token
    counts (the /v1 surface zeroes them too), so counts are 0 and only the
    wall-clock total is real."""
    total_ns = int((time.perf_counter() - started) * 1_000_000_000)
    return {
        "total_duration": total_ns,
        "load_duration": 0,
        "prompt_eval_count": 0,
        "prompt_eval_duration": 0,
        "eval_count": 0,
        "eval_duration": total_ns,
    }


@router.get("/api/tags")
async def list_tags(request: Request):
    """Ollama /api/tags: the agents this consent key may address, as models.

    The model set is exactly the /v1/models set (the key's agent_ids), so
    tags, chat and generate can never disagree about what a key may use.
    """
    binding = await resolve_consent_key(request)
    if binding is None:
        return _unauthorized()
    created = binding.get("created_at")
    modified_at = created if isinstance(created, str) and created else _now()
    models = []
    for agent_id in binding.get("agent_ids", []):
        models.append({
            "name": agent_id,
            "model": agent_id,
            "modified_at": modified_at,
            "size": 0,
            "digest": "sha256:" + hashlib.sha256(agent_id.encode()).hexdigest(),
            "details": {
                "parent_model": "",
                "format": "taos-agent",
                "family": "taos-agent",
                "families": ["taos-agent"],
                "parameter_size": "",
                "quantization_level": "",
            },
        })
    return {"models": models}


async def _parse_and_authorize(request: Request):
    """Shared prelude for generate/chat: consent key first (an
    unauthenticated caller always gets 401, never schema feedback), then
    manual body parsing so every error stays in the Ollama envelope
    rather than FastAPI's default 422 {"detail": ...}.

    Returns (binding, body, None) or (None, None, error_response).
    """
    binding = await resolve_consent_key(request)
    if binding is None:
        return None, None, _unauthorized()
    try:
        body = await request.json()
    except Exception:
        return None, None, _ollama_error("request body must be valid JSON", status=400)
    if not isinstance(body, dict):
        return None, None, _ollama_error("request body must be a JSON object", status=400)
    return binding, body, None


def _validate_model_and_stream(binding: dict, body: dict):
    """Validate model presence + scope and the stream flag.

    Returns (model, stream, None) or (None, None, error_response). Scope
    failure is a 404 that does not leak whether the agent exists for
    another key (same reasoning as /v1's model_not_found).
    """
    model = body.get("model")
    if not isinstance(model, str) or not model:
        return None, None, _ollama_error("you must provide a 'model' parameter", status=400)
    if model not in binding.get("agent_ids", []):
        return None, None, _ollama_error(f"model '{model}' not found", status=404)
    raw_stream = body.get("stream")
    if raw_stream is not None and not isinstance(raw_stream, bool):
        return None, None, _ollama_error("'stream' must be a boolean", status=400)
    # Ollama parity: streaming is the default (OpenAI's /v1 defaults off).
    stream = raw_stream is not False
    return model, stream, None


async def _drive_turn(request: Request, model: str, messages: list):
    """Run one agent turn via the shared /v1 seam.

    Returns (reply_text, started_perf_counter, None) or
    (None, None, error_response), mapping the seam's exceptions onto the
    Ollama envelope: client-shape problems 400, turn failures 502.
    """
    started = time.perf_counter()
    try:
        reply_text = await _run_agent_turn(request.app.state, model, messages)
    except _BadRequest as e:
        return None, None, _ollama_error(str(e), status=400)
    except _TurnError as e:
        return None, None, _ollama_error(str(e), status=502)
    except Exception:  # defensive: never leak internals as a 500 trace
        logger.exception("ollama_compat: turn failed")
        return None, None, _ollama_error("agent turn failed", status=502)
    return reply_text, started, None


def _respond(*, kind: str, model: str, reply_text: str, started: float, stream: bool):
    """Build the Ollama answer for one completed turn.

    kind is "chat" (content under message.content) or "generate" (content
    under response). Non-streaming is a single JSON object with done=true;
    streaming is NDJSON — one content chunk, then the terminal done chunk
    carrying the stats.
    """
    if kind == "chat":
        def _chunk(content: str, done: bool) -> dict:
            chunk = {
                "model": model,
                "created_at": _now(),
                "message": {"role": "assistant", "content": content},
                "done": done,
            }
            if done:
                chunk["done_reason"] = "stop"
            return chunk
    else:
        def _chunk(content: str, done: bool) -> dict:
            chunk = {
                "model": model,
                "created_at": _now(),
                "response": content,
                "done": done,
            }
            if done:
                chunk["done_reason"] = "stop"
            return chunk

    if not stream:
        final = _chunk(reply_text, True)
        final.update(_stats(started))
        return JSONResponse(final)

    def _gen():
        yield json.dumps(_chunk(reply_text, False)) + "\n"
        final = _chunk("", True)
        final.update(_stats(started))
        yield json.dumps(final) + "\n"

    return StreamingResponse(_gen(), media_type="application/x-ndjson")


@router.post("/api/chat")
async def chat(request: Request):
    """Ollama /api/chat: one agent turn from an Ollama message list."""
    binding, body, error = await _parse_and_authorize(request)
    if error is not None:
        return error
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return _ollama_error("'messages' must contain at least one message", status=400)
    model, stream, error = _validate_model_and_stream(binding, body)
    if error is not None:
        return error
    reply_text, started, error = await _drive_turn(request, model, messages)
    if error is not None:
        return error
    return _respond(kind="chat", model=model, reply_text=reply_text,
                    started=started, stream=stream)


@router.post("/api/generate")
async def generate(request: Request):
    """Ollama /api/generate: one agent turn from a single prompt.

    The prompt becomes the user message (with `system`, when given, as a
    leading system message) for the shared turn builder, which requires a
    non-empty user text — an empty prompt is a 400 here rather than a
    turn-seam error.
    """
    binding, body, error = await _parse_and_authorize(request)
    if error is not None:
        return error
    prompt = body.get("prompt")
    if not isinstance(prompt, str) or not prompt:
        return _ollama_error("you must provide a 'prompt' parameter", status=400)
    model, stream, error = _validate_model_and_stream(binding, body)
    if error is not None:
        return error
    messages = []
    system = body.get("system")
    if isinstance(system, str) and system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    reply_text, started, error = await _drive_turn(request, model, messages)
    if error is not None:
        return error
    return _respond(kind="generate", model=model, reply_text=reply_text,
                    started=started, stream=stream)
