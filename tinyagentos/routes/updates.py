"""Owner-only software update status and pre-check routes."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, Request

from tinyagentos import updates
from tinyagentos.auth_context import require_admin

router = APIRouter(dependencies=[Depends(require_admin)])


def _cache_dir(request: Request) -> Path:
    return Path(request.app.state.data_dir) / "updates"


@router.get("/api/updates")
async def get_updates(request: Request):
    cfg = updates.UpdateConfig.from_env()
    return await asyncio.to_thread(updates.summary, cfg, _cache_dir(request))


@router.post("/api/updates/check")
async def check_updates(request: Request):
    cfg = updates.UpdateConfig.from_env()
    return await asyncio.to_thread(updates.check, cfg, _cache_dir(request))
