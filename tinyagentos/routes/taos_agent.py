"""taOS Assistant — settings, config and chat completion endpoint.

GET  /api/taos-agent/settings          → {model: str | null}
PATCH /api/taos-agent/settings         → accepts {model: str}, persists via desktop_settings
GET  /api/taos-agent/config            → {model, permitted_models, persona, key_masked, framework, system}
PUT  /api/taos-agent/permitted-models  → validate + persist permitted_models; re-scope the agent key
PUT  /api/taos-agent/persona           → persist persona (system-prompt override)
POST /api/taos-agent/chat              → streams chat completion via opencode (NDJSON)

The system prompt is read from docs/taos-agent-manual.md at module import time.
When a persona is set via PUT /api/taos-agent/persona it is used instead.
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


def _mask_key(key: str | None) -> str | None:
    """Return a masked form of a LiteLLM key (first 6 + … + last 4), or None.

    Keys too short for that pattern are fully masked rather than surfaced raw
    (GET /api/taos-agent/config returns this value).
    """
    if not key:
        return None
    if len(key) < 12:
        return "…"
    return key[:6] + "…" + key[-4:]


class SettingsPatch(BaseModel):
    model: str


class PermittedModelsUpdate(BaseModel):
    models: list[str]


class PersonaUpdate(BaseModel):
    persona: str


class ChatRequest(BaseModel):
    messages: list[dict]


@router.get("/api/taos-agent/settings")
async def get_settings(request: Request):
    store = request.app.state.desktop_settings
    prefs = await store.get_preference("user", _PREF_NAMESPACE)
    return JSONResponse({"model": prefs.get("model", None)})


@router.patch("/api/taos-agent/settings")
async def patch_settings(request: Request, body: SettingsPatch):
    from tinyagentos.routes.auth import _require_admin
    ok, err = _require_admin(request)
    if not ok:
        return err

    store = request.app.state.desktop_settings
    prefs = await store.get_preference("user", _PREF_NAMESPACE)
    prefs["model"] = body.model
    await store.save_preference("user", _PREF_NAMESPACE, prefs)
    return JSONResponse({"model": body.model})


@router.get("/api/taos-agent/config")
async def get_config(request: Request):
    """Return the full taOS agent configuration (model, permitted_models, persona, key)."""
    store = request.app.state.desktop_settings
    prefs = await store.get_preference("user", _PREF_NAMESPACE)

    raw_key: str | None = getattr(request.app.state, "taos_opencode_key", None)

    return JSONResponse({
        "model": prefs.get("model", None),
        "permitted_models": prefs.get("permitted_models", []),
        "persona": prefs.get("persona", ""),
        "key_masked": _mask_key(raw_key),
        "framework": "opencode",
        "system": True,
    })


@router.put("/api/taos-agent/permitted-models")
async def put_permitted_models(request: Request, body: PermittedModelsUpdate):
    """Set the taOS agent's permitted model set and re-scope its LiteLLM key."""
    from tinyagentos.routes.auth import _require_admin
    ok, err = _require_admin(request)
    if not ok:
        return err

    if not body.models:
        return JSONResponse({"error": "models must not be empty"}, status_code=400)

    store = request.app.state.desktop_settings
    prefs = await store.get_preference("user", _PREF_NAMESPACE)
    current_model: str | None = prefs.get("model")

    # Build final set — always include the current primary model.
    permitted = list(body.models)
    if current_model and current_model not in permitted:
        permitted = [current_model, *permitted]

    # Validate every model is reachable.
    from tinyagentos.cluster.model_resolver import resolve_model_location
    for model_id in permitted:
        location = resolve_model_location(request, model_id)
        if location.kind == "not_found":
            return JSONResponse(
                {
                    "error": f"model '{model_id}' is not reachable anywhere in the cluster right now.",
                    "model": model_id,
                },
                status_code=409,
            )

    prefs["permitted_models"] = permitted
    await store.save_preference("user", _PREF_NAMESPACE, prefs)

    # Re-scope the agent's own LiteLLM key.
    proxy = getattr(request.app.state, "llm_proxy", None)
    key: str | None = getattr(request.app.state, "taos_opencode_key", None)
    key_rescoped = False
    if proxy is not None and key:
        try:
            key_rescoped = await proxy.update_agent_key(key, permitted)
        except Exception:
            logger.exception("taos-agent: re-scoping key after permitted-models update failed")

    return JSONResponse({"permitted_models": permitted, "key_rescoped": key_rescoped})


@router.put("/api/taos-agent/persona")
async def put_persona(request: Request, body: PersonaUpdate):
    """Persist a system-prompt override for the taOS agent."""
    from tinyagentos.routes.auth import _require_admin
    ok, err = _require_admin(request)
    if not ok:
        return err

    store = request.app.state.desktop_settings
    prefs = await store.get_preference("user", _PREF_NAMESPACE)
    prefs["persona"] = body.persona
    await store.save_preference("user", _PREF_NAMESPACE, prefs)
    return JSONResponse({"persona": body.persona})


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

    # Use persona override if set, else fall back to the built-in manual.
    persona: str = prefs.get("persona", "").strip()
    system_prompt = persona if persona else (SYSTEM_PROMPT or None)

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
        system=system_prompt,
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
