from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, model_validator

from tinyagentos.mcp.proxy import call_tool
from tinyagentos.themes.schema import ThemeError, theme_vocabulary, validate_theme_config

router = APIRouter()


class AttachRequest(BaseModel):
    scope_kind: Literal["all", "agent", "group"]
    scope_id: str | None = None
    allowed_tools: list[str] = []
    allowed_resources: list[str] = []

    @model_validator(mode="after")
    def _check_scope_id(self) -> "AttachRequest":
        if self.scope_kind in ("agent", "group") and not self.scope_id:
            raise ValueError(f"scope_id is required when scope_kind is '{self.scope_kind}'")
        return self


class ConfigBody(BaseModel):
    config: dict


class CreateThemeBody(BaseModel):
    config: dict
    name: str
    theme_id: str


class PreviewThemeBody(BaseModel):
    theme_id: str


class EnvBody(BaseModel):
    env: dict[str, str]


@router.get("/api/mcp/servers")
async def list_servers(request: Request):
    store = request.app.state.mcp_store
    supervisor = request.app.state.mcp_supervisor
    servers = await store.list_servers()
    for s in servers:
        status = supervisor.get_status(s["id"])
        s.update(status)
    return JSONResponse({"servers": servers})


@router.post("/api/mcp/servers/{server_id}/start")
async def start_server(server_id: str, request: Request):
    supervisor = request.app.state.mcp_supervisor
    ok = await supervisor.start(server_id)
    if not ok:
        return JSONResponse({"error": "failed to start server"}, status_code=500)
    return JSONResponse({"ok": True, "server_id": server_id})


@router.post("/api/mcp/servers/{server_id}/stop")
async def stop_server(server_id: str, request: Request):
    supervisor = request.app.state.mcp_supervisor
    ok = await supervisor.stop(server_id)
    return JSONResponse({"ok": ok, "server_id": server_id})


@router.post("/api/mcp/servers/{server_id}/restart")
async def restart_server(server_id: str, request: Request):
    supervisor = request.app.state.mcp_supervisor
    ok = await supervisor.restart(server_id)
    if not ok:
        return JSONResponse({"error": "failed to restart server"}, status_code=500)
    return JSONResponse({"ok": True, "server_id": server_id})


@router.delete("/api/mcp/servers/{server_id}")
async def uninstall_server(server_id: str, request: Request):
    store = request.app.state.mcp_store
    supervisor = request.app.state.mcp_supervisor
    server = await store.get_server(server_id)
    if server is None:
        return JSONResponse({"error": "server not found"}, status_code=404)
    cascade = await supervisor.uninstall(server_id)
    return JSONResponse({"ok": True, "server_id": server_id, **cascade})


@router.get("/api/mcp/servers/{server_id}/logs")
async def get_logs(
    server_id: str,
    request: Request,
    since: int = 0,
    limit: int = 200,
):
    supervisor = request.app.state.mcp_supervisor
    lines = supervisor.logs(server_id, since_idx=since, limit=limit)
    return JSONResponse({"logs": lines, "count": len(lines)})


@router.get("/api/mcp/servers/{server_id}/logs/stream")
async def stream_logs(server_id: str, request: Request):
    supervisor = request.app.state.mcp_supervisor

    async def event_gen():
        try:
            async for entry in supervisor.stream_logs(server_id):
                if await request.is_disconnected():
                    break
                yield f"data: {json.dumps(entry)}\n\n"
        except Exception:
            pass

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/mcp/servers/{server_id}/capabilities")
async def get_capabilities(server_id: str, request: Request):
    store = request.app.state.mcp_store
    server = await store.get_server(server_id)
    if server is None:
        return JSONResponse({"error": "server not found"}, status_code=404)
    registry = getattr(request.app.state, "registry", None)
    capabilities: list = []
    if registry is not None:
        try:
            manifest = registry.get_manifest(server_id)
            if manifest:
                capabilities = getattr(manifest, "capabilities", [])
        except Exception:
            pass
    return JSONResponse({"server_id": server_id, "capabilities": capabilities})


@router.get("/api/mcp/servers/{server_id}/permissions")
async def list_permissions(server_id: str, request: Request):
    store = request.app.state.mcp_store
    attachments = await store.list_attachments(server_id)
    return JSONResponse({"server_id": server_id, "attachments": attachments})


@router.post("/api/mcp/servers/{server_id}/permissions")
async def attach_permission(server_id: str, body: AttachRequest, request: Request):
    store = request.app.state.mcp_store
    server = await store.get_server(server_id)
    if server is None:
        return JSONResponse({"error": "server not found"}, status_code=404)
    attachment_id = await store.add_attachment(
        server_id=server_id,
        scope_kind=body.scope_kind,
        scope_id=body.scope_id,
        allowed_tools=body.allowed_tools,
        allowed_resources=body.allowed_resources,
    )
    return JSONResponse({"ok": True, "attachment_id": attachment_id})


@router.delete("/api/mcp/servers/{server_id}/permissions/{attachment_id}")
async def delete_permission(server_id: str, attachment_id: int, request: Request):
    store = request.app.state.mcp_store
    removed = await store.delete_attachment(attachment_id)
    if not removed:
        return JSONResponse({"error": "attachment not found"}, status_code=404)
    return JSONResponse({"ok": True, "attachment_id": attachment_id})


@router.get("/api/mcp/servers/{server_id}/config")
async def get_config(server_id: str, request: Request):
    store = request.app.state.mcp_store
    config = await store.get_config(server_id)
    return JSONResponse({"server_id": server_id, "config": config})


@router.put("/api/mcp/servers/{server_id}/config")
async def put_config(server_id: str, body: ConfigBody, request: Request):
    store = request.app.state.mcp_store
    server = await store.get_server(server_id)
    if server is None:
        return JSONResponse({"error": "server not found"}, status_code=404)
    await store.set_config(server_id, body.config)
    return JSONResponse({"ok": True, "server_id": server_id})


@router.get("/api/mcp/servers/{server_id}/env")
async def get_env(server_id: str, request: Request):
    secrets_store = request.app.state.secrets
    prefix = f"mcp:{server_id}:"
    all_secrets = await secrets_store.list()
    env = {
        s["name"][len(prefix):]: s.get("value", "")
        for s in all_secrets
        if s["name"].startswith(prefix)
    }
    # Keys only are returned — no plaintext values exposed via GET
    return JSONResponse({"server_id": server_id, "env_keys": list(env.keys())})


@router.put("/api/mcp/servers/{server_id}/env")
async def put_env(server_id: str, body: EnvBody, request: Request):
    secrets_store = request.app.state.secrets
    prefix = f"mcp:{server_id}:"
    for key, value in body.env.items():
        name = f"{prefix}{key}"
        existing = await secrets_store.get(name)
        if existing:
            await secrets_store.update(name, value=value)
        else:
            await secrets_store.add(name, value, category="general")
    return JSONResponse({"ok": True, "server_id": server_id, "keys_set": list(body.env.keys())})


@router.get("/api/mcp/servers/{server_id}/used-by")
async def used_by(server_id: str, request: Request):
    store = request.app.state.mcp_store
    attachments = await store.list_attachments(server_id)
    agents = [
        {"scope_kind": a["scope_kind"], "scope_id": a["scope_id"]}
        for a in attachments
        if a["scope_kind"] in ("agent", "all")
    ]
    return JSONResponse({"server_id": server_id, "agents": agents})


@router.post("/api/mcp/call")
async def proxy_call(request: Request):
    body = await request.json()
    missing = [f for f in ("server_id", "tool", "agent_name") if not body.get(f)]
    if missing:
        return JSONResponse({"error": f"missing fields: {missing}"}, status_code=400)

    supervisor = request.app.state.mcp_supervisor
    store = request.app.state.mcp_store

    result = await call_tool(
        supervisor=supervisor,
        store=store,
        agent_name=body["agent_name"],
        agent_groups=body.get("agent_groups", []),
        server_id=body["server_id"],
        tool=body["tool"],
        arguments=body.get("arguments", {}),
        resource=body.get("resource"),
    )
    status_code = result.pop("status", 200) if "error" in result else 200
    return JSONResponse(result, status_code=status_code)


# --- taOS assistant theme tools ----------------------------------------------
# These three endpoints are the agent-facing tools that let the taOS assistant
# discover the theme vocabulary, create a validated theme, and request a preview.


@router.get("/api/mcp/tools/get_theme_schema")
async def get_theme_schema(request: Request):
    """Return the machine-readable theme vocabulary the agent generates against."""
    return JSONResponse(theme_vocabulary())


@router.post("/api/mcp/tools/create_theme")
async def create_theme(body: CreateThemeBody, request: Request):
    """Validate a generated theme config and install it.

    On a validation failure return ``{"error": ...}`` (HTTP 200) so the agent
    can repair the config and retry rather than treating it as a hard fault.
    """
    try:
        validated = validate_theme_config(body.config)
    except ThemeError as exc:
        return JSONResponse({"error": str(exc)})
    store = request.app.state.themes
    await store.install(
        theme_id=body.theme_id,
        name=body.name,
        version="1.0.0",
        config=validated,
    )
    return JSONResponse({"theme_id": body.theme_id})


@router.post("/api/mcp/tools/preview_theme")
async def preview_theme(body: PreviewThemeBody, request: Request):
    """Ask the desktop SPA to enter preview for ``theme_id``.

    The only event bus on app.state is the persisted NotificationStore, which
    is not a transient desktop signal carrying a payload the SPA can act on for
    live preview. Per the Task 5 fallback, we return the preview intent and
    leave the SPA-side wiring (an actual desktop event/channel) to a later
    frontend task.
    """
    return JSONResponse({"theme_id": body.theme_id, "preview": True})
