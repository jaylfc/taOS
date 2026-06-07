from __future__ import annotations

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

router = APIRouter()


# ── Canvas ───────────────────────────────────────────────────────────────────

@router.post("/api/canvas/generate")
async def create_canvas(request: Request):
    """Create a new canvas page."""
    body = await request.json()
    canvas_store = request.app.state.canvas_store
    canvas = await canvas_store.create(
        title=body.get("title", "Untitled"),
        content=body.get("content", ""),
        style=body.get("style", "auto"),
        format=body.get("format", "markdown"),
        created_by=body.get("agent_name", "system"),
    )
    return {
        "canvas_id": canvas["id"],
        "canvas_url": f"/canvas/{canvas['id']}",
        "edit_token": canvas["edit_token"],
    }


@router.post("/api/canvas/{canvas_id}/update")
async def update_canvas(request: Request, canvas_id: str):
    """Update canvas content (requires edit_token)."""
    body = await request.json()
    canvas_store = request.app.state.canvas_store
    updated = await canvas_store.update(
        canvas_id,
        edit_token=body.get("edit_token", ""),
        content=body.get("content"),
        title=body.get("title"),
    )
    if not updated:
        return JSONResponse({"error": "Invalid edit token or canvas not found"}, status_code=403)
    # Broadcast to canvas viewers
    hub = request.app.state.chat_hub
    canvas = await canvas_store.get(canvas_id)
    if canvas:
        await hub.broadcast(f"canvas:{canvas_id}", {"type": "canvas_update", "content": canvas["content"], "title": canvas["title"]})
    return {"status": "updated"}


@router.get("/api/canvas/{canvas_id}/data")
async def canvas_data(request: Request, canvas_id: str):
    """Get canvas data as JSON."""
    canvas_store = request.app.state.canvas_store
    canvas = await canvas_store.get(canvas_id)
    if not canvas:
        return JSONResponse({"error": "Canvas not found"}, status_code=404)
    return canvas


@router.delete("/api/canvas/{canvas_id}")
async def delete_canvas(request: Request, canvas_id: str):
    canvas_store = request.app.state.canvas_store
    deleted = await canvas_store.delete(canvas_id)
    if not deleted:
        return JSONResponse({"error": "Canvas not found"}, status_code=404)
    return {"status": "deleted"}


@router.get("/api/canvas")
async def list_canvases(request: Request, limit: int = 50):
    canvas_store = request.app.state.canvas_store
    canvases = await canvas_store.list_all(limit=limit)
    return {"canvases": canvases}


@router.websocket("/ws/canvas/{canvas_id}")
async def canvas_ws(websocket: WebSocket, canvas_id: str):
    """WebSocket for live canvas updates."""
    auth_mgr = websocket.app.state.auth
    token = websocket.cookies.get("taos_session", "")
    if not token or auth_mgr.validate_session(token) is None:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    hub = websocket.app.state.chat_hub
    canvas_channel = f"canvas:{canvas_id}"
    hub.join(websocket, canvas_channel)
    try:
        while True:
            await websocket.receive_text()  # Keep connection alive
    except WebSocketDisconnect:
        pass
    finally:
        hub.leave(websocket, canvas_channel)
