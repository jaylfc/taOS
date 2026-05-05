"""Copilot agent-side WebSocket — agent runtime sends ops, receives acks.

The agent runtime obtains a ticket via /api/desktop/browser/copilot/ticket
(same endpoint as the iframe side). The ticket is bound to (user, agent, tab),
but the agent connection is keyed only by (user, agent) — an agent has one WS
regardless of how many tabs it's pinned to. The ticket's tab_id determines the
*default* iframe target for op routing.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urlparse

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from tinyagentos.routes.desktop_browser import push, router
from tinyagentos.routes.desktop_browser.copilot_ws import _maybe_send_chat_push

_logger = logging.getLogger(__name__)

# Strong references to fire-and-forget background tasks. Without this, CPython
# may GC a task before it completes; exceptions would also be silently swallowed.
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _log_task_exception(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        _logger.warning("background push task failed: %s", exc)


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


def _short_url(url: str) -> str:
    """Return host + truncated path for display in push notifications.

    e.g. 'https://github.com/foo/bar/baz/qux' → 'github.com/foo/bar/...'
    """
    if not url:
        return url
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        path = parsed.path or ""
        # Truncate path to first 2 segments for brevity
        parts = [p for p in path.split("/") if p]
        if len(parts) > 2:
            short_path = "/" + "/".join(parts[:2]) + "/..."
        else:
            short_path = path
        return host + short_path
    except Exception:
        return url[:60]


async def _maybe_send_drive_push(
    user_id: str,
    agent_id: str,
    agent_name: str,
    target_url: str,
    window_id: str,
    tab_id: str,
    store: Any,
    hub: Any,
    vapid: tuple[str, str],
) -> None:
    """Send a push notification when the agent starts driving a tab, unless
    the user is already focused on that tab or has muted drive-started alerts."""
    from tinyagentos.routes.desktop_browser.store import BrowserStore
    if not isinstance(store, BrowserStore):
        return
    if await store.is_push_muted(user_id, agent_id, "drive-started"):
        return
    focused = hub.get_focused_tab(user_id)
    if focused is not None:
        if window_id:
            if focused == (window_id, tab_id):
                return
        else:
            # window_id unknown at the agent WS — fall back to tab_id match.
            if focused[1] == tab_id:
                return
    payload = {
        "title": f"{agent_name or 'Agent'} started driving",
        "body": _short_url(target_url),
        "tag": f"drive:{agent_id}:{tab_id}",
        "data": {"window_id": window_id, "tab_id": tab_id, "agent_id": agent_id},
    }
    await push.send(user_id, payload, store=store, vapid=vapid)


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

            # Chat push trigger — fire for chat-message ops (non-blocking).
            if op == "chat-message":
                msg_text = str(msg.get("text") or "")
                vapid = getattr(websocket.app.state, "vapid_keypair", None)
                if vapid is not None:
                    try:
                        _task = asyncio.create_task(_maybe_send_chat_push(
                            user_id=consumed.user_id,
                            agent_id=consumed.agent_id,
                            agent_name=consumed.agent_id,
                            msg_text=msg_text,
                            window_id="",
                            tab_id=target_tab,
                            store=store,
                            hub=hub,
                            vapid=vapid,
                        ))
                        _BACKGROUND_TASKS.add(_task)
                        _task.add_done_callback(_BACKGROUND_TASKS.discard)
                        _task.add_done_callback(_log_task_exception)
                    except Exception:
                        _logger.warning(
                            "chat push trigger setup failed", exc_info=True
                        )

            if op in _DRIVE_OPS:
                bumped = await store.bump_drive_session(
                    user_id=consumed.user_id,
                    profile_id=target_profile,
                    tab_id=target_tab,
                    agent_id=consumed.agent_id,
                )
                if not bumped:
                    # First drive op of this session — start the session and
                    # fire a push notification (non-blocking).
                    await store.start_drive_session(
                        user_id=consumed.user_id,
                        profile_id=target_profile,
                        tab_id=target_tab,
                        agent_id=consumed.agent_id,
                    )
                    target_url = hub.get_tab_url(
                        user_id=consumed.user_id,
                        profile_id=target_profile,
                        tab_id=target_tab,
                    ) or ""
                    agent_name = consumed.agent_id  # best available identifier
                    vapid = getattr(websocket.app.state, "vapid_keypair", None)
                    if vapid is not None:
                        try:
                            _task = asyncio.create_task(_maybe_send_drive_push(
                                user_id=consumed.user_id,
                                agent_id=consumed.agent_id,
                                agent_name=agent_name,
                                target_url=target_url,
                                window_id="",
                                tab_id=target_tab,
                                store=store,
                                hub=hub,
                                vapid=vapid,
                            ))
                            _BACKGROUND_TASKS.add(_task)
                            _task.add_done_callback(_BACKGROUND_TASKS.discard)
                            _task.add_done_callback(_log_task_exception)
                        except Exception:
                            _logger.warning(
                                "drive push trigger setup failed", exc_info=True
                            )
    except WebSocketDisconnect:
        pass
    finally:
        hub.remove_agent(user_id=consumed.user_id, agent_id=consumed.agent_id)
