"""Copilot WebSocket ticket store and minting endpoint.

Tickets are short-lived (60s) single-use tokens that let the copilot
client authenticate a WebSocket upgrade without relying on cookies, which
some browsers don't forward reliably on WS upgrades.
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.auth import get_current_user
from tinyagentos.routes.desktop_browser import router


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
        token = secrets.token_urlsafe(32)
        self._tickets[token] = CopilotTicket(
            user_id=user_id,
            profile_id=profile_id,
            tab_id=tab_id,
            agent_id=agent_id,
            issued_at=self._clock(),
        )
        # Opportunistic GC — sweep expired tickets to keep dict bounded.
        now = self._clock()
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
