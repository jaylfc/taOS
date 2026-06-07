"""taOS agent — settings, config, chat and file attachments.

GET  /api/taos-agent/settings                  → {model: str | null}
PATCH /api/taos-agent/settings                 → accepts {model: str}, persists via desktop_settings
GET  /api/taos-agent/config                    → {model, permitted_models, persona, key_masked, framework, system}
PUT  /api/taos-agent/permitted-models          → validate + persist permitted_models; re-scope the agent key
PUT  /api/taos-agent/persona                   → persist persona (system-prompt override)
POST /api/taos-agent/chat                      → streams chat completion via opencode (NDJSON)
POST /api/taos-agent/attachments/upload        → accepts a file, returns a persistent attachment record
GET  /api/taos-agent/attachments/files/{name}  → serve a stored attachment

The system prompt is read from docs/taos-agent-manual.md at module import time.
When a persona is set via PUT /api/taos-agent/persona it is used instead.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from tinyagentos.adapters.opencode_adapter import OpenCodeAdapter, OpenCodeConfig
from tinyagentos.taos_agent_runtime import ensure_taos_opencode_server

logger = logging.getLogger(__name__)
router = APIRouter()

_PREF_NAMESPACE = "taos_agent"
_MANUAL_PATH = Path(__file__).resolve().parent.parent.parent / "docs" / "taos-agent-manual.md"

_DONE = object()


def _load_manual() -> str:
    try:
        return _MANUAL_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("taos-agent-manual.md not found at %s", _MANUAL_PATH)
        return ""


SYSTEM_PROMPT: str = _load_manual()


def _mask_key(key: str | None) -> str | None:
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
    attachments: list[dict] | None = None
    """Optional multimodal attachments for the last user turn."""


class AttachmentMeta(BaseModel):
    mime_type: str
    data_b64: str


@router.post("/api/taos-agent/attachments/upload")
async def upload_attachment(request: Request, file: UploadFile = File(...)):
    """Accept a file upload, store it, and return a record the frontend can
    embed inline when sending the chat prompt."""
    data_dir: Path = request.app.state.data_dir
    upload_dir = data_dir / "taos-agent-files"
    upload_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(file.filename or "file").suffix
    stored_name = f"{uuid.uuid4().hex[:12]}{ext}"
    dest = upload_dir / stored_name
    content = await file.read()
    _MAX_BYTES = 50 * 1024 * 1024
    if len(content) > _MAX_BYTES:
        return JSONResponse({"error": "file too large (50 MB max)"}, status_code=413)
    dest.write_bytes(content)
    mime, _ = mimetypes.guess_type(file.filename or "")
    return JSONResponse({
        "filename": file.filename or "uploaded",
        "mime_type": mime or file.content_type or "application/octet-stream",
        "size": len(content),
        "url": f"/api/taos-agent/attachments/files/{stored_name}",
    })


@router.get("/api/taos-agent/attachments/files/{token}")
async def serve_attachment(request: Request, token: str):
    """Serve a previously uploaded taOS agent attachment."""
    data_dir: Path = request.app.state.data_dir
    upload_dir = data_dir / "taos-agent-files"
    # Resolve by prefix — the actual filename is {token_prefix}-{original}
    candidates = list(upload_dir.glob(f"{token}*"))
    if not candidates:
        return JSONResponse({"error": "file not found"}, status_code=404)
    file_path = candidates[0]
    if not file_path.exists() or not file_path.resolve().is_relative_to(upload_dir.resolve()):
        return JSONResponse({"error": "file not found"}, status_code=404)
    return FileResponse(file_path)


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
    with a streaming fetch + TextDecoder.  ``attachments`` are optional:
    each entry embeds a stored file as a base64 image in the last user turn.
    """
    store = request.app.state.desktop_settings
    prefs = await store.get_preference("user", _PREF_NAMESPACE)
    model = prefs.get("model")
    if not model:
        return JSONResponse(
            {"error": "No model configured. Open taOS agent settings and pick a model first."},
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
            queue.put_nowait(_DONE)

    cfg = OpenCodeConfig(
        base_url=server.base_url,
        server_password=app_state.taos_opencode_password,
        model_provider_id="litellm",
        model_id=model,
        system=system_prompt,
    )
    adapter = OpenCodeAdapter(cfg, sink)
    adapter.session_id = getattr(app_state, "taos_opencode_session_id", None)

    text = body.messages[-1].get("content", "") if body.messages else ""

    # Resolve URL references → base64-encoded dicts the adapter can embed.
    data_dir: Path = request.app.state.data_dir
    upload_dir = data_dir / "taos-agent-files"
    attachments: list[dict] = []
    _MAX_EMBED = 50 * 1024 * 1024
    for ref in body.attachments or []:
        url: str = ref.get("url", "")
        mime: str = ref.get("mime_type", "application/octet-stream")
        # Extract stored filename from the URL path segment.
        stored_name = url.split("/")[-1] if url else ""
        if not stored_name:
            continue
        file_path = upload_dir / stored_name
        try:
            resolved = file_path.resolve()
        except Exception:
            continue
        if not resolved.is_relative_to(upload_dir.resolve()):
            continue
        if not resolved.exists():
            continue
        data = resolved.read_bytes()
        if len(data) > _MAX_EMBED:
            continue
        attachments.append({
            "mime_type": mime,
            "data_b64": base64.b64encode(data).decode(),
        })

    async def _drive() -> None:
        try:
            await adapter.ensure_session()
            app_state.taos_opencode_session_id = adapter.session_id
            trace_id = uuid.uuid4().hex
            await adapter.prompt(text, trace_id=trace_id, attachments=attachments)
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
