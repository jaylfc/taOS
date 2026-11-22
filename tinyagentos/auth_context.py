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


async def resolve_agent_owner(request: Request, agent_id: str, *, exact: bool = False) -> str | None:
    """Return the ``user_id`` that owns *agent_id* per the agent registry, or ``None``.

    *agent_id* may be a configured agent's name, a canonical id
    (``slug-YYYYMMDD-HHMMSS``), a bare slug, or a handle -- the spellings routes
    accept for an agent. ``None`` means
    the registry cannot attribute the agent to anyone (unknown id, registry not
    wired, or not initialised); callers must treat that as NOT owned by the
    caller, never as free-for-all.

    With *exact*, *agent_id* is a canonical id and only the registry row with
    that id counts: no configured name, slug or handle may stand in for it
    (an archive's canonical id can equal another agent's configured name).
    """
    if not agent_id:
        # get_by_handle("") matches every agent registered without a handle,
        # so an empty id would resolve to whichever of them is oldest.
        return None
    # A configured agent's name is its unique config slug (``helper-2``), while
    # the registry slug comes from the display name (``helper``) and another
    # agent may hold ``helper-2``; the canonical id deploy saved is the owner.
    # An entry without one (imported, created without deploy, or deployed with
    # the registry down) has no registry row of its own, so its name could only
    # match someone else's slug: nobody owns it.
    config = getattr(request.app.state, "config", None)
    for agent in [] if exact else getattr(config, "agents", None) or []:
        if agent.get("name") == agent_id:
            agent_id = agent.get("registry_canonical_id") or ""
            if not agent_id:
                return None
            # Only that row owns it: with the row gone, a slug or handle
            # spelled like the saved id is someone else's.
            exact = True
            break
    registry = getattr(request.app.state, "agent_registry", None)
    if registry is None:
        return None
    try:
        record = await registry.get(agent_id)
        if record is None and not exact:
            record = await registry.get_by_slug(agent_id)
        if record is None and not exact:
            record = await registry.get_by_handle(agent_id)
    except RuntimeError:  # store not initialised
        return None
    if record is None:
        return None
    return record.get("user_id") or None


async def require_agent_owner_or_admin(
    request: Request, user: CurrentUser, agent_id: str, *, exact: bool = False
) -> None:
    """403 unless *user* is an admin or the registry says it owns *agent_id*.

    Fail-closed for non-admins: an agent the registry cannot attribute (unknown
    id, or a config-only agent with no registry row) is nobody's to expose, so
    only an admin may act on it.
    """
    if user.is_admin:
        return
    owner = await resolve_agent_owner(request, agent_id, exact=exact)
    if owner is None:
        raise HTTPException(status_code=403, detail="forbidden")
    require_owner_or_admin(user, owner)


def agent_bound_name(request: Request) -> str | None:
    """The agent a per-agent local token is bound to, or None."""
    return getattr(request.state, "agent_name", None) or None


def is_host_caller(request: Request) -> bool:
    """True for the unbound host local token (no agent binding)."""
    return (
        getattr(request.state, "via", None) == "local_token"
        and not agent_bound_name(request)
    )


def is_legacy_owner(request: Request) -> bool:
    """True for a session on a legacy single-password install.

    Such an install has no user records, so its session carries an empty
    ``user_id`` and ``is_admin`` False; the one person who knows the password is
    the install owner. Only honoured while the store really has no users, so a
    stale empty-id session can never act as an owner once accounts exist.
    """
    if getattr(request.state, "via", None) != "session":
        return False
    if getattr(request.state, "user_id", None) != "":
        return False
    auth = getattr(request.app.state, "auth", None)
    try:
        return auth is not None and auth.get_primary_user() is None
    except Exception:
        return False


def is_human_admin(request: Request) -> bool:
    """True for an admin session or the unbound host token, never an agent token.

    A per-agent local token maps to the admin user, so ``is_admin`` alone cannot
    tell a person from an agent holding its own credential.
    """
    if agent_bound_name(request):
        return False
    return (
        bool(getattr(request.state, "is_admin", False))
        or is_host_caller(request)
        or is_legacy_owner(request)
    )


async def require_agent_access(
    request: Request, agent_id: str | None, *, allow_bound: bool = True, exact: bool = False
) -> None:
    """403 unless the caller may act on *agent_id*.

    - agent-bound local token: only its own agent (and only when *allow_bound*);
      it maps to the admin user, so an owner check alone would let agent A touch
      agent B;
    - the unbound host token: allowed;
    - a session: the agent's registry owner or an admin, via
      ``require_agent_owner_or_admin`` on the stripped identifier.

    Call it BEFORE any lookup, write or directory creation for the agent.
    """
    name = (agent_id or "").strip()
    bound = agent_bound_name(request)
    if bound:
        if not allow_bound or name != bound:
            raise HTTPException(status_code=403, detail="forbidden")
        return
    if is_host_caller(request) or is_legacy_owner(request):
        return
    await require_agent_owner_or_admin(request, current_user(request), name, exact=exact)


async def can_access_agent(request: Request, agent_id: str | None) -> bool:
    """Boolean form of :func:`require_agent_access`, for filtering lists."""
    try:
        await require_agent_access(request, agent_id)
    except HTTPException:
        return False
    return True
