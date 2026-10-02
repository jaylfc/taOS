"""Dispatcher config routes - GET/PUT /api/dispatcher/config.

Session only (request.state.user_id); agent Bearer token -> 403;
no session -> 401. Non-admin: own config only; admin may pass ?user_id=.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from tinyagentos.auth_context import current_user, require_owner_or_admin
from tinyagentos.projects.dispatcher_store import DispatcherConfig, DispatcherStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dispatcher", tags=["dispatcher"])


class DispatcherConfigRequest(BaseModel):
    """Request model for PUT /api/dispatcher/config."""
    enabled: bool = False
    boards: list[str] = Field(default_factory=list)
    eligible_agents: list[str] = Field(default_factory=list)
    max_concurrent_per_agent: int = 1
    poll_seconds: int = 30
    lease_seconds: int = 900

    @field_validator("max_concurrent_per_agent")
    @classmethod
    def validate_cap(cls, v: int) -> int:
        if v != 1:
            raise ValueError(
                "max_concurrent_per_agent must be exactly 1 (one-active-claim rule)"
            )
        return v

    @field_validator("poll_seconds")
    @classmethod
    def validate_poll(cls, v: int) -> int:
        return max(10, min(3600, v))

    @field_validator("lease_seconds")
    @classmethod
    def validate_lease(cls, v: int) -> int:
        return max(60, min(86400, v))


class DispatcherConfigResponse(BaseModel):
    """Response model for GET /api/dispatcher/config."""
    user_id: str
    enabled: bool
    boards: list[str]
    eligible_agents: list[str]
    max_concurrent_per_agent: int
    poll_seconds: int
    lease_seconds: int
    updated_by: str
    updated_at: float


async def _get_dispatcher_store(request: Request) -> DispatcherStore:
    store = getattr(request.app.state, "dispatcher_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Dispatcher store not initialised")
    return store


def _require_session(request: Request) -> str:
    """Extract user_id from session, raise 401 if no session, 403 if agent token."""
    # Check for agent Bearer token first
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        # Agent token - not allowed for config CRUD
        raise HTTPException(status_code=403, detail="Agent tokens cannot modify dispatcher config")

    user = current_user(request)
    if user is None or not user.user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user.user_id


@router.get("/config", response_model=DispatcherConfigResponse)
async def get_dispatcher_config(
    request: Request,
    user_id: Optional[str] = Query(None, description="Target user_id (admin only)"),
    store: DispatcherStore = Depends(_get_dispatcher_store),
):
    """Get dispatcher config for the current user (or target user if admin)."""
    caller_id = _require_session(request)

    # Determine target user
    target_id = caller_id
    if user_id is not None:
        # Non-admin can only read own config
        user = current_user(request)
        if user is None or not user.is_admin:
            raise HTTPException(
                status_code=403,
                detail="Only admins can read other users' dispatcher config",
            )
        target_id = user_id

    cfg = await store.get_config(target_id)
    return DispatcherConfigResponse(**cfg.to_dict())


@router.put("/config", response_model=DispatcherConfigResponse)
async def put_dispatcher_config(
    request: Request,
    body: DispatcherConfigRequest,
    user_id: Optional[str] = Query(None, description="Target user_id (admin only)"),
    store: DispatcherStore = Depends(_get_dispatcher_store),
):
    """Set dispatcher config for the current user (or target user if admin)."""
    caller_id = _require_session(request)
    user = current_user(request)

    # Determine target user
    target_id = caller_id
    if user_id is not None:
        if user is None or not user.is_admin:
            raise HTTPException(
                status_code=403,
                detail="Only admins can modify other users' dispatcher config",
            )
        target_id = user_id

    # Validate boards: each must be an ACTIVE project owned by target user
    if body.boards:
        project_store = getattr(request.app.state, "project_store", None)
        if project_store is None:
            raise HTTPException(status_code=503, detail="Project store not initialised")
        owned_projects = await project_store.list_for_user(target_id, status="active")
        owned_ids = {p["id"] for p in owned_projects}
        for board_id in body.boards:
            if board_id not in owned_ids:
                raise HTTPException(
                    status_code=422,
                    detail=f"Board {board_id!r} is not an active project owned by the target user",
                )

    # Validate eligible_agents: each must exist in agent_registry
    if body.eligible_agents:
        agent_registry = getattr(request.app.state, "agent_registry", None)
        if agent_registry is None:
            raise HTTPException(status_code=503, detail="Agent registry not initialised")
        for canonical_id in body.eligible_agents:
            agent = await agent_registry.get(canonical_id)
            if agent is None:
                raise HTTPException(
                    status_code=422,
                    detail=f"Agent {canonical_id!r} not found in registry",
                )

    # Convert request to config object
    cfg = DispatcherConfig(
        user_id=target_id,
        enabled=body.enabled,
        boards=body.boards,
        eligible_agents=body.eligible_agents,
        max_concurrent_per_agent=body.max_concurrent_per_agent,
        poll_seconds=body.poll_seconds,
        lease_seconds=body.lease_seconds,
        updated_by=caller_id,
    )

    saved = await store.set_config(target_id, cfg, updated_by=caller_id)
    return DispatcherConfigResponse(**saved.to_dict())