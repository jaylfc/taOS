from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request


@dataclass(frozen=True)
class CurrentUser:
    user_id: str
    is_admin: bool


def current_user(request: Request) -> CurrentUser:
    """FastAPI dependency. 401 if no authenticated user on request.state."""
    uid = getattr(request.state, "user_id", None)
    if not uid:
        raise HTTPException(status_code=401, detail="authentication required")
    return CurrentUser(
        user_id=uid,
        is_admin=bool(getattr(request.state, "is_admin", False)),
    )


def require_owner_or_admin(user: CurrentUser, resource_user_id: str) -> None:
    """403 unless the caller owns the resource or is an admin."""
    if user.is_admin or user.user_id == resource_user_id:
        return
    raise HTTPException(status_code=403, detail="forbidden")


def require_admin(request: Request) -> None:
    """FastAPI dependency: 403 unless the caller is an admin or holds the host local token.

    For routers that operate on SYSTEM-GLOBAL state (the secrets keystore, the
    controller process, LLM provider config, MCP integrations): there is no
    owner to scope to, so a plain non-admin member session must never reach
    the handler. ``AuthMiddleware`` (tinyagentos/auth_middleware.py) stamps both
    accepted signals on ``request.state``:

    - ``is_admin`` -- True for an admin session AND for the host local token
      when it maps to the primary user (deployed agents and ``taosctl`` present
      that token, so they keep working unchanged);
    - ``via == "local_token"`` -- set for a valid local token even in the
      pre-onboarding edge case where there is no primary user yet, so
      ``is_admin`` is False there. Checking both keeps agent tool-calls
      working in every state the middleware allows without ever accepting a
      bare non-admin user session.

    Single-user installs are unaffected: the sole user created at setup IS the
    admin. Use as ``APIRouter(dependencies=[Depends(require_admin)])`` for a
    router that is admin-only end to end, or ``Depends(require_admin)`` on the
    individual handlers of a router that mixes gated writes with open reads.
    Mirrors ``routes/settings.py::_require_admin_or_local_token`` and
    ``routes/skill_exec.py::_is_admin_or_local_token``.
    """
    if getattr(request.state, "is_admin", False):
        return
    if getattr(request.state, "via", None) == "local_token":
        return
    raise HTTPException(status_code=403, detail="forbidden")


async def resolve_agent_owner(request: Request, agent_id: str) -> str | None:
    """Return the ``user_id`` that owns *agent_id* per the agent registry, or ``None``.

    *agent_id* may be a canonical id (``slug-YYYYMMDD-HHMMSS``), a bare slug, or
    a handle -- the three spellings routes accept for an agent. ``None`` means
    the registry cannot attribute the agent to anyone (unknown id, registry not
    wired, or not initialised); callers must treat that as NOT owned by the
    caller, never as free-for-all.
    """
    registry = getattr(request.app.state, "agent_registry", None)
    if registry is None:
        return None
    try:
        record = await registry.get(agent_id)
        if record is None:
            record = await registry.get_by_slug(agent_id)
        if record is None:
            record = await registry.get_by_handle(agent_id)
    except RuntimeError:  # store not initialised
        return None
    if record is None:
        return None
    return record.get("user_id") or None


async def require_agent_owner_or_admin(
    request: Request, user: CurrentUser, agent_id: str
) -> None:
    """403 unless *user* is an admin or the registry says it owns *agent_id*.

    Fail-closed for non-admins: an agent the registry cannot attribute (unknown
    id, or a config-only agent with no registry row) is nobody's to expose, so
    only an admin may act on it.
    """
    if user.is_admin:
        return
    owner = await resolve_agent_owner(request, agent_id)
    if owner is None:
        raise HTTPException(status_code=403, detail="forbidden")
    require_owner_or_admin(user, owner)
