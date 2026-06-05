"""taOS Assistant — settings and chat completion endpoint.

GET  /api/taos-agent/settings  → {model: str | null}
PATCH /api/taos-agent/settings → accepts {model: str}, persists via desktop_settings
POST  /api/taos-agent/chat     → streams chat completion via opencode (NDJSON)

The system prompt is read from docs/taos-agent-manual.md at module import time.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from tinyagentos.adapters.opencode_adapter import OpenCodeAdapter, OpenCodeConfig
from tinyagentos.taos_agent_runtime import ensure_taos_opencode_server

logger = logging.getLogger(__name__)
router = APIRouter()

_PREF_NAMESPACE = "taos_agent"
_MANUAL_PATH = Path(__file__).resolve().parent.parent.parent / "docs" / "taos-agent-manual.md"

# Sentinel object placed on the queue to signal the stream is done.
_DONE = object()


# Read the system-prompt manual once at startup (or import time).
# If the file is absent the assistant still works — it just won't have a
# system prompt until the file is created and the server restarted.
def _load_manual() -> str:
    try:
        return _MANUAL_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("taos-agent-manual.md not found at %s", _MANUAL_PATH)
        return ""


SYSTEM_PROMPT: str = _load_manual()


class SettingsPatch(BaseModel):
    model: str


class ChatRequest(BaseModel):
    messages: list[dict]


@router.get("/api/taos-agent/settings")
async def get_settings(request: Request):
    store = request.app.state.desktop_settings
    prefs = await store.get_preference("user", _PREF_NAMESPACE)
    return JSONResponse({"model": prefs.get("model", None)})


@router.patch("/api/taos-agent/settings")
async def patch_settings(request: Request, body: SettingsPatch):
    store = request.app.state.desktop_settings
    prefs = await store.get_preference("user", _PREF_NAMESPACE)
    prefs["model"] = body.model
    await store.save_preference("user", _PREF_NAMESPACE, prefs)
    return JSONResponse({"model": body.model})


@router.post("/api/taos-agent/chat")
async def chat(request: Request, body: ChatRequest):
    """Stream a chat completion through a host opencode server.

    Returns NDJSON where each line is a JSON object with a ``delta`` string
    field, followed by a final ``{"done": true}`` line.  The frontend reads
    with a streaming fetch + TextDecoder.
    """
    store = request.app.state.desktop_settings
    prefs = await store.get_preference("user", _PREF_NAMESPACE)
    model = prefs.get("model")
    if not model:
        return JSONResponse(
            {"error": "No model configured. Open taOS Assistant settings and pick a model first."},
            status_code=400,
        )

    llm_proxy = getattr(request.app.state, "llm_proxy", None)
    proxy_running = llm_proxy is not None and llm_proxy.is_running()
    if not proxy_running:
        return JSONResponse(
            {"error": "LiteLLM proxy is not running. Check that at least one provider is configured."},
            status_code=503,
        )

    # Ensure the host opencode server is running.
    try:
        server = await ensure_taos_opencode_server(request.app.state, model)
    except Exception:
        logger.exception("taos-agent: failed to start opencode server")
        return JSONResponse(
            {"error": "taOS agent runtime unavailable. Check that opencode is installed."},
            status_code=503,
        )

    # Extract the latest user message text.
    text = body.messages[-1].get("content", "") if body.messages else ""
    if not text:
        return JSONResponse({"error": "Empty message."}, status_code=400)

    app_state = request.app.state
    queue: asyncio.Queue = asyncio.Queue()

    def sink(reply: dict) -> None:
        """Map adapter reply dicts onto NDJSON queue items."""
        kind = reply.get("kind")
        if kind == "delta":
            queue.put_nowait({"delta": reply.get("content", "")})
        elif kind == "error":
            queue.put_nowait({"error": reply.get("error", "error")})
            queue.put_nowait(_DONE)
        elif kind == "final":
            # Text already arrived as deltas; final just signals completion.
            queue.put_nowait(_DONE)
        # reasoning / tool_call / tool_result — not rendered by the panel; ignore.

    cfg = OpenCodeConfig(
        base_url=server.base_url,
        server_password=app_state.taos_opencode_password,
        model_provider_id="litellm",
        model_id=model,
        system=SYSTEM_PROMPT or None,
    )
    adapter = OpenCodeAdapter(cfg, sink)
    # Reuse the persistent session so opencode keeps conversation history.
    adapter.session_id = getattr(app_state, "taos_opencode_session_id", None)

    async def _drive() -> None:
        """Run the opencode turn; always puts a done-sentinel when finished."""
        try:
            await adapter.ensure_session()
            app_state.taos_opencode_session_id = adapter.session_id
            trace_id = uuid.uuid4().hex
            await adapter.prompt(text, trace_id=trace_id)
            await adapter.close()
        except Exception as exc:
            logger.exception("taos-agent: drive task error")
            queue.put_nowait({"error": str(exc)})
        finally:
            queue.put_nowait(_DONE)

    drive_task = asyncio.create_task(_drive())

    async def _generate():
        try:
            while True:
                item = await queue.get()
                if item is _DONE:
                    break
                yield json.dumps(item) + "\n"
        except Exception as exc:
            logger.exception("taos-agent: generator error")
            yield json.dumps({"error": str(exc)}) + "\n"
        finally:
            # Ensure the drive task is awaited so exceptions surface in logs.
            if not drive_task.done():
                drive_task.cancel()
                try:
                    await drive_task
                except (asyncio.CancelledError, Exception):
                    pass
            elif not drive_task.cancelled():
                exc = drive_task.exception()
                if exc is not None:
                    logger.error("taos-agent: drive task raised %r", exc)
        yield json.dumps({"done": True}) + "\n"

    return StreamingResponse(
        _generate(),
        media_type="application/x-ndjson",
    )
