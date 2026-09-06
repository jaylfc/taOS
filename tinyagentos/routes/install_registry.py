from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.routes.auth import _require_admin

router = APIRouter()


class RecordInstallBody(BaseModel):
    item_id: str
    item_kind: str
    version: str
    location_kind: str
    location_ref: str
    update_channel: str = "stable"


class SetVersionBody(BaseModel):
    version: str


@router.get("/api/installs")
async def list_installs(request: Request, item_id: str | None = None, location_ref: str | None = None):
    store = request.app.state.install_registry
    return await store.list(item_id=item_id, location_ref=location_ref)


@router.get("/api/installs/{entry_id}")
async def get_install(request: Request, entry_id: str):
    store = request.app.state.install_registry
    row = await store.get(entry_id)
    if row is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return row


@router.post("/api/installs")
async def record_install(request: Request, body: RecordInstallBody):
    ok, err = _require_admin(request)
    if not ok:
        return err
    store = request.app.state.install_registry
    row = await store.record(
        item_id=body.item_id,
        item_kind=body.item_kind,
        version=body.version,
        location_kind=body.location_kind,
        location_ref=body.location_ref,
        update_channel=body.update_channel,
    )
    return row


@router.patch("/api/installs/{entry_id}")
async def set_install_version(request: Request, entry_id: str, body: SetVersionBody):
    ok, err = _require_admin(request)
    if not ok:
        return err
    store = request.app.state.install_registry
    row = await store.set_version(entry_id, body.version)
    if row is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return row


@router.delete("/api/installs/{entry_id}")
async def delete_install(request: Request, entry_id: str):
    ok, err = _require_admin(request)
    if not ok:
        return err
    store = request.app.state.install_registry
    removed = await store.delete(entry_id)
    if not removed:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"ok": True}
