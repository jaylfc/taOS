from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import taosmd
import taosmd.agents as tm_agents
from taosmd.agents import AgentNotFoundError

router = APIRouter()


class LibrarianPatch(BaseModel):
    enabled: bool | None = None
    tasks: dict[str, bool] | None = None
    fanout: str | None = None
    fanout_auto_scale: bool | None = None


class MemoryModelUpdate(BaseModel):
    model: str | None = None
    clear: bool = False


@router.get("/api/agents/{slug}/librarian")
async def get_librarian(slug: str):
    try:
        return tm_agents.get_librarian(slug)
    except AgentNotFoundError:
        return JSONResponse(status_code=404, content={"detail": f"agent {slug!r} not found"})


@router.patch("/api/agents/{slug}/librarian")
async def patch_librarian(slug: str, body: LibrarianPatch):
    kwargs = body.model_dump(exclude_none=True)
    try:
        result = tm_agents.set_librarian(slug, **kwargs)
        return result
    except AgentNotFoundError:
        return JSONResponse(status_code=404, content={"detail": f"agent {slug!r} not found"})
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})


@router.get("/api/memory/model")
async def get_memory_model():
    getter = getattr(taosmd, "get_memory_model", None)
    if getter is None:
        return {"model": None, "supported": False}
    return {"model": getter(), "supported": True}


@router.put("/api/memory/model")
async def set_memory_model(request: Request, body: MemoryModelUpdate):
    # System-wide setting — only an admin may change which model powers memory
    # for the whole install. (GET stays open: it returns only a model-id string.)
    from tinyagentos.routes.auth import _require_admin
    ok, err = _require_admin(request)
    if not ok:
        return err
    setter = getattr(taosmd, "set_memory_model", None)
    if setter is None:
        return JSONResponse(status_code=501, content={"detail": "installed taosmd has no system memory-model API"})
    if not body.clear and not (body.model and body.model.strip()):
        return JSONResponse(status_code=400, content={"detail": "model is required unless clear=true"})
    try:
        setter(body.model or "", clear=body.clear)
    except Exception as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
    getter = getattr(taosmd, "get_memory_model", None)
    return {"model": getter() if getter else None}
