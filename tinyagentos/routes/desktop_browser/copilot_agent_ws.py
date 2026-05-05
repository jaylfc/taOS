"""Copilot agent-side WebSocket — agent runtime sends ops, receives acks.

The agent runtime obtains a ticket via /api/desktop/browser/copilot/ticket
(same endpoint as the iframe side). The ticket is bound to (user, agent, tab),
but the agent connection is keyed only by (user, agent) — an agent has one WS
regardless of how many tabs it's pinned to. The ticket's tab_id determines the
*default* iframe target for op routing.
"""
from __future__ import annotations

import logging
from urllib.parse import urlparse

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from tinyagentos.routes.desktop_browser import router

_logger = logging.getLogger(__name__)

_DRIVE_OPS = {"scrollTo", "click", "type", "navigate", "focus"}

# Privileged ops and the permission required to execute them.
PRIVILEGED_OPS = {
    "drive": {"scrollTo", "click", "type", "focus"},
    "navigate": {"navigate"},
    "see_cookies": set(),  # see_cookies is for raw cookie reads — not used by current ops
}

# Reverse lookup: op name → required permission
OP_TO_PERMISSION: dict[str, str] = {}
for _perm, _ops in PRIVILEGED_OPS.items():
    for _op_name in _ops:
        OP_TO_PERMISSION[_op_name] = _perm


def _required_permission(op: str) -> str | None:
    return OP_TO_PERMISSION.get(op)


def _trusted_host(server_url: str | None) -> str:
    """Derive the host from an authoritative server-tracked URL. Returns
    empty string if the URL is missing or unparseable. Never trust
    agent-supplied msg["host"] for authorization."""
    if not server_url:
        return ""
    try:
        return urlparse(server_url).hostname or ""
    except Exception:
        return ""


@router.websocket("/api/desktop/browser/copilot-agent")
async def copilot_agent_ws(websocket: WebSocket, ticket: str):
    """Agent runtime → server WebSocket."""
    consumed = websocket.app.state.copilot_ticket_store.consume(ticket)
    if consumed is None:
        await websocket.close(code=4401, reason="invalid or expired ticket")
        return

    pinned = await websocket.app.state.browser_store.list_pins_for_tab(
        user_id=consumed.user_id,
        profile_id=consumed.profile_id,
        tab_id=consumed.tab_id,
    )
    if not any(p["agent_id"] == consumed.agent_id for p in pinned):
        await websocket.close(code=4403, reason="agent not pinned")
        return

    await websocket.accept()
    hub = websocket.app.state.copilot_hub
    store = websocket.app.state.browser_store
    hub.add_agent(user_id=consumed.user_id, agent_id=consumed.agent_id, ws=websocket)

    try:
        while True:
            msg = await websocket.receive_json()
            op = msg.get("op")
            if not isinstance(op, str):
                continue

            # Allow the agent to target a specific (profile, tab) per op via msg fields,
            # falling back to the ticket-bound (profile, tab). For PR 7 the typical agent
            # only operates on the ticket's tab; cross-tab ops are out of scope.
            target_profile = msg.get("profile_id", consumed.profile_id)
            target_tab = msg.get("tab_id", consumed.tab_id)

            # If the agent overrides the target tab/profile, re-verify the pin.
            # The connect-time check only proves the ticket-bound (profile, tab) is pinned.
            if target_profile != consumed.profile_id or target_tab != consumed.tab_id:
                target_pinned = await store.list_pins_for_tab(
                    user_id=consumed.user_id,
                    profile_id=target_profile,
                    tab_id=target_tab,
                )
                if not any(p["agent_id"] == consumed.agent_id for p in target_pinned):
                    await websocket.send_json({
                        "event": "denied",
                        "op_id": msg.get("op_id"),
                        "reason": "agent not pinned for target tab",
                    })
                    continue

            # Capability check for privileged ops. The host comes from the
            # SERVER-tracked current URL for this tab (set by proxy.py on every
            # successful HTML fetch). Agent-supplied msg["host"] is NOT trusted —
            # otherwise a malicious agent could claim it's operating on an allowed
            # host while actually driving on a different one.
            required = _required_permission(op)
            if required is not None:
                trusted_url = hub.get_tab_url(
                    user_id=consumed.user_id,
                    profile_id=target_profile,
                    tab_id=target_tab,
                )
                host = _trusted_host(trusted_url)
                granted = await store.check_capability(
                    user_id=consumed.user_id,
                    profile_id=target_profile,
                    agent_id=consumed.agent_id,
                    host=host,
                    permission=required,
                )
                if not granted:
                    # Notify iframe so the modal can pop
                    await hub.notify_capability_needed(
                        user_id=consumed.user_id,
                        profile_id=target_profile,
                        tab_id=target_tab,
                        agent_id=consumed.agent_id,
                        permission=required,
                        host=host,
                        full_url=trusted_url or "",
                    )
                    # Tell agent the op was denied
                    await websocket.send_json({
                        "event": "denied",
                        "op_id": msg.get("op_id"),
                        "reason": "capability-needed",
                        "permission": required,
                    })
                    continue

            ok = await hub.route_op_to_iframe(
                user_id=consumed.user_id,
                profile_id=target_profile,
                tab_id=target_tab,
                agent_id=consumed.agent_id,
                op=msg,
            )
            if not ok:
                await websocket.send_json({
                    "event": "error",
                    "op_id": msg.get("op_id"),
                    "reason": "iframe not connected",
                })
                continue

            if op in _DRIVE_OPS:
                bumped = await store.bump_drive_session(
                    user_id=consumed.user_id,
                    profile_id=target_profile,
                    tab_id=target_tab,
                    agent_id=consumed.agent_id,
                )
                if not bumped:
                    await store.start_drive_session(
                        user_id=consumed.user_id,
                        profile_id=target_profile,
                        tab_id=target_tab,
                        agent_id=consumed.agent_id,
                    )
    except WebSocketDisconnect:
        pass
    finally:
        hub.remove_agent(user_id=consumed.user_id, agent_id=consumed.agent_id)
