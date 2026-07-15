from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from tinyagentos.council.member_store import MemberStore
from tinyagentos.council.role_registry import RoleRegistry

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/council/roles")
async def api_list_roles(request: Request):
    registry = request.app.state.council_roles
    roles = await registry.list_roles()
    return roles


@router.get("/api/council/members")
async def api_list_members(request: Request):
    store = request.app.state.council_members
    members = await store.list_members()
    return members
