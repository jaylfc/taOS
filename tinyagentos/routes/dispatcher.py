"""Dispatcher config routes - GET/PUT /api/dispatcher/config.

Session (or the host local token) only: an agent Bearer token is not a
credential here; no session at all -> 401. Non-admin: own config only (naming
your own id in ?user_id= is the same thing); admin may pass ?user_id= of an
existing user.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from tinyagentos.auth_context import current_user
from tinyagentos.projects.dispatcher_store import DispatcherConfig, DispatcherStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dispatcher", tags=["dispatcher"])

# Cap on both id lists. 200 is far above any real deployment (a board list of
# active projects, a dispatcher fleet), and it bounds the per-id registry
# lookups a single PUT can trigger.
MAX_IDS = 200


class DispatcherConfigRequest(BaseModel):
    """Request model for PUT /api/dispatcher/config.

    strict=True + extra="forbid" because this is stored, not just echoed: a
    misspelled key ({"enabeld": true}) or a coerced string ("enabled": "yes")
    used to be a silent 200 that stored something the caller did not write.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    enabled: bool = False
    boards: list[str] = Field(default_factory=list, max_length=MAX_IDS)
    eligible_agents: list[str] = Field(default_factory=list, max_length=MAX_IDS)
    max_concurrent_per_agent: int = 1
    poll_seconds: int = 30
    lease_seconds: int = 900

    @field_validator("boards", "eligible_agents", mode="before")
    @classmethod
    def dedupe_ids(cls, v):
        """Drop repeats, keeping first-seen order.

        Runs BEFORE max_length so a payload that repeats one id thousands of
        times collapses to a single entry instead of forcing one registry
        lookup (and one stored list element) per copy.
        """
        if not isinstance(v, list):
            return v
        seen: set[str] = set()
        out: list = []
        for item in v:
            if isinstance(item, str):
                if item in seen:
                    continue
                seen.add(item)
            out.append(item)
        return out

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
    # null until the config has actually been stored once.
    updated_at: Optional[float] = None


async def _get_dispatcher_store(request: Request) -> DispatcherStore:
    store = getattr(request.app.state, "dispatcher_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Dispatcher store not initialised")
    return store


def _require_session(request: Request) -> str:
    """Return the caller_id of a session-authenticated caller.

    /api/dispatcher/config is deliberately NOT on ``_AGENT_TOKEN_PATHS``: that
    allowlist's contract is "the route verifies the JWT + a scope grant", and
    config CRUD verifies neither -- it is refused for agents. So the middleware
    no longer lets a Bearer alone through the auth gate here, which is what lets
    a signed-in browser carrying a stale Bearer header through as a session
    (previously any "Bearer " prefix was answered with 403, so a garbage header
    locked a logged-in user out). A request that still arrives tagged as an
    unverified registry JWT is refused with 403 rather than served as anonymous;
    everything else with no session is a 401.
    """
    if getattr(request.state, "via", None) == "registry_jwt_candidate":
        raise HTTPException(
            status_code=403,
            detail="Agent tokens cannot read or modify dispatcher config",
        )

    user = current_user(request)
    if user is None or not user.user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user.user_id


def _resolve_target(request: Request, caller_id: str, user_id: Optional[str]) -> str:
    """Resolve the config owner: the caller, or ?user_id= for an admin.

    A non-admin naming their OWN id is not a cross-user request, so it is
    allowed. Any other ?user_id= needs admin, and an admin may only target a
    user that exists -- writing a row for a made-up id would orphan a config no
    session can ever read.
    """
    if user_id is None or user_id == caller_id:
        return caller_id

    user = current_user(request)
    if user is None or not user.is_admin:
        raise HTTPException(
            status_code=403,
            detail="Only admins can manage other users' dispatcher config",
        )

    auth_mgr = getattr(request.app.state, "auth", None)
    if auth_mgr is None:
        raise HTTPException(status_code=503, detail="Auth store not initialised")
    if auth_mgr.get_user_by_id(user_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown user {user_id!r}")
    return user_id


@router.get("/config", response_model=DispatcherConfigResponse)
async def get_dispatcher_config(
    request: Request,
    user_id: Optional[str] = Query(None, description="Target user_id (admin only)"),
    store: DispatcherStore = Depends(_get_dispatcher_store),
):
    """Get dispatcher config for the current user (or target user if admin)."""
    caller_id = _require_session(request)
    target_id = _resolve_target(request, caller_id, user_id)

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
    target_id = _resolve_target(request, caller_id, user_id)

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

    # Validate eligible_agents: each must be an ACTIVE agent OWNED BY the target
    # user. Existence alone is not enough -- storing another user's agent (or a
    # suspended/retired one) hands this user's dispatchable tasks to it.
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
            if (agent.get("user_id") or "") != target_id:
                raise HTTPException(
                    status_code=422,
                    detail=f"Agent {canonical_id!r} is not owned by the target user",
                )
            if agent.get("status") != "active":
                raise HTTPException(
                    status_code=422,
                    detail=f"Agent {canonical_id!r} is not active (status {agent.get('status')!r})",
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