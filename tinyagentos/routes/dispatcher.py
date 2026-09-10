from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.auth_context import CurrentUser, current_user, require_owner_or_admin

logger = logging.getLogger(__name__)
router = APIRouter()


class DispatcherConfigIn(BaseModel):
    enabled: bool | None = None
    boards: list[str] | None = None
    eligible_agents: list[str] | None = None
    max_concurrent_per_agent: int | None = None
    poll_seconds: int | None = None


@router.get("/api/dispatcher/config")
async def get_dispatcher_config(
    request: Request, user: CurrentUser = Depends(current_user)
):
    store = getattr(request.app.state, "dispatcher_store", None)
    if store is None:
        return JSONResponse({"error": "dispatcher not configured"}, status_code=503)
    cfg = await store.get_config(user.user_id)
    return {"config": cfg}


@router.put("/api/dispatcher/config")
async def update_dispatcher_config(
    request: Request,
    body: DispatcherConfigIn,
    user: CurrentUser = Depends(current_user),
):
    store = getattr(request.app.state, "dispatcher_store", None)
    if store is None:
        return JSONResponse({"error": "dispatcher not configured"}, status_code=503)

    current = await store.get_config(user.user_id)
    updates = body.model_dump(exclude_none=True)
    if not updates:
        return {"config": current}

    merged = {**current, **updates}
    await store.set_config(user.user_id, merged)
    return {"config": merged}
