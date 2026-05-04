"""Tests for CopilotTicketStore and the /api/desktop/browser/copilot/ticket endpoint."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


# ---------------------------------------------------------------------------
# Store-only unit tests (no HTTP)
# ---------------------------------------------------------------------------

class TestCopilotTicketStore:
    def _make_store(self, now: list[float] | None = None):
        from tinyagentos.routes.desktop_browser.copilot_ws import CopilotTicketStore
        if now is None:
            return CopilotTicketStore()
        return CopilotTicketStore(clock=lambda: now[0])

    def test_mint_returns_url_safe_token(self):
        store = self._make_store()
        token = store.mint(user_id="u1", profile_id="p1", tab_id="t1", agent_id="a1")
        assert token
        # token_urlsafe(32) uses base64url — no +, /, or = characters
        for bad_char in ("+", "/", "="):
            assert bad_char not in token

    @pytest.mark.parametrize("missing", ["user_id", "profile_id", "tab_id", "agent_id"])
    def test_mint_rejects_empty_params(self, missing):
        store = self._make_store()
        kwargs = dict(user_id="u1", profile_id="p1", tab_id="t1", agent_id="a1")
        kwargs[missing] = ""
        with pytest.raises(ValueError):
            store.mint(**kwargs)

    def test_consume_returns_ticket_once(self):
        now = [0.0]
        store = self._make_store(now)
        token = store.mint(user_id="u1", profile_id="p1", tab_id="t1", agent_id="a1")
        ticket = store.consume(token)
        assert ticket is not None
        assert ticket.user_id == "u1"
        assert ticket.profile_id == "p1"
        assert ticket.tab_id == "t1"
        assert ticket.agent_id == "a1"
        # Second consume → None (single-use)
        assert store.consume(token) is None

    def test_consume_returns_none_for_unknown_token(self):
        store = self._make_store()
        assert store.consume("totally-unknown-token") is None

    def test_consume_returns_none_for_expired_ticket(self):
        now = [0.0]
        store = self._make_store(now)
        token = store.mint(user_id="u1", profile_id="p1", tab_id="t1", agent_id="a1")
        # Advance clock past TTL
        now[0] = 61.0
        result = store.consume(token)
        assert result is None
        # Ticket must also be gone from the store after expiry consume
        assert store.consume(token) is None

    def test_mint_garbage_collects_expired(self):
        from tinyagentos.routes.desktop_browser.copilot_ws import CopilotTicketStore

        now = [0.0]
        store = CopilotTicketStore(clock=lambda: now[0])

        # Mint A at t=0
        token_a = store.mint(user_id="u1", profile_id="p1", tab_id="t1", agent_id="a1")

        # Advance past TTL and mint B → GC should sweep A out
        now[0] = 120.0
        store.mint(user_id="u1", profile_id="p1", tab_id="t1", agent_id="b1")

        # A is gone — consume returns None and the internal dict doesn't hold it
        assert store.consume(token_a) is None

    def test_mint_single_clock_read_no_self_eviction(self):
        """Regression: mint() must capture `now` once so GC cannot evict the
        freshly minted ticket when the clock jumps exactly TTL between calls."""
        from tinyagentos.routes.desktop_browser.copilot_ws import CopilotTicketStore

        # Clock returns 0.0 on first call, then 60.0 (exactly TTL) on every
        # subsequent call — simulates the worst-case double-read scenario.
        calls = iter([0.0, 60.0])
        clock = lambda: next(calls)  # noqa: E731

        store = CopilotTicketStore(clock=clock)
        token = store.mint(user_id="u1", profile_id="p1", tab_id="t1", agent_id="a1")

        # Token must still be present in the store — not evicted by its own mint.
        assert token in store._tickets
        # And must be consumable (using real time.time for consume, so patch
        # issued_at to 0 is fine — consume's clock call will be >> 0 only if
        # the ticket survived the GC sweep; here we pass a fixed consume clock).
        consume_store = CopilotTicketStore(clock=lambda: 0.0)
        consume_store._tickets = store._tickets
        ticket = consume_store.consume(token)
        assert ticket is not None
        assert ticket.agent_id == "a1"


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------

def _add_agent(app, agent_id: str):
    app.state.config.agents.append({
        "id": agent_id,
        "name": agent_id,
        "host": "127.0.0.1",
        "qmd_index": "test",
        "color": "#000000",
    })


@pytest.mark.asyncio
class TestMintTicketEndpoint:
    async def test_mint_ticket_happy_path(self, client, app):
        from tinyagentos.routes.desktop_browser.copilot_ws import CopilotTicketStore
        _add_agent(app, "agent-tick-1")
        # Pin the agent first
        await client.post(
            "/api/desktop/browser/pins",
            json={"profile_id": "p1", "tab_id": "t1", "agent_id": "agent-tick-1"},
        )
        resp = await client.post(
            "/api/desktop/browser/copilot/ticket",
            json={"profile_id": "p1", "tab_id": "t1", "agent_id": "agent-tick-1"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "ticket" in body
        assert body["ttl_seconds"] == CopilotTicketStore.TICKET_TTL_SECONDS

    async def test_mint_ticket_403_when_not_pinned(self, client):
        resp = await client.post(
            "/api/desktop/browser/copilot/ticket",
            json={"profile_id": "p1", "tab_id": "t1", "agent_id": "no-pin-agent"},
        )
        assert resp.status_code == 403
        assert resp.json() == {"error": "agent not pinned to tab"}

    async def test_mint_ticket_401_when_unauthenticated(self, app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as unauth_client:
            resp = await unauth_client.post(
                "/api/desktop/browser/copilot/ticket",
                json={"profile_id": "p1", "tab_id": "t1", "agent_id": "a1"},
            )
            assert resp.status_code == 401

    async def test_mint_ticket_multi_user_isolation(self, client, app, tmp_data_dir):
        """User A pins an agent; user B cannot mint a ticket for it."""
        _add_agent(app, "agent-iso")
        # User A pins
        await client.post(
            "/api/desktop/browser/pins",
            json={"profile_id": "p1", "tab_id": "t1", "agent_id": "agent-iso"},
        )

        # Set up user B
        auth_mgr = app.state.auth
        if auth_mgr.find_user("user_b") is None:
            invite_code = auth_mgr.add_user_invite("user_b", "admin")
            auth_mgr.complete_invite("user_b", invite_code, "user_b", "", "pass_b")
        record = auth_mgr.find_user("user_b")
        token_b = auth_mgr.create_session(user_id=record["id"], long_lived=True)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            cookies={"taos_session": token_b},
        ) as b_client:
            resp = await b_client.post(
                "/api/desktop/browser/copilot/ticket",
                json={"profile_id": "p1", "tab_id": "t1", "agent_id": "agent-iso"},
            )
            assert resp.status_code == 403

    async def test_mint_ticket_422_on_missing_field(self, client):
        """Pydantic returns 422 when a required body field is omitted."""
        resp = await client.post(
            "/api/desktop/browser/copilot/ticket",
            json={"profile_id": "p1", "tab_id": "t1"},  # agent_id missing
        )
        assert resp.status_code == 422

    async def test_minted_ticket_can_be_consumed_via_app_state(self, client, app):
        _add_agent(app, "agent-consume")
        await client.post(
            "/api/desktop/browser/pins",
            json={"profile_id": "p1", "tab_id": "t1", "agent_id": "agent-consume"},
        )
        resp = await client.post(
            "/api/desktop/browser/copilot/ticket",
            json={"profile_id": "p1", "tab_id": "t1", "agent_id": "agent-consume"},
        )
        assert resp.status_code == 200
        token = resp.json()["ticket"]

        # The ticket store must hold a consumable ticket for that token
        ticket = app.state.copilot_ticket_store.consume(token)
        assert ticket is not None
        assert ticket.agent_id == "agent-consume"
        assert ticket.tab_id == "t1"
