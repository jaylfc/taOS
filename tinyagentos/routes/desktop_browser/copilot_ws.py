"""Copilot WebSocket ticket store and minting endpoint.

Tickets are short-lived (60s) single-use tokens that let the copilot
client authenticate a WebSocket upgrade without relying on cookies, which
some browsers don't forward reliably on WS upgrades.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import Depends, Request, WebSocket
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.websockets import WebSocketDisconnect

from tinyagentos.auth import get_current_user
from tinyagentos.routes.desktop_browser import router

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CopilotTicket:
    user_id: str
    profile_id: str
    tab_id: str
    agent_id: str
    issued_at: float


class CopilotTicketStore:
    """In-memory single-use ticket store. Tickets expire after 60s.

    Tickets are minted by an authenticated HTTP endpoint after the server
    confirms the (user, agent, tab) pin holds. The WebSocket upgrade then
    consumes the ticket — single-use, expires fast.
    """

    TICKET_TTL_SECONDS = 60.0

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        # `clock` is injectable for tests (avoids monkey-patching time.time).
        self._clock = clock or time.time
        self._tickets: dict[str, CopilotTicket] = {}

    def mint(
        self,
        *,
        user_id: str,
        profile_id: str,
        tab_id: str,
        agent_id: str,
    ) -> str:
        """Mint a single-use ticket. Returns the opaque token string."""
        if not all([user_id, profile_id, tab_id, agent_id]):
            raise ValueError("user_id, profile_id, tab_id, agent_id all required")
        now = self._clock()
        token = secrets.token_urlsafe(32)
        self._tickets[token] = CopilotTicket(
            user_id=user_id,
            profile_id=profile_id,
            tab_id=tab_id,
            agent_id=agent_id,
            issued_at=now,
        )
        # Opportunistic GC — sweep expired tickets to keep dict bounded.
        self._tickets = {
            k: v
            for k, v in self._tickets.items()
            if now - v.issued_at < self.TICKET_TTL_SECONDS
        }
        return token

    def consume(self, token: str) -> CopilotTicket | None:
        """Consume the ticket. Returns the ticket if valid and unexpired,
        None otherwise. The ticket is removed regardless of validity."""
        ticket = self._tickets.pop(token, None)
        if ticket is None:
            return None
        if self._clock() - ticket.issued_at >= self.TICKET_TTL_SECONDS:
            return None
        return ticket


# ---------------------------------------------------------------------------
# HTTP endpoint
# ---------------------------------------------------------------------------

class TicketRequest(BaseModel):
    profile_id: str
    tab_id: str
    agent_id: str


@router.post("/api/desktop/browser/copilot/ticket")
async def mint_copilot_ticket(
    request: Request,
    body: TicketRequest,
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
):
    """Mint a single-use 60s ticket for a copilot WebSocket upgrade.

    Verifies the user has the agent pinned to this tab before minting.
    """
    user_id = str(current_user.get("id") or "")
    if not user_id:
        return JSONResponse({"error": "session has no user id"}, status_code=401)

    pinned = await request.app.state.browser_store.list_pins_for_tab(
        user_id=user_id,
        profile_id=body.profile_id,
        tab_id=body.tab_id,
    )
    if not any(p["agent_id"] == body.agent_id for p in pinned):
        return JSONResponse({"error": "agent not pinned to tab"}, status_code=403)

    token = request.app.state.copilot_ticket_store.mint(
        user_id=user_id,
        profile_id=body.profile_id,
        tab_id=body.tab_id,
        agent_id=body.agent_id,
    )
    return {"ticket": token, "ttl_seconds": CopilotTicketStore.TICKET_TTL_SECONDS}


# ---------------------------------------------------------------------------
# CopilotHub
# ---------------------------------------------------------------------------

async def _close_safely(ws: WebSocket) -> None:
    """Close a WebSocket without raising if already closed."""
    try:
        await ws.close()
    except Exception:
        pass


class CopilotHub:
    """Routes messages between agents (server-side runtime, future PR 7)
    and iframes (browser-side copilot.js).

    PR 6 only registers iframe connections. The fan-out from proxy.py
    (page-changed events, Task 5) iterates iframe connections by
    (user, profile, tab) and pushes events.

    Connection key: (user_id, profile_id, tab_id, agent_id) — one WS
    per (tab, pinned-agent) pair. If a tab has 3 agents pinned, the
    iframe opens 3 WS connections (one per agent).
    """

    def __init__(self) -> None:
        self._iframe_conns: dict[tuple[str, str, str, str], WebSocket] = {}
        # Agent connections: one per (user_id, agent_id) tuple. An agent's runtime
        # talks to the server through this single WS regardless of how many tabs
        # it's pinned to.
        self._agent_conns: dict[tuple[str, str], WebSocket] = {}
        # Authoritative current URL per (user_id, profile_id, tab_id) — written
        # by proxy.py on every successful HTML fetch, read by capability checks
        # in copilot_agent_ws.py. The agent-supplied msg["host"] is NOT trusted
        # for authorization; this tracker is the source of truth.
        self._tab_urls: dict[tuple[str, str, str], str] = {}

    def set_tab_url(
        self, *, user_id: str, profile_id: str, tab_id: str, url: str,
    ) -> None:
        """Record the current URL the iframe is serving for this tab.
        Called from proxy.py on every successful HTML fetch."""
        self._tab_urls[(user_id, profile_id, tab_id)] = url

    def get_tab_url(
        self, *, user_id: str, profile_id: str, tab_id: str,
    ) -> str | None:
        """Return the last recorded URL for this tab, or None if unknown."""
        return self._tab_urls.get((user_id, profile_id, tab_id))

    def add_iframe(
        self, *, user_id: str, profile_id: str, tab_id: str, agent_id: str,
        ws: WebSocket,
    ) -> None:
        """Register an iframe WS. Replaces any prior connection for the same key
        (refresh, reconnect — close the old one)."""
        key = (user_id, profile_id, tab_id, agent_id)
        old = self._iframe_conns.pop(key, None)
        if old is not None:
            asyncio.create_task(_close_safely(old))
        self._iframe_conns[key] = ws

    def remove_iframe(
        self, *, user_id: str, profile_id: str, tab_id: str, agent_id: str,
    ) -> None:
        """Remove a registered iframe WS. No-op if not present."""
        self._iframe_conns.pop((user_id, profile_id, tab_id, agent_id), None)

    def add_agent(self, *, user_id: str, agent_id: str, ws: WebSocket) -> None:
        """Register agent connection. Replaces prior connection for the same key
        (closes the old one async-style, mirrors add_iframe pattern)."""
        key = (user_id, agent_id)
        old = self._agent_conns.pop(key, None)
        if old is not None:
            asyncio.create_task(_close_safely(old))
        self._agent_conns[key] = ws

    def remove_agent(self, *, user_id: str, agent_id: str) -> None:
        self._agent_conns.pop((user_id, agent_id), None)

    async def route_op_to_iframe(
        self, *, user_id: str, profile_id: str, tab_id: str, agent_id: str, op: dict,
    ) -> bool:
        """Forward an op to the iframe-side WS. Returns True iff the iframe was reachable."""
        key = (user_id, profile_id, tab_id, agent_id)
        ws = self._iframe_conns.get(key)
        if ws is None:
            return False
        try:
            await ws.send_json(op)
            return True
        except Exception as e:
            _logger.debug("copilot route_op_to_iframe failed: %s", e)
            return False

    async def route_ack_to_agent(
        self, *, user_id: str, agent_id: str, ack: dict,
    ) -> bool:
        """Forward an ack from iframe → agent. Returns True iff the agent was reachable."""
        ws = self._agent_conns.get((user_id, agent_id))
        if ws is None:
            return False
        try:
            await ws.send_json(ack)
            return True
        except Exception as e:
            _logger.debug("copilot route_ack_to_agent failed: %s", e)
            return False

    async def notify_capability_needed(
        self,
        *,
        user_id: str,
        profile_id: str,
        tab_id: str,
        agent_id: str,
        permission: str,
        host: str,
        full_url: str = "",
    ) -> bool:
        """Push a capability-needed event to the iframe-side WS for the
        (user, profile, tab, agent) tuple. The iframe's copilot.js forwards
        it to the parent via postMessage; the parent's agent-ws-bridge
        catches it and dispatches taos-browser:capability-prompt for the
        CapabilityPromptModal.

        Returns True iff the iframe was reachable.
        """
        key = (user_id, profile_id, tab_id, agent_id)
        ws = self._iframe_conns.get(key)
        if ws is None:
            return False
        payload = {
            "event": "capability-needed",
            "profile_id": profile_id,
            "permission": permission,
            "host": host,
            "full_url": full_url,
        }
        try:
            await ws.send_json(payload)
            return True
        except Exception as e:
            _logger.debug("notify_capability_needed failed: %s", e)
            return False

    async def push_event_to_pinned(
        self, *, user_id: str, profile_id: str, tab_id: str, event: dict,
    ) -> None:
        """Push an event to every iframe connection for every agent pinned
        to this tab. Used by proxy.py when a tab navigates → page-changed
        event. Failed sends are logged and ignored — the connection will be
        cleaned up when its WS handler hits WebSocketDisconnect."""
        targets = [
            ws for (u, p, t, _a), ws in self._iframe_conns.items()
            if u == user_id and p == profile_id and t == tab_id
        ]
        for ws in targets:
            try:
                await ws.send_json(event)
            except Exception as e:
                _logger.debug("copilot push failed: %s", e)


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------

# Allowed event kinds from iframe → server. 'ack' is routed back to the agent runtime.
_ALLOWED_EVENT_KINDS = {
    "page-changed", "url-changed", "scroll", "form-submit",
    "download-started", "ack",
}


@router.websocket("/api/desktop/browser/copilot")
async def copilot_ws(websocket: WebSocket, ticket: str):
    """Iframe-side WebSocket for copilot.js. Authenticated by single-use ticket.

    URL: ws://host/api/desktop/browser/copilot?ticket=<token>
    """
    # Consume the ticket BEFORE accepting the upgrade — invalid → close.
    consumed = websocket.app.state.copilot_ticket_store.consume(ticket)
    if consumed is None:
        await websocket.close(code=4401, reason="invalid or expired ticket")
        return

    # Re-verify the pin still holds (user could have unpinned between
    # mint and connect).
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
    hub.add_iframe(
        user_id=consumed.user_id,
        profile_id=consumed.profile_id,
        tab_id=consumed.tab_id,
        agent_id=consumed.agent_id,
        ws=websocket,
    )
    try:
        while True:
            message = await websocket.receive_json()
            event_kind = message.get("event")
            if event_kind not in _ALLOWED_EVENT_KINDS:
                # Don't crash on unknown events; just drop.
                continue
            # Iframe → server events. 'ack' messages get routed to the agent runtime.
            # Other events (page-changed etc.) are broadcast by proxy.py via push_event_to_pinned.
            if event_kind == "ack":
                await hub.route_ack_to_agent(
                    user_id=consumed.user_id,
                    agent_id=consumed.agent_id,
                    ack=message,
                )
    except WebSocketDisconnect:
        pass
    finally:
        hub.remove_iframe(
            user_id=consumed.user_id,
            profile_id=consumed.profile_id,
            tab_id=consumed.tab_id,
            agent_id=consumed.agent_id,
        )
