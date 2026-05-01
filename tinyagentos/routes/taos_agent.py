"""taOS Assistant — settings and chat completion endpoint.

GET  /api/taos-agent/settings  → {model: str | null}
PATCH /api/taos-agent/settings → accepts {model: str}, persists via desktop_settings
POST  /api/taos-agent/chat     → streams chat completion via LiteLLM proxy (NDJSON)

The system prompt is read from docs/taos-agent-manual.md at module import time.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from tinyagentos.llm_proxy import TAOS_LITELLM_MASTER_KEY

logger = logging.getLogger(__name__)
router = APIRouter()

_PREF_NAMESPACE = "taos_agent"
_LITELLM_URL = "http://localhost:4000"
_MANUAL_PATH = Path(__file__).resolve().parent.parent.parent / "docs" / "taos-agent-manual.md"

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
    """Stream a chat completion through the LiteLLM proxy.

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

    # Build the messages list: system prompt prepended, then user messages.
    messages: list[dict] = []
    if SYSTEM_PROMPT:
        messages.append({"role": "system", "content": SYSTEM_PROMPT})
    messages.extend(body.messages)

    llm_proxy = getattr(request.app.state, "llm_proxy", None)
    proxy_running = llm_proxy is not None and llm_proxy.is_running()
    if not proxy_running:
        return JSONResponse(
            {"error": "LiteLLM proxy is not running. Check that at least one provider is configured."},
            status_code=503,
        )

    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
    }

    async def _generate():
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream(
                    "POST",
                    f"{_LITELLM_URL}/v1/chat/completions",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {TAOS_LITELLM_MASTER_KEY}",
                        "Content-Type": "application/json",
                    },
                ) as resp:
                    if resp.status_code != 200:
                        body_text = await resp.aread()
                        error_msg = body_text.decode(errors="replace")[:500]
                        yield json.dumps({"error": f"LLM proxy error {resp.status_code}: {error_msg}"}) + "\n"
                        return

                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data = line[6:]
                        if data.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        delta = (
                            chunk.get("choices", [{}])[0]
                            .get("delta", {})
                            .get("content", "")
                        )
                        if delta:
                            yield json.dumps({"delta": delta}) + "\n"
        except httpx.ConnectError:
            yield json.dumps({"error": "Cannot connect to LiteLLM proxy."}) + "\n"
        except Exception as exc:
            logger.exception("taos-agent chat error")
            yield json.dumps({"error": str(exc)}) + "\n"
        yield json.dumps({"done": True}) + "\n"

    return StreamingResponse(
        _generate(),
        media_type="application/x-ndjson",
    )
