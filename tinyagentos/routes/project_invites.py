from __future__ import annotations

import html
import json
import logging
import socket

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator

from tinyagentos.agent_registry_store import _slugify
from tinyagentos.auth_context import CurrentUser, current_user, require_owner_or_admin
from tinyagentos.auth_middleware import rate_limit_ok
from tinyagentos.projects.invite_store import (
    InviteAlreadyRedeemedError,
    InviteExpiredError,
    InvitePinError,
    InvitePendingCapError,
    InviteRevokedError,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class MintInviteIn(BaseModel):
    scopes: list[str] = Field(default_factory=list)
    approval_mode: str = Field(default="auto", pattern="^(auto|manual)$")
    check_interval_secs: int = Field(default=1800, ge=60)


class MintOsInviteIn(BaseModel):
    """Mint an OS-level (project-less) invite. The redeemed agent becomes
    chat-available immediately with a global (non-project) grant; projects are
    assigned later. ``display_name`` is the human alias applied to the minted
    identity on redeem."""

    scopes: list[str] = Field(default_factory=list)
    approval_mode: str = Field(default="auto", pattern="^(auto|manual)$")
    check_interval_secs: int = Field(default=1800, ge=60)
    display_name: str | None = Field(default=None, max_length=64)

    @field_validator("display_name")
    @classmethod
    def _clean_display_name(cls, v: str | None) -> str | None:
        # Stored verbatim and echoed back, so trim and reject control chars;
        # an all-whitespace alias collapses to None (no alias).
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        if any(ord(c) < 0x20 for c in v):
            raise ValueError("display_name must not contain control characters")
        return v


class RedeemInviteIn(BaseModel):
    invite_id: str
    pin: str
    harness: str
    label: str | None = None


# ---------------------------------------------------------------------------
# Handle derivation (design section 2a)
# ---------------------------------------------------------------------------
#
# The controller derives the human-facing handle as
# {project_slug}-{harness}[-{label}], slugified. Two live agents may not share
# a bus handle, so before minting we check the registry for an ACTIVE agent
# already holding the target handle in this project and append -2, -3, ...
# until free. The label is the natural first disambiguator, so the numeric
# suffix is the fallback for same-harness/same-label re-invites.

_CANCEL_SCOPES = {"canvas_read", "canvas_write"}

# Scopes that only mean anything bound to a specific project. An OS-level
# (project-less) invite has no project to bind them to, so they are stripped
# before minting rather than granted verbatim: an OS invite must never hand out
# project-scoped authority that resolves to no project.
_PROJECT_SCOPED = {"project_tasks", "canvas_read", "canvas_write"}


def _derive_handle(project_slug: str, harness: str, label: str | None) -> str:
    """Build the base handle {project_slug}-{harness}[-{label}], slugified.

    Each component is slugified independently so an awkward label cannot bleed
    separators into a neighbour; the parts are then joined with single dashes.
    """
    slug = _slugify(project_slug)
    hw = _slugify(harness)
    parts = [slug, hw]
    if label:
        lbl = _slugify(label)
        if lbl:
            parts.append(lbl)
    return "-".join(p for p in parts if p)


def _derive_os_handle(display_name: str | None, harness: str, label: str | None) -> str:
    """Build the base handle for an OS-level (project-less) redeem.

    There is no project slug to namespace on, so the alias (display_name) is the
    primary name, falling back to the harness. An optional label disambiguates,
    as in the project path. Each component is slugified independently.

    ``_slugify`` returns the sentinel ``"agent"`` for empty input, so only
    slugify a non-empty alias; otherwise the harness fallback never fires."""
    alias = (display_name or "").strip()
    base = _slugify(alias) if alias else _slugify(harness)
    parts = [base]
    if label:
        lbl = _slugify(label)
        if lbl:
            parts.append(lbl)
    return "-".join(p for p in parts if p) or _slugify(harness)


async def _dedupe_handle(request: Request, base_handle: str) -> str:
    """Return ``base_handle`` unless an ACTIVE agent already holds it in this
    project, in which case append -2, -3, ... until a free handle is found.

    The registry active-handle check is exactly the one the consent minting
    path uses (``registry.get_by_handle(..., status="active")``), so a re-invite
    collides deterministically with the live agent rather than minting a
    duplicate bus address.
    """
    registry = getattr(request.app.state, "agent_registry", None)
    if registry is None:
        return base_handle
    candidate = base_handle
    n = 2
    while await registry.get_by_handle(candidate, status="active") is not None:
        candidate = f"{base_handle}-{n}"
        n += 1
    return candidate


# ---------------------------------------------------------------------------
# Connection bundle (design section 4 + addendum)
# ---------------------------------------------------------------------------

# Port the controller binds. The bundle endpoints reuse this for every
# enumerated LAN / mesh address.
_CONTROLLER_PORT = 6969


def _enumerate_lan_ips() -> list[str]:
    """Return the controller host's non-loopback IPv4 addresses.

    Mirrors the UDP getsockname trick used by worker/agent.py for the primary
    interface, plus a full interface enumeration (``socket`` + ``fcntl``/
    ``subprocess ip addr``) for multi-homed hosts. Loopback (127.0.0.0/8) and
    link-local (169.254.0.0/16) addresses are excluded; the controller binds
    **

    Fail-soft: if enumeration raises, returns an empty list and the caller
    falls back to the operator override / mesh endpoint.
    """
    ips: list[str] = []
    seen: set[str] = set()

    def _add(ip: str) -> None:
        if not ip:
            return
        try:
            addr = socket.inet_aton(ip)
        except OSError:
            return
        # Exclude loopback and link-local.
        if ip.startswith("127.") or ip.startswith("169.254."):
            return
        if ip in seen:
            return
        seen.add(ip)
        ips.append(ip)

    # Primary interface via UDP getsockname (no packets sent).
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            _add(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass

    # Full interface enumeration (Linux/macOS `ip addr`/`ifconfig` style via
    # the stdlib getaddrinfo on each adapter is not portable, so shell out to
    # `ip -4 addr` when present, else best-effort).
    try:
        import subprocess

        out = subprocess.run(
            ["ip", "-4", "addr", "show"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode == 0:
            import re

            for m in re.finditer(r"inet\s+(\d+\.\d+\.\d+\.\d+)/\d+", out.stdout):
                _add(m.group(1))
    except Exception:  # noqa: BLE001 - enumeration is best-effort
        pass

    return ips


async def _build_controller_dict(request: Request) -> dict:
    """Enumerate the controller's reachable endpoints (operator override, LAN,
    mesh; no relay in Phase 1) and wrap them in the controller descriptor shared
    by the project and OS-level bundles."""
    endpoints: list[dict] = []
    priority = 1

    # Operator override (TAOS_CONTROLLER_CALLBACK_HOST) becomes priority 1.
    override = None
    try:
        from tinyagentos.routes.agent_deploy import controller_callback_host

        override = await controller_callback_host(request)
    except Exception:  # noqa: BLE001
        override = None
    if override:
        endpoints.append(
            {"kind": "lan", "url": f"http://{override}:{_CONTROLLER_PORT}", "priority": priority}
        )
        priority += 1

    for ip in _enumerate_lan_ips():
        if override and ip == override:
            continue
        endpoints.append(
            {"kind": "lan", "url": f"http://{ip}:{_CONTROLLER_PORT}", "priority": priority}
        )
        priority += 1

    # Mesh endpoint from tailscale mesh status, when joined.
    try:
        from tinyagentos.taosnet.mesh import mesh_status

        status = await mesh_status()
        if status.get("joined") and status.get("node_ip"):
            endpoints.append(
                {
                    "kind": "mesh",
                    "url": f"http://{status['node_ip']}:{_CONTROLLER_PORT}",
                    "priority": priority,
                }
            )
            priority += 1
    except Exception:  # noqa: BLE001 - mesh is optional
        pass

    return {
        "endpoints": endpoints,
        "health_path": "/api/health",
        "registry_pubkey_path": "/api/agents/registry/pubkey",
    }


async def build_os_connection_bundle(
    request: Request,
    *,
    invite_record: dict,
    agent_handle: str,
    granted_scopes: list[str],
    check_interval_secs: int,
) -> dict:
    """Assemble the connection bundle for an OS-level (project-less) redeem.

    Same security model as the project bundle (NO token or secret; the token
    arrives via the status poll), but the agent joins taOS chat globally rather
    than a project: the advertised API surface is the a2a bus only, with no
    project/task/canvas routes. ``project`` is null in the returned bundle.
    """
    controller = await _build_controller_dict(request)

    scopeset = set(granted_scopes)
    apis: dict = {}
    if "a2a_send" in scopeset or "a2a_receive" in scopeset:
        apis["a2a_bus_send"] = "/api/a2a/bus/send"
        apis["a2a_bus_messages"] = "/api/a2a/bus/messages"
        apis["a2a_bus_channels"] = "/api/a2a/bus/channels"

    delivery = {
        "stream_path": "/api/a2a/bus/stream?channel={channel}&since={cursor}",
        "poll_path": "/api/a2a/bus/messages?channel={channel}&since={cursor}",
        "check_interval_secs": check_interval_secs,
        "cursor": "ts",
        "filter": "mentions",
    }

    onboarding = {
        "links": [
            {"label": "taOS repository", "url": "https://github.com/jaylfc/taOS"},
            {
                "label": "Agent manual (04-apps.md)",
                "url": "https://github.com/jaylfc/taOS/blob/main/docs/agent-manual/04-apps.md",
            },
            {
                "label": "Agent manual (09-os-control.md)",
                "url": "https://github.com/jaylfc/taOS/blob/main/docs/agent-manual/09-os-control.md",
            },
        ],
        "check_interval_secs": check_interval_secs,
    }

    guide = _build_os_guide_markdown(
        agent_handle=agent_handle,
        granted_scopes=granted_scopes,
        check_interval_secs=check_interval_secs,
    )

    return {
        "version": 1,
        "invite_id": invite_record["invite_id"],
        "project": None,
        "controller": controller,
        "auth": {
            "flow": "auth_request",
            "agent_handle": agent_handle,
            "granted_scopes": sorted(granted_scopes),
        },
        "apis": apis,
        "delivery": delivery,
        "onboarding": onboarding,
        "guide_markdown": guide,
    }


async def build_connection_bundle(
    request: Request,
    *,
    invite_record: dict,
    project: dict,
    agent_handle: str,
    granted_scopes: list[str],
    check_interval_secs: int,
) -> dict:
    """Assemble the JSON connection bundle returned by a successful redeem.

    The bundle carries NO token or secret (the token arrives via the status
    poll). It enumerates the controller's reachable endpoints (LAN, mesh; no
    relay in Phase 1), the agent-JWT-reachable API surface scoped EXACTLY to
    the granted scopes (mirroring auth_middleware's canvas allowlist), the
    timed-check delivery contract, and an onboarding kit + guide_markdown.

    See design section 4 and the Approved-build addendum.
    """
    pid = project["id"]
    project_slug = project.get("slug") or pid

    controller = await _build_controller_dict(request)

    # --- agent_handle scoped api surface ----------------------------------
    apis: dict = {}
    scopeset = set(granted_scopes)
    has_tasks = "project_tasks" in scopeset
    has_canvas_read = "canvas_read" in scopeset
    has_canvas_write = "canvas_write" in scopeset

    if has_tasks:
        apis["tasks_list"] = f"/api/projects/{pid}/tasks"
        apis["tasks_ready"] = f"/api/projects/{pid}/tasks/ready"
        apis["task_lifecycle"] = f"/api/projects/{pid}/tasks/{{task_id}}/(claim|release|close|reopen)"
        apis["task_comments"] = f"/api/projects/{pid}/tasks/{{task_id}}/comments"
    if has_canvas_read:
        apis["canvas_elements"] = f"/api/projects/{pid}/canvas/elements"
        apis["canvas_snapshot"] = f"/api/projects/{pid}/canvas/snapshot.png"
    if has_canvas_write:
        apis["canvas_element"] = f"/api/projects/{pid}/canvas/elements/{{eid}}"

    # A2A bus routes are always advertised (a2a_send / a2a_receive are part of
    # the invite's default scope set, but include them whenever either scope is
    # present so the advertised surface matches what the token can call).
    if "a2a_send" in scopeset or "a2a_receive" in scopeset:
        apis["a2a_bus_send"] = "/api/a2a/bus/send"
        apis["a2a_bus_messages"] = "/api/a2a/bus/messages"
        apis["a2a_bus_channels"] = "/api/a2a/bus/channels"

    delivery = {
        "stream_path": f"/api/a2a/bus/stream?channel={{channel}}&since={{cursor}}",
        "poll_path": f"/api/a2a/bus/messages?channel={{channel}}&since={{cursor}}",
        "check_interval_secs": check_interval_secs,
        "cursor": "ts",
        "filter": "mentions+project",
    }

    # --- onboarding kit ----------------------------------------------------
    onboarding = {
        "links": [
            {"label": "taOS repository", "url": "https://github.com/jaylfc/taOS"},
            {
                "label": "Agent manual (04-apps.md)",
                "url": "https://github.com/jaylfc/taOS/blob/main/docs/agent-manual/04-apps.md",
            },
            {
                "label": "Agent manual (09-os-control.md)",
                "url": "https://github.com/jaylfc/taOS/blob/main/docs/agent-manual/09-os-control.md",
            },
        ],
        "check_interval_secs": check_interval_secs,
    }

    guide = _build_guide_markdown(
        project=project,
        project_slug=project_slug,
        agent_handle=agent_handle,
        granted_scopes=granted_scopes,
        has_tasks=has_tasks,
        has_canvas_read=has_canvas_read,
        has_canvas_write=has_canvas_write,
        check_interval_secs=check_interval_secs,
    )

    return {
        "version": 1,
        "invite_id": invite_record["invite_id"],
        "project": {
            "id": project["id"],
            "name": project.get("name", ""),
            "slug": project_slug,
        },
        "controller": controller,
        "auth": {
            "flow": "auth_request",
            "agent_handle": agent_handle,
            "granted_scopes": sorted(granted_scopes),
        },
        "apis": apis,
        "delivery": delivery,
        "onboarding": onboarding,
        "guide_markdown": guide,
    }


def _build_guide_markdown(
    *,
    project: dict,
    project_slug: str,
    agent_handle: str,
    granted_scopes: list[str],
    has_tasks: bool,
    has_canvas_read: bool,
    has_canvas_write: bool,
    check_interval_secs: int,
) -> str:
    """Generate the personalized capability guide from granted scopes + project
    + derived handle. Contains NO secret: the token still arrives via the status
    poll. The agent that only reads the guide gets an accurate capability map."""
    scopeset = set(granted_scopes)
    lines: list[str] = []
    lines.append(f"# Joining {project.get('name', project_slug)} on taOS as `{agent_handle}`")
    lines.append("")
    lines.append(
        "You were invited to a taOS project. Redeeming your invite registered you as "
        "an external agent and minted an agent-identity token (delivered via your "
        "status poll, not this guide). This document is your personalized capability map."
    )
    lines.append("")
    lines.append("## Links")
    lines.append("")
    lines.append("- taOS repository: https://github.com/jaylfc/taOS")
    lines.append("- Agent manual - apps: https://github.com/jaylfc/taOS/blob/main/docs/agent-manual/04-apps.md")
    lines.append("- Agent manual - OS control: https://github.com/jaylfc/taOS/blob/main/docs/agent-manual/09-os-control.md")
    lines.append("")
    lines.append("## Your capabilities in the Projects app")
    lines.append("")
    if has_tasks:
        lines.append(
            "- Tasks (kanban): list ready tasks, read a task, claim/release/close/reopen it, "
            "and read/post comments. You are a member of this project."
        )
    else:
        lines.append(
            "- Tasks (kanban): NOT granted. You cannot read or act on project tasks."
        )
    lines.append("")
    lines.append("## Your capabilities on the Canvas")
    lines.append("")
    if has_canvas_read:
        lines.append(
            "- Canvas read: read canvas elements (notes, links, text, mermaid, flowchart) "
            "and the canvas snapshot for this project."
        )
    else:
        lines.append("- Canvas read: NOT granted.")
    if has_canvas_write:
        lines.append(
            "- Canvas write: create canvas elements and edit/delete the ones you own."
        )
    else:
        lines.append("- Canvas write: NOT granted.")
    if not has_canvas_read and not has_canvas_write:
        lines.append("- You have no canvas access in this project.")
    lines.append("")
    lines.append("## A2A bus contract (authenticated proxy)")
    lines.append("")
    if "a2a_send" in scopeset or "a2a_receive" in scopeset:
        lines.append(
            "Reach other agents and the coordinator through the authenticated proxy "
            "`/api/a2a/bus/*` (never the raw :7900 bus). The proxy forces `from` to your "
            "own handle, so you always post as yourself. Send with "
            "`POST /api/a2a/bus/send` using the body `{thread, body}` (and optional "
            "`reply_to`); `channel` is ignored, address threads by name. Read via "
            "`GET /api/a2a/bus/messages?channel={channel}&since={cursor}`."
        )
    else:
        lines.append("- A2A bus: NOT granted.")
    lines.append("")
    lines.append("## WRITE THIS INTO YOUR OWN PERSISTENT MEMORY NOW")
    lines.append("")
    lines.append(
        "Persist the following into your own long-term memory (not the operator's): "
        f"your canonical_id (from the status poll), the project id `{project.get('id')}` "
        f"and slug `{project_slug}`, the path to the token file where you stored the "
        "polled token, and this bus contract (authenticated proxy, `from` forced to "
        f"`{agent_handle}`). A re-spawn must reuse this identity, not mint a new one."
    )
    lines.append("")
    lines.append("## CHECK ON A TIMER (the loop that makes you a member)")
    lines.append("")
    lines.append(
        f"Poll every {check_interval_secs} seconds (or hold the SSE stream at "
        "`/api/a2a/bus/stream`) for your ready tasks and any @mentions addressed to "
        f"`{agent_handle}`. On each check: read ready tasks, act on them, comment "
        "progress, post on the bus, and report status. This timed check is the "
        "reliable delivery floor; the stream is an optional optimization only if your "
        "harness can hold a connection and wake on a message."
    )
    lines.append("")
    return "\n".join(lines)


def _build_os_guide_markdown(
    *,
    agent_handle: str,
    granted_scopes: list[str],
    check_interval_secs: int,
) -> str:
    """Generate the capability guide for an OS-level (project-less) redeem.

    Contains NO secret (the token arrives via the status poll). The agent is
    chat-available across taOS but is a member of no project yet; projects are
    assigned later via the Assign-to-project flow."""
    scopeset = set(granted_scopes)
    lines: list[str] = []
    lines.append(f"# Joining taOS in chat as `{agent_handle}`")
    lines.append("")
    lines.append(
        "You were invited to taOS as an external agent. Redeeming your invite "
        "registered you and minted an agent-identity token (delivered via your "
        "status poll, not this guide). You are available in chat now; you are not "
        "a member of any project yet. The operator can assign you to projects "
        "later, which grants project-scoped capabilities at that time."
    )
    lines.append("")
    lines.append("## Links")
    lines.append("")
    lines.append("- taOS repository: https://github.com/jaylfc/taOS")
    lines.append("- Agent manual - apps: https://github.com/jaylfc/taOS/blob/main/docs/agent-manual/04-apps.md")
    lines.append("- Agent manual - OS control: https://github.com/jaylfc/taOS/blob/main/docs/agent-manual/09-os-control.md")
    lines.append("")
    lines.append("## A2A bus contract (authenticated proxy)")
    lines.append("")
    if "a2a_send" in scopeset or "a2a_receive" in scopeset:
        lines.append(
            "Reach other agents and the coordinator through the authenticated proxy "
            "`/api/a2a/bus/*` (never the raw :7900 bus). The proxy forces `from` to your "
            "own handle, so you always post as yourself. Send with "
            "`POST /api/a2a/bus/send` using the body `{thread, body}` (and optional "
            "`reply_to`); `channel` is ignored, address threads by name. Read via "
            "`GET /api/a2a/bus/messages?channel={channel}&since={cursor}`."
        )
    else:
        lines.append("- A2A bus: NOT granted.")
    lines.append("")
    lines.append("## WRITE THIS INTO YOUR OWN PERSISTENT MEMORY NOW")
    lines.append("")
    lines.append(
        "Persist the following into your own long-term memory (not the operator's): "
        "your canonical_id (from the status poll), the path to the token file where "
        "you stored the polled token, and this bus contract (authenticated proxy, "
        f"`from` forced to `{agent_handle}`). A re-spawn must reuse this identity, "
        "not mint a new one."
    )
    lines.append("")
    lines.append("## CHECK ON A TIMER")
    lines.append("")
    lines.append(
        f"Poll every {check_interval_secs} seconds (or hold the SSE stream at "
        "`/api/a2a/bus/stream`) for any @mentions addressed to "
        f"`{agent_handle}`. On each check: read your mentions, act on them, and "
        "report status. This timed check is the reliable delivery floor; the "
        "stream is an optional optimization only if your harness can hold a "
        "connection and wake on a message."
    )
    lines.append("")
    return "\n".join(lines)


@router.post("/api/projects/{project_id}/invites")
async def mint_invite(
    project_id: str,
    payload: MintInviteIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    store = request.app.state.project_invites
    project_store = request.app.state.project_store
    project = await project_store.get_project(project_id)
    if project is None:
        return JSONResponse({"error": "project not found"}, status_code=404)
    require_owner_or_admin(user, project["user_id"])
    try:
        result = await store.mint(
            project_id=project_id,
            scopes=list(payload.scopes),
            approval_mode=payload.approval_mode,
            check_interval_secs=payload.check_interval_secs,
            created_by=user.user_id,
        )
    except InvitePendingCapError as exc:
        return JSONResponse({"error": str(exc)}, status_code=429)
    record = result["record"]
    return {
        "invite_id": record["invite_id"],
        "pin": result["pin"],
        "expires_ts": record["expires_ts"],
        "scopes": record["scopes"],
        "approval_mode": record["approval_mode"],
        "check_interval_secs": record["check_interval_secs"],
    }


@router.get("/api/projects/{project_id}/invites")
async def list_invites(
    project_id: str,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    store = request.app.state.project_invites
    project_store = request.app.state.project_store
    project = await project_store.get_project(project_id)
    if project is None:
        return JSONResponse({"error": "project not found"}, status_code=404)
    require_owner_or_admin(user, project["user_id"])
    items = await store.list_for_project(project_id)
    return [
        {
            "invite_id": i["invite_id"],
            "scopes": i["scopes"],
            "status": i["status"],
            "expires_ts": i["expires_ts"],
            "redeemed_by": i.get("redeemed_by"),
        }
        for i in items
    ]


@router.delete("/api/projects/{project_id}/invites/{invite_id}")
async def revoke_invite(
    project_id: str,
    invite_id: str,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    store = request.app.state.project_invites
    project_store = request.app.state.project_store
    project = await project_store.get_project(project_id)
    if project is None:
        return JSONResponse({"error": "project not found"}, status_code=404)
    require_owner_or_admin(user, project["user_id"])
    row = await store.get(invite_id)
    if row is None or row.get("project_id") != project_id:
        return JSONResponse({"error": "invite not found"}, status_code=404)
    ok = await store.revoke(invite_id)
    if not ok:
        return JSONResponse({"error": "invite not found or already redeemed"}, status_code=404)
    return JSONResponse(content=None, status_code=204)


# ---------------------------------------------------------------------------
# OS-level invites (project-less; admin-gated)
# ---------------------------------------------------------------------------
#
# An OS-level invite mints the SAME redeemable link (URL + PIN) as a project
# invite, but is not tied to a project. On redeem the agent gets a global
# (chat) grant and NO project grant, so it is available in chat immediately and
# projects are assigned later. There is no per-resource owner to check against,
# so these routes are admin-only (matching mint-internal / seed-internal).


def _require_admin(user: CurrentUser) -> None:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="forbidden")


@router.post("/api/agents/invites")
async def mint_os_invite(
    payload: MintOsInviteIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    _require_admin(user)
    store = request.app.state.project_invites
    # OS-level invites carry no project, so drop any project-bound scopes rather
    # than granting them against a non-existent project (kilo review #1918).
    os_scopes = [s for s in payload.scopes if s not in _PROJECT_SCOPED]
    try:
        result = await store.mint(
            project_id=None,
            scopes=os_scopes,
            approval_mode=payload.approval_mode,
            check_interval_secs=payload.check_interval_secs,
            created_by=user.user_id,
            display_name=payload.display_name,
        )
    except InvitePendingCapError as exc:
        return JSONResponse({"error": str(exc)}, status_code=429)
    record = result["record"]
    return {
        "invite_id": record["invite_id"],
        "pin": result["pin"],
        "expires_ts": record["expires_ts"],
        "scopes": record["scopes"],
        "approval_mode": record["approval_mode"],
        "check_interval_secs": record["check_interval_secs"],
        "display_name": record["display_name"],
    }


@router.get("/api/agents/invites")
async def list_os_invites(
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    _require_admin(user)
    store = request.app.state.project_invites
    items = await store.list_os_level()
    return [
        {
            "invite_id": i["invite_id"],
            "scopes": i["scopes"],
            "status": i["status"],
            "expires_ts": i["expires_ts"],
            "redeemed_by": i.get("redeemed_by"),
            "display_name": i.get("display_name"),
        }
        for i in items
    ]


@router.delete("/api/agents/invites/{invite_id}")
async def revoke_os_invite(
    invite_id: str,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    _require_admin(user)
    store = request.app.state.project_invites
    row = await store.get(invite_id)
    # Only OS-level (project-less) invites are revocable through this route;
    # project invites keep their per-project revoke endpoint.
    if row is None or row.get("project_id") is not None:
        return JSONResponse({"error": "invite not found"}, status_code=404)
    ok = await store.revoke(invite_id)
    if not ok:
        return JSONResponse({"error": "invite not found or already redeemed"}, status_code=404)
    return JSONResponse(content=None, status_code=204)


# ---------------------------------------------------------------------------
# Redeem (auth-EXEMPT) + content-negotiated invite advert
# ---------------------------------------------------------------------------

# Map the S1 store exceptions onto HTTP statuses (design section 5 failure
# modes). Wrong pin / expired / attempt-capped -> 403; already redeemed /
# revoked -> 409.
def _invite_exception_to_http(exc: Exception) -> tuple[int, str]:
    if isinstance(exc, (InvitePinError, InviteExpiredError)):
        return 403, str(exc)
    if isinstance(exc, (InviteAlreadyRedeemedError, InviteRevokedError)):
        return 409, str(exc)
    return 400, str(exc)


@router.post("/api/projects/invites/redeem")
async def redeem_invite(request: Request, body: RedeemInviteIn):
    """Auth-EXEMPT redeem (the PIN is the proof of possession).

    Flow (design section 2, Approach C):
      1. Verify invite_id + PIN (attempts, TTL, single-use) via the S1 store.
      2. Derive the handle {project_slug}-{harness}[-{label}], dedup against
         ACTIVE registry agents holding that handle in this project.
      3. Build a CreateAuthRequest-shaped record (framework=harness,
         identity_claim=handle, requested_scopes=invite scopes, project_id).
      4. auto: approve through the shared approve_request_record helper
         (decided_by = invite.created_by). manual: create a pending request so
         the consent bell fires.
      5. Return build_connection_bundle(...) + {request_id, agent_handle,
         poll_path}.
    """
    client_ip = request.client.host if request.client else "unknown"
    if not rate_limit_ok(client_ip):
        return JSONResponse(
            {"error": "too many redeem attempts; slow down and retry"},
            status_code=429,
        )

    store = request.app.state.project_invites
    try:
        invite = await store.redeem(body.invite_id, body.pin)
    except Exception as exc:  # noqa: BLE001 - mapped to HTTP below
        status, msg = _invite_exception_to_http(exc)
        return JSONResponse({"error": msg}, status_code=status)

    # OS-level (project-less) invite: mint a chat-available identity with a
    # global grant and NO project grant. Same PIN/expiry/attempt-cap security
    # (already enforced by store.redeem above); only the grant is not
    # project-scoped, so there is no membership row.
    if invite["project_id"] is None:
        return await _redeem_os_level(request, body, invite, store)

    project_store = request.app.state.project_store
    project = await project_store.get_project(invite["project_id"])
    if project is None:
        return JSONResponse({"error": "project not found"}, status_code=404)

    # The store returns scopes as a JSON string; parse it defensively.
    raw_scopes = invite["scopes"] or []
    if isinstance(raw_scopes, str):
        try:
            raw_scopes = json.loads(raw_scopes)
        except (ValueError, TypeError):
            raw_scopes = []
    # Force-include project_tasks for this project even if the record somehow
    # omitted it (membership invariant: a successful redeem MUST yield a member).
    scopes = list(raw_scopes)
    if "project_tasks" not in scopes:
        scopes = ["project_tasks"] + scopes

    handle = _derive_handle(project.get("slug") or invite["project_id"], body.harness, body.label)
    handle = await _dedupe_handle(request, handle)

    # Build a CreateAuthRequest-shaped record reusing the consent store's
    # create() so the downstream approve helper consumes a normal record.
    from tinyagentos.auth_requests_store import AuthRequestsStore
    from tinyagentos.routes.agent_auth_requests import approve_request_record

    auth_store = request.app.state.auth_requests
    record = await auth_store.create(
        identity_claim=handle,
        framework=body.harness,
        requested_scopes=scopes,
        requested_skills=None,
        reason=f"invite {body.invite_id}",
        duration_secs=None,
        project_id=invite["project_id"],
    )

    if invite["approval_mode"] == "auto":
        try:
            await approve_request_record(
                request,
                record=record,
                granted_scopes=scopes,
                effective_project=invite["project_id"],
                decided_by=invite["created_by"],
                project_id=invite["project_id"],
            )
        except Exception as exc:  # noqa: BLE001 - surface as JSON error
            return JSONResponse({"error": str(exc)}, status_code=400)
    else:
        # manual: leave pending so the consent bell fires (handled by
        # create_auth_request in the consent route path). We created the record
        # directly above, so surface a bell notification too.
        _notify_pending_invite(request, record)

    # Record the redeem back on the invite for audit.
    try:
        await store.mark_redeemed(body.invite_id, handle, record["id"])
    except Exception:  # noqa: BLE001 - audit best-effort
        pass

    bundle = await build_connection_bundle(
        request,
        invite_record=invite,
        project=project,
        agent_handle=handle,
        granted_scopes=scopes,
        check_interval_secs=invite.get("check_interval_secs") or 1800,
    )

    return {
        "request_id": record["id"],
        "agent_handle": handle,
        "poll_path": f"/api/agents/auth-requests/{record['id']}",
        "bundle": bundle,
    }


async def _redeem_os_level(request: Request, body: RedeemInviteIn, invite: dict, store) -> dict | JSONResponse:
    """Redeem an OS-level (project-less) invite.

    Mints a chat-available identity: the granted scopes are exactly the invite's
    scopes (project_tasks is NOT forced), the auth-request carries project_id=None,
    and approve_request_record writes GLOBAL grants (project_id=None) with no
    membership row. The alias (display_name) from the invite is applied to the
    minted identity.
    """
    # Parse scopes defensively (store returns a JSON string). No project_tasks
    # is forced: this identity is not bound to any project.
    raw_scopes = invite["scopes"] or []
    if isinstance(raw_scopes, str):
        try:
            raw_scopes = json.loads(raw_scopes)
        except (ValueError, TypeError):
            raw_scopes = []
    scopes = list(raw_scopes)

    display_name = invite.get("display_name")
    handle = _derive_os_handle(display_name, body.harness, body.label)
    handle = await _dedupe_handle(request, handle)

    from tinyagentos.routes.agent_auth_requests import approve_request_record

    auth_store = request.app.state.auth_requests
    record = await auth_store.create(
        identity_claim=handle,
        framework=body.harness,
        requested_scopes=scopes,
        requested_skills=None,
        reason=f"invite {body.invite_id}",
        duration_secs=None,
        project_id=None,
    )

    if invite["approval_mode"] == "auto":
        try:
            await approve_request_record(
                request,
                record=record,
                granted_scopes=scopes,
                effective_project=None,
                decided_by=invite["created_by"],
                project_id=None,
                display_name=display_name,
            )
        except Exception as exc:  # noqa: BLE001 - surface as JSON error
            return JSONResponse({"error": str(exc)}, status_code=400)
    else:
        _notify_pending_invite(request, record)

    try:
        await store.mark_redeemed(body.invite_id, handle, record["id"])
    except Exception:  # noqa: BLE001 - audit best-effort
        pass

    bundle = await build_os_connection_bundle(
        request,
        invite_record=invite,
        agent_handle=handle,
        granted_scopes=scopes,
        check_interval_secs=invite.get("check_interval_secs") or 1800,
    )

    return {
        "request_id": record["id"],
        "agent_handle": handle,
        "poll_path": f"/api/agents/auth-requests/{record['id']}",
        "bundle": bundle,
    }


def _notify_pending_invite(request: Request, record: dict) -> None:
    """Best-effort bell notification for a manually-approved invite redeem.

    Fire-and-forget: a notification failure must never fail the redeem.
    """
    notifs = getattr(request.app.state, "notifications", None)
    if notifs is None:
        return
    try:
        scopes = record.get("requested_scopes") or []
        import asyncio

        asyncio.create_task(
            notifs.add(
                title="Access request",
                message=f"{record['identity_claim']} is requesting {', '.join(scopes)}",
                level="info",
                source="auth_requests",
                data={
                    "request_id": record["id"],
                    "identity_claim": record["identity_claim"],
                    "framework": record["framework"],
                    "requested_scopes": list(scopes),
                },
            )
        )
    except Exception:  # noqa: BLE001
        pass


@router.get("/i/{invite_id}")
async def invite_info(request: Request, invite_id: str):
    """Content-negotiated invite advert (auth-EXEMPT).

    `Accept: application/json` -> machine instructions for the redeem contract.
    Browser `Accept: text/html` -> a minimal self-contained page explaining what
    this is and that an agent should fetch it as JSON. No PIN check here; it
    only advertises the redeem contract.
    """
    store = request.app.state.project_invites
    invite = await store.get(invite_id)
    if invite is None:
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            return HTMLResponse(
                "<!doctype html><meta charset=utf-8><title>Invite not found</title>"
                "<h1>Invite not found</h1><p>This invite link is invalid or expired.</p>",
                status_code=404,
            )
        return JSONResponse({"error": "invite not found"}, status_code=404)

    project_store = request.app.state.project_store
    project = await project_store.get_project(invite["project_id"]) or {}

    accept = request.headers.get("accept", "")
    if "text/html" in accept and "application/json" not in accept:
        return HTMLResponse(_invite_html(invite, project), status_code=200)

    return {
        "redeem": {
            "method": "POST",
            "path": "/api/projects/invites/redeem",
            "fields": {
                "invite_id": "required",
                "pin": "required",
                "harness": "required (your tool: kilo|grok|opencode|claude|aider|...)",
                "label": "optional (short role hint, e.g. frontend)",
            },
        },
        "project": {
            "id": project.get("id"),
            "name": project.get("name"),
            "slug": project.get("slug"),
        },
        "onboarding": {
            "links": [
                {"label": "taOS repository", "url": "https://github.com/jaylfc/taOS"},
                {
                    "label": "Agent manual (04-apps.md)",
                    "url": "https://github.com/jaylfc/taOS/blob/main/docs/agent-manual/04-apps.md",
                },
                {
                    "label": "Agent manual (09-os-control.md)",
                    "url": "https://github.com/jaylfc/taOS/blob/main/docs/agent-manual/09-os-control.md",
                },
            ],
        },
    }


def _invite_html(invite: dict, project: dict) -> str:
    """Minimal self-contained HTML page for a human who opens the invite link."""
    name = html.escape(project.get("name") or invite.get("project_id") or "a taOS project")
    return (
        "<!doctype html><meta charset=utf-8><title>taOS project invite</title>"
        "<h1>You have been invited to a taOS project</h1>"
        f"<p>This link is an invite to join <strong>{name}</strong> as an external agent.</p>"
        "<p>Hand the URL to your agent (Claude Code, grok, opencode, kilo, aider, ...). "
        "It should fetch this URL as JSON and then POST a redeem with the PIN it was "
        "given. The JSON advertises the exact redeem contract.</p>"
        "<p>If you are the agent: request <code>Accept: application/json</code> and follow "
        "the returned <code>redeem</code> instructions.</p>"
    )
