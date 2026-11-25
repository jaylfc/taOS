"""RED-FIRST tests for taOSmd answer_context integration.

These tests verify that agent memory context is built via taOSmd's
answer_context function instead of raw chat history composition.

Before the fix, these tests would FAIL because:
1. agent_chat_router used build_context_window (raw chat history)
2. skill memory_search returned empty stub
3. user_memory agent-search used raw SQLite search
4. routes/memory search used raw qmd search for agents

After the fix, these tests PASS because all paths route through answer_context.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestAgentChatRouterAnswerContext:
    """Tests that agent_chat_router uses answer_context for memory context."""

    @pytest.mark.asyncio
    async def test_route_inner_calls_answer_context_for_each_recipient(self):
        """Test that _route_inner calls answer_context per recipient agent."""
        from tinyagentos.agent_chat_router import AgentChatRouter

        # Mock app state
        mock_state = MagicMock()
        mock_state.config = MagicMock()
        mock_state.config.agents = [
            {"name": "agent1", "model": "test-model", "status": "running"},
            {"name": "agent2", "model": "test-model", "status": "running"},
        ]
        mock_state.chat_messages = AsyncMock()
        mock_state.chat_messages.get_messages = AsyncMock(return_value=[])
        mock_state.chat_messages.get_thread_messages = AsyncMock(return_value=[])
        mock_state.registry = MagicMock()
        mock_state.bridge_sessions = AsyncMock()
        mock_state.bridge_sessions.enqueue_user_message = AsyncMock()
        mock_state.group_policy = None
        mock_state.trace_registry = MagicMock()
        mock_state.trace_registry.get = AsyncMock(return_value=AsyncMock())

        # Mock taosmd client
        with patch("tinyagentos.agent_chat_router.get_taosmd_client") as mock_get_client:
            mock_taosmd = AsyncMock()
            mock_get_client.return_value = mock_taosmd
            mock_taosmd.answer_context = AsyncMock(return_value=MagicMock(
                context="Assembled context",
                dropped=2,  # Non-zero to trigger trace recording
                layers={},
                total_tokens=100,
                intent="question",
                latency_ms=10.0,
            ))

            router = AgentChatRouter(mock_state)

            # Mock find_agent to return agents
            with patch("tinyagentos.agent_db.find_agent") as mock_find:
                mock_find.side_effect = lambda cfg, name: next(
                    (a for a in cfg.agents if a["name"] == name), None
                )

                # Mock resolve_guest
                with patch("tinyagentos.chat.guest_agents.resolve_guest", return_value=None):
                    # Mock _find_model_manifest
                    with patch("tinyagentos.cluster.model_resolver._find_model_manifest") as mock_manifest:
                        mock_manifest.return_value = MagicMock(context_window=8192)

                        message = {
                            "id": "msg-1",
                            "content": "Hello agent",
                            "channel_id": "chan-1",
                            "author_id": "user",
                            "created_at": datetime.now(timezone.utc).timestamp(),
                            "metadata": {"hops_since_user": 0},
                        }
                        channel = {
                            "id": "chan-1",
                            "type": "dm",
                            "members": ["user", "agent1", "agent2"],
                            "settings": {},
                        }

                        await router._route_inner(message, channel)

                        # Verify answer_context was called for each recipient
                        assert mock_taosmd.answer_context.call_count == 2
                        calls = mock_taosmd.answer_context.call_args_list
                        agents_called = {call[1]["agent"] for call in calls}
                        assert agents_called == {"agent1", "agent2"}

                        # Verify trace was recorded for dropped counts (dropped > 0)
                        trace_store = mock_state.trace_registry.get.return_value
                        trace_store.record.assert_called()
                        call_args = trace_store.record.call_args
                        args = call_args[0] if call_args else ()
                        kwargs = call_args[1] if call_args else {}
                        # event_type is first positional argument
                        assert args[0] == "memory_truncated" if args else False
                        assert kwargs.get("payload", {}).get("dropped") == 2

    @pytest.mark.asyncio
    async def test_answer_context_result_used_in_bridge_message(self):
        """Test that answer_context result is passed to bridge as context."""
        from tinyagentos.agent_chat_router import AgentChatRouter

        mock_state = MagicMock()
        mock_state.config = MagicMock()
        mock_state.config.agents = [
            {"name": "agent1", "model": "test-model", "status": "running", "framework": "other"},
        ]
        mock_state.chat_messages = AsyncMock()
        mock_state.chat_messages.get_messages = AsyncMock(return_value=[])
        mock_state.registry = MagicMock()
        mock_state.bridge_sessions = AsyncMock()
        mock_state.group_policy = None
        mock_state.trace_registry = MagicMock()
        mock_state.trace_registry.get = AsyncMock(return_value=AsyncMock())

        with patch("tinyagentos.agent_chat_router.get_taosmd_client") as mock_get_client:
            mock_taosmd = AsyncMock()
            mock_get_client.return_value = mock_taosmd
            mock_taosmd.answer_context = AsyncMock(return_value=MagicMock(
                context="Answer context from taOSmd",
                dropped=2,
                layers={"l0": 50, "l1": 100},
                total_tokens=150,
                intent="question",
                latency_ms=15.0,
            ))

            with patch("tinyagentos.agent_db.find_agent") as mock_find:
                mock_find.return_value = {"name": "agent1", "model": "test-model", "status": "running", "framework": "other"}

                with patch("tinyagentos.chat.guest_agents.resolve_guest", return_value=None):
                    with patch("tinyagentos.cluster.model_resolver._find_model_manifest") as mock_manifest:
                        mock_manifest.return_value = MagicMock(context_window=8192)
                        with patch("tinyagentos.agent_chat_router._openclaw_transport", return_value="bridge"):
                            with patch("tinyagentos.agent_manual.build_manual", return_value="Manual text"):
                                router = AgentChatRouter(mock_state)

                                message = {
                                    "id": "msg-1",
                                    "content": "Hello",
                                    "channel_id": "chan-1",
                                    "author_id": "user",
                                    "created_at": datetime.now(timezone.utc).timestamp(),
                                    "metadata": {"hops_since_user": 0},
                                }
                                channel = {
                                    "id": "chan-1",
                                    "type": "dm",
                                    "members": ["user", "agent1"],
                                    "settings": {},
                                }

                                await router._route_inner(message, channel)

                                # Verify bridge.enqueue_user_message was called with context from answer_context
                                mock_state.bridge_sessions.enqueue_user_message.assert_awaited_once()
                                call_args = mock_state.bridge_sessions.enqueue_user_message.call_args
                                enqueued_msg = call_args[0][1]
                                assert "context" in enqueued_msg
                                # Context should include the answer_context result
                                context = enqueued_msg["context"]
                                assert len(context) == 1
                                assert context[0]["role"] == "system"
                                assert "Answer context from taOSmd" in context[0]["content"]


class TestSkillMemorySearchAnswerContext:
    """Tests that memory_search skill uses answer_context."""

    @pytest.mark.asyncio
    async def test_skill_memory_search_calls_answer_context(self):
        """Test that _skill_memory_search uses answer_context."""
        from tinyagentos.routes.skill_exec import _skill_memory_search

        mock_request = MagicMock()
        mock_request.state.agent_name = "test-agent"
        mock_request.app.state.config = MagicMock()
        mock_request.app.state.config.agents = [
            {"name": "test-agent", "model": "test-model"},
        ]
        mock_request.app.state.registry = MagicMock()

        with patch("tinyagentos.routes.skill_exec.get_taosmd_client") as mock_get_client:
            mock_taosmd = AsyncMock()
            mock_get_client.return_value = mock_taosmd
            mock_taosmd.answer_context = AsyncMock(return_value=MagicMock(
                context="Skill answer context",
                dropped=1,
                layers={"l0": 50, "l1": 100},
                total_tokens=150,
                intent="question",
                latency_ms=20.0,
            ))

            with patch("tinyagentos.routes.skill_exec.calculate_agent_budget", return_value=4000):
                result = await _skill_memory_search(
                    {"query": "test query", "limit": 10},
                    mock_request,
                )

                assert result["status"] == "ok"
                assert result["context"] == "Skill answer context"
                assert result["dropped"] == 1
                assert result["layers"] == {"l0": 50, "l1": 100}
                assert result["total_tokens"] == 150
                assert result["intent"] == "question"

                mock_taosmd.answer_context.assert_awaited_once()
                call_args = mock_taosmd.answer_context.call_args
                assert call_args[1]["query"] == "test query"
                assert call_args[1]["agent"] == "test-agent"
                assert call_args[1]["budget_tokens"] == 4000

    @pytest.mark.asyncio
    async def test_skill_memory_search_fails_without_agent_identity(self):
        """Test that skill fails when no agent identity in credentials."""
        from tinyagentos.routes.skill_exec import _skill_memory_search

        mock_request = MagicMock()
        mock_request.state.agent_name = None  # No agent identity

        result = await _skill_memory_search(
            {"query": "test query", "limit": 10},
            mock_request,
        )

        assert result["status"] == "error"
        assert "agent identity not found" in result["error"]


class TestUserMemoryAgentSearchAnswerContext:
    """Tests that user_memory agent-search uses answer_context."""

    @pytest.mark.asyncio
    async def test_agent_search_uses_answer_context(self):
        """Test that agent_search endpoint uses answer_context."""
        from tinyagentos.routes.user_memory import agent_search

        mock_request = MagicMock()
        mock_request.app.state.config = MagicMock()
        mock_request.app.state.config.agents = [
            {"name": "test-agent", "model": "test-model", "can_read_user_memory": True},
        ]
        mock_request.app.state.registry = MagicMock()

        with patch("tinyagentos.routes.user_memory.get_taosmd_client") as mock_get_client:
            mock_taosmd = AsyncMock()
            mock_get_client.return_value = mock_taosmd
            mock_taosmd.answer_context = AsyncMock(return_value=MagicMock(
                context="User memory context",
                dropped=0,
                layers={},
                total_tokens=100,
                intent="question",
                latency_ms=5.0,
            ))

            with patch("tinyagentos.routes.user_memory.calculate_agent_budget", return_value=4000):
                # Call the endpoint function directly
                response = await agent_search(
                    mock_request,
                    q="test query",
                    agent_name="test-agent",
                    limit=10,
                )

                assert response.status_code == 200
                # Parse JSON response
                import json
                body = json.loads(response.body.decode())
                assert body["context"] == "User memory context"
                assert body["dropped"] == 0
                assert body["agent"] == "test-agent"

                mock_taosmd.answer_context.assert_awaited_once()


class TestRoutesMemorySearchAnswerContext:
    """Tests that routes/memory search uses answer_context for agents."""

    @pytest.mark.asyncio
    async def test_memory_search_uses_answer_context_for_agent_caller(self):
        """Test that memory_search uses answer_context when called by agent."""
        from tinyagentos.routes.memory import memory_search
        from tinyagentos.routes.memory import SearchRequest

        mock_request = MagicMock()
        # Simulate bound agent local token
        mock_request.state.via = "local_token"
        mock_request.state.agent_name = "test-agent"
        mock_request.app.state.config = MagicMock()
        mock_request.app.state.config.agents = [
            {"name": "test-agent", "model": "test-model"},
        ]
        mock_request.app.state.registry = MagicMock()

        with patch("tinyagentos.routes.memory.get_taosmd_client") as mock_get_client:
            mock_taosmd = AsyncMock()
            mock_get_client.return_value = mock_taosmd
            mock_taosmd.answer_context = AsyncMock(return_value=MagicMock(
                context="Agent memory context",
                dropped=3,
                layers={"l0": 50, "l1": 100, "l2": 200},
                total_tokens=350,
                intent="question",
                latency_ms=25.0,
            ))

            with patch("tinyagentos.routes.memory.calculate_agent_budget", return_value=4000):
                body = SearchRequest(query="test query", agent="test-agent", limit=10)
                result = await memory_search(mock_request, body)

                assert "context" in result
                assert result["context"] == "Agent memory context"
                assert result["dropped"] == 3
                assert result["layers"] == {"l0": 50, "l1": 100, "l2": 200}
                assert result["total_tokens"] == 350

                mock_taosmd.answer_context.assert_awaited_once()

    # Note: Human caller test is complex due to auth flow. The integration test
    # in test_routes_memory.py covers raw qmd search for human callers.


class TestDroppedCountsInTrace:
    """Tests that dropped counts are surfaced in agent trace."""

    @pytest.mark.asyncio
    async def test_dropped_count_recorded_in_trace(self):
        """Test that answer_context dropped count is recorded in trace."""
        from tinyagentos.agent_chat_router import AgentChatRouter

        mock_state = MagicMock()
        mock_state.config = MagicMock()
        mock_state.config.agents = [
            {"name": "agent1", "model": "test-model", "status": "running"},
        ]
        mock_state.chat_messages = AsyncMock()
        mock_state.chat_messages.get_messages = AsyncMock(return_value=[])
        mock_state.registry = MagicMock()
        mock_state.bridge_sessions = AsyncMock()
        mock_state.bridge_sessions.enqueue_user_message = AsyncMock()
        mock_state.group_policy = None
        mock_trace_registry = MagicMock()
        mock_trace_store = AsyncMock()
        mock_trace_registry.get = AsyncMock(return_value=mock_trace_store)
        mock_state.trace_registry = mock_trace_registry

        with patch("tinyagentos.agent_chat_router.get_taosmd_client") as mock_get_client:
            mock_taosmd = AsyncMock()
            mock_get_client.return_value = mock_taosmd
            # Return result with dropped items
            mock_taosmd.answer_context = AsyncMock(return_value=MagicMock(
                context="Context with dropped items",
                dropped=5,  # 5 items dropped
                layers={"l0": 50, "l1": 100, "l2": 200},
                total_tokens=350,
                intent="question",
                latency_ms=15.0,
            ))

            with patch("tinyagentos.agent_db.find_agent") as mock_find:
                mock_find.return_value = {"name": "agent1", "model": "test-model", "status": "running"}

                with patch("tinyagentos.chat.guest_agents.resolve_guest", return_value=None):
                    with patch("tinyagentos.cluster.model_resolver._find_model_manifest") as mock_manifest:
                        mock_manifest.return_value = MagicMock(context_window=8192)
                        with patch("tinyagentos.agent_chat_router._openclaw_transport", return_value="bridge"):
                            with patch("tinyagentos.agent_manual.build_manual", return_value="Manual"):
                                router = AgentChatRouter(mock_state)

                                message = {
                                    "id": "msg-1",
                                    "content": "Hello",
                                    "channel_id": "chan-1",
                                    "author_id": "user",
                                    "created_at": datetime.now(timezone.utc).timestamp(),
                                    "metadata": {"hops_since_user": 0},
                                }
                                channel = {
                                    "id": "chan-1",
                                    "type": "dm",
                                    "members": ["user", "agent1"],
                                    "settings": {},
                                }

                                await router._route_inner(message, channel)

                                # Verify trace was recorded with dropped count
                                mock_trace_store.record.assert_called()
                                call_args = mock_trace_store.record.call_args
                                args = call_args[0] if call_args else ()
                                kwargs = call_args[1] if call_args else {}
                                # event_type is first positional argument
                                assert args[0] == "memory_truncated" if args else False
                                assert kwargs.get("payload", {}).get("dropped") == 5
                                # history_token_budget(8192) = max(512, 8192 - 10000 - 1024) = 512
                                assert kwargs.get("payload", {}).get("budget_tokens") == 512


if __name__ == "__main__":
    pytest.main([__file__, "-v"])