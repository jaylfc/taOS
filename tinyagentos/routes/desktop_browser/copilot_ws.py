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

# Allowed event kinds from iframe → server. Drive ack events come in PR 7.
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
                # Don't crash on unknown events; just drop. PR 7 will route
                # 'ack' events back to the agent connection.
                continue
            # PR 6: iframe → server events accepted but not routed.
            # PR 7 wires 'ack' to the agent-side connection.
    except WebSocketDisconnect:
        pass
    finally:
        hub.remove_iframe(
            user_id=consumed.user_id,
            profile_id=consumed.profile_id,
            tab_id=consumed.tab_id,
            agent_id=consumed.agent_id,
        )
