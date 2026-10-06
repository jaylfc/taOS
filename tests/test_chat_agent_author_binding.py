"""RED-FIRST: chat routes must bind author identity to the bound agent.

Every deployed agent gets its own TAOS_LOCAL_TOKEN (mint_agent_local_token).
The auth middleware sets request.state.agent_name for such tokens.
tinyagentos/routes/chat.py must use that binding instead of trusting caller-
supplied author_id/slug for post, reaction, typing, thinking, delta and state.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from tinyagentos.chat.typing_registry import TypingRegistry


def _bound_client(app, agent_name: str) -> AsyncClient:
    """Return an unauthenticated AsyncClient presenting *agent_name*'s bound token."""
    token = app.state.auth.mint_agent_local_token(agent_name)
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


def _host_token_client(app) -> AsyncClient:
    """Return a client presenting the host local token (no agent binding)."""
    token = app.state.auth.get_local_token()
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


async def _create_channel_dm(app, name: str = "dm") -> dict:
    ch_store = app.state.chat_channels
    return await ch_store.create_channel(name=name, type="dm", created_by="user")


@pytest.mark.asyncio
class TestChatAgentAuthorBinding:
    async def test_bound_agent_cannot_post_as_other_agent(self, client, app):
        """A bound agent's token forces author_id/author_type to the bound name,
        ignoring any caller-supplied values."""
        ch = await _create_channel_dm(app)
        async with _bound_client(app, "agent-a") as c:
            resp = await c.post("/api/chat/messages", json={
                "channel_id": ch["id"],
                "author_id": "agent-b",
                "author_type": "agent",
                "content": "impersonation attempt",
            })
        assert resp.status_code == 200
        body = resp.json()
        assert body["author_id"] == "agent-a"
        assert body["author_type"] == "agent"

    async def test_bound_agent_reaction_author_is_bound(self, client, app):
        """add_reaction ignores body author_id when a bound agent token is presented."""
        ch = await _create_channel_dm(app)
        msg_store = app.state.chat_messages
        msg = await msg_store.send_message(
            channel_id=ch["id"],
            author_id="agent-a",
            author_type="agent",
            content="a message",
        )
        async with _bound_client(app, "agent-a") as c:
            resp = await c.post(
                f"/api/chat/messages/{msg['id']}/reactions",
                json={"emoji": "\u2764\ufe0f", "author_id": "agent-b"},
            )
        assert resp.status_code == 200
        reactions = (await msg_store.get_message(msg["id"]))["reactions"]
        assert isinstance(reactions, dict)
        thumbs = reactions.get("\u2764\ufe0f", [])
        assert "agent-a" in thumbs
        assert "agent-b" not in thumbs

    async def test_bound_agent_typing_author_is_bound(self, client, app):
        """post_typing ignores body author_id when a bound agent token is presented."""
        app.state.typing = TypingRegistry()
        ch = await _create_channel_dm(app)
        async with _bound_client(app, "agent-a") as c:
            resp = await c.post(
                f"/api/chat/channels/{ch['id']}/typing",
                json={"author_id": "agent-b"},
            )
        assert resp.status_code == 200
        listed = app.state.typing.list(ch["id"])
        agent_slugs = [t["slug"] for t in listed.get("agent", [])]
        assert "agent-a" in agent_slugs

    async def test_bound_agent_thinking_other_slug_is_403(self, client, app):
        """post_thinking returns 403 when body slug does not match the bound agent."""
        app.state.typing = TypingRegistry()
        ch = await _create_channel_dm(app)
        async with _bound_client(app, "agent-a") as c:
            resp = await c.post(
                f"/api/chat/channels/{ch['id']}/thinking",
                json={"slug": "agent-b", "state": "start"},
            )
        assert resp.status_code == 403

    async def test_bound_agent_cannot_change_state_of_other_agents_message(self, client, app):
        """update_message_state returns 403 when the message belongs to another agent."""
        ch = await _create_channel_dm(app)
        msg_store = app.state.chat_messages
        msg = await msg_store.send_message(
            channel_id=ch["id"],
            author_id="agent-a",
            author_type="agent",
            content="agent-a message",
            state="streaming",
        )
        async with _bound_client(app, "agent-b") as c:
            resp = await c.post(
                f"/api/chat/messages/{msg['id']}/state",
                json={"state": "complete"},
            )
        assert resp.status_code == 403

    async def test_bound_agent_cannot_stream_delta_into_other_agents_message(self, client, app):
        """post_message_delta returns 403 when the message belongs to another agent."""
        ch = await _create_channel_dm(app)
        msg_store = app.state.chat_messages
        msg = await msg_store.send_message(
            channel_id=ch["id"],
            author_id="agent-a",
            author_type="agent",
            content="agent-a message",
            state="streaming",
        )
        async with _bound_client(app, "agent-b") as c:
            resp = await c.post(
                f"/api/chat/messages/{msg['id']}/delta",
                json={"channel_id": ch["id"], "delta": " intruder"},
            )
        assert resp.status_code == 403

    async def test_host_local_token_keeps_body_author(self, client, app):
        """No binding (host local token): body author_id is preserved, no regression."""
        ch = await _create_channel_dm(app)
        async with _host_token_client(app) as c:
            resp = await c.post("/api/chat/messages", json={
                "channel_id": ch["id"],
                "author_id": "human-author",
                "author_type": "user",
                "content": "host token message",
            })
        assert resp.status_code == 200
        body = resp.json()
        assert body["author_id"] == "human-author"
        assert body["author_type"] == "user"

    async def test_bound_agent_reaction_type_is_agent(self, client, app):
        """Bound agent reaction ignores body author_type=\"user\" and forwards as agent."""
        ch = await _create_channel_dm(app)
        msg_store = app.state.chat_messages
        msg = await msg_store.send_message(
            channel_id=ch["id"],
            author_id="agent-a",
            author_type="agent",
            content="a message",
        )
        with patch("tinyagentos.routes.chat.maybe_trigger_semantic", new_callable=AsyncMock) as mock_sem:
            async with _bound_client(app, "agent-a") as c:
                resp = await c.post(
                    f"/api/chat/messages/{msg['id']}/reactions",
                    json={"emoji": "\u2764\ufe0f", "author_type": "user"},
                )
        assert resp.status_code == 200
        mock_sem.assert_awaited_once()
        assert mock_sem.call_args.kwargs["reactor_type"] == "agent"

    async def test_bound_agent_typing_kind_is_agent(self, client, app):
        """Bound agent typing is recorded with kind=\"agent\", not \"human\"."""
        app.state.typing = TypingRegistry()
        ch = await _create_channel_dm(app)
        async with _bound_client(app, "agent-a") as c:
            resp = await c.post(
                f"/api/chat/channels/{ch['id']}/typing",
                json={"author_id": "agent-b"},
            )
        assert resp.status_code == 200
        listed = app.state.typing.list(ch["id"])
        assert "agent-a" in [t["slug"] for t in listed.get("agent", [])]
        assert "agent-a" not in [t["slug"] for t in listed.get("human", [])]

    async def test_bound_agent_delta_missing_message_is_403(self, client, app):
        """post_message_delta returns 403 when the target message does not exist."""
        ch = await _create_channel_dm(app)
        async with _bound_client(app, "agent-a") as c:
            resp = await c.post(
                "/api/chat/messages/nonexistent/delta",
                json={"channel_id": ch["id"], "delta": "hi"},
            )
        assert resp.status_code == 403

    async def test_bound_agent_state_missing_message_is_403(self, client, app):
        """update_message_state returns 403 when the target message does not exist."""
        ch = await _create_channel_dm(app)
        async with _bound_client(app, "agent-a") as c:
            resp = await c.post(
                "/api/chat/messages/nonexistent/state",
                json={"state": "complete"},
            )
        assert resp.status_code == 403

    async def test_bound_agent_cannot_stream_into_user_message_with_its_name(self, client, app):
        """Host token posts a message with author_id=\"agent-a\", author_type=\"user\";
        agent-a's bound token delta -> 403."""
        ch = await _create_channel_dm(app)
        async with _host_token_client(app) as c:
            resp = await c.post("/api/chat/messages", json={
                "channel_id": ch["id"],
                "author_id": "agent-a",
                "author_type": "user",
                "content": "host-spoofed message",
            })
        assert resp.status_code == 200
        msg = resp.json()
        async with _bound_client(app, "agent-a") as c:
            resp = await c.post(
                f"/api/chat/messages/{msg['id']}/delta",
                json={"channel_id": ch["id"], "delta": " intruder"},
            )
        assert resp.status_code == 403

    async def test_bound_agent_delta_without_channel_broadcasts_on_message_channel(self, client, app):
        """post_message_delta broadcasts on msg.channel_id when a bound agent omits channel_id in body.
        
        A bound agent-authored message in channel C, POST delta with the agent token and
        no channel_id, asserts the hub broadcast went to C (mirroring the existing
        delta tests' hub fixture).
        """
        from tinyagentos.chat.typing_registry import TypingRegistry
        
        # Mock the chat_hub to verify broadcast calls - need AsyncMock for broadcast
        mock_hub = MagicMock()
        mock_hub.next_seq = MagicMock(return_value=1)
        mock_hub.broadcast = AsyncMock()  # Need AsyncMock for async broadcast method
        app.state.chat_hub = mock_hub
        
        # Create a channel and a message authored by agent-a
        app.state.typing = TypingRegistry()
        ch = await _create_channel_dm(app)
        msg_store = app.state.chat_messages
        msg = await msg_store.send_message(
            channel_id=ch["id"],
            author_id="agent-a",
            author_type="agent",
            content="agent message",
            state="streaming",
        )
        
        # Verify the message was created
        assert msg["author_id"] == "agent-a"
        assert msg["channel_id"] == ch["id"]
        
        # POST delta with bound agent token but no channel_id in body
        async with _bound_client(app, "agent-a") as c:
            resp = await c.post(
                f"/api/chat/messages/{msg['id']}/delta",
                json={"delta": " test delta"},
                # Note: channel_id is omitted from body
            )
        
        # Verify the delta was accepted (success response)
        assert resp.status_code == 200
        assert resp.json()["status"] == "sent"
        
        # Verify the hub.broadcast was called with the message's channel_id, not empty
        mock_hub.broadcast.assert_called_once()
        call_args = mock_hub.broadcast.call_args
        # broadcast is called with positional args: channel_id, dict
        called_channel_id = call_args.args[0]

        assert called_channel_id == ch["id"], (
            f"Expected hub.broadcast to be called with channel_id={ch['id']}, "
            f"but it was called with {called_channel_id}"
        )

        # Verify the broadcast message contains the correct data
        broadcast_dict = call_args.args[1]

        assert broadcast_dict["type"] == "message_delta"
        assert broadcast_dict["message_id"] == msg["id"]
        assert broadcast_dict["channel_id"] == ch["id"]
        assert broadcast_dict["delta"] == " test delta"
