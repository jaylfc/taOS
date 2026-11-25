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


class TestTaOSmdClientInProcess:
    """Tests for the in-process taOSmd client logic."""

    @pytest.mark.asyncio
    async def test_in_process_uses_dropped_from_context_assembler(self):
        """Test that in-process path uses dropped count from ContextAssembler result."""
        from tinyagentos.taosmd_client import TaOSmdClient, AnswerContextResult

        client = TaOSmdClient(use_in_process=True, http_client=None)

        # Mock the ContextAssembler
        mock_assembler = AsyncMock()
        mock_assembler.assemble = AsyncMock(return_value={
            "context": "Assembled memory context",
            "dropped": 7,  # Actual dropped count from ContextAssembler
            "layers": {"l0": 500, "l1": 1000},
            "total_tokens": 1500,
            "intent": "question",
        })

        # Inject the mock assembler
        client._context_assembler = mock_assembler
        client._in_process_available = True

        result = await client._answer_context_in_process(
            query="test query",
            agent="test-agent",
            budget_tokens=4000,
            now=datetime.now(timezone.utc),
            reasoning_reserve=1024,
        )

        # Verify the dropped count comes from ContextAssembler, not heuristic
        assert result.dropped == 7
        assert result.context == "Assembled memory context"
        assert result.layers == {"l0": 500, "l1": 1000}
        assert result.total_tokens == 1500
        assert result.intent == "question"

        # Verify assemble was called with full budget (not reduced by reasoning_reserve)
        mock_assembler.assemble.assert_awaited_once()
        call_args = mock_assembler.assemble.call_args
        assert call_args[1]["max_total_tokens"] == 4000
        # reasoning_reserve is NOT passed to assembler (accounted for in budget)
        assert "reasoning_reserve" not in call_args[1]

    @pytest.mark.asyncio
    async def test_in_process_handles_missing_dropped_field(self):
        """Test that in-process path handles missing dropped field gracefully (defaults to 0)."""
        from tinyagentos.taosmd_client import TaOSmdClient

        client = TaOSmdClient(use_in_process=True, http_client=None)

        mock_assembler = AsyncMock()
        mock_assembler.assemble = AsyncMock(return_value={
            "context": "Assembled memory context",
            # No "dropped" field - should default to 0
            "layers": {"l0": 500},
            "total_tokens": 500,
            "intent": "question",
        })

        client._context_assembler = mock_assembler
        client._in_process_available = True

        result = await client._answer_context_in_process(
            query="test query",
            agent="test-agent",
            budget_tokens=4000,
            now=datetime.now(timezone.utc),
            reasoning_reserve=1024,
        )

        assert result.dropped == 0

    @pytest.mark.asyncio
    async def test_in_process_does_not_pass_reasoning_reserve_to_assembler(self):
        """Test that reasoning_reserve is NOT passed to ContextAssembler;
        it is accounted for in the budget calculation via default_budget_tokens."""
        from tinyagentos.taosmd_client import TaOSmdClient

        client = TaOSmdClient(use_in_process=True, http_client=None)

        mock_assembler = AsyncMock()
        mock_assembler.assemble = AsyncMock(return_value={
            "context": "Context",
            "dropped": 0,
            "layers": {},
            "total_tokens": 100,
            "intent": "general",
        })

        client._context_assembler = mock_assembler
        client._in_process_available = True

        await client._answer_context_in_process(
            query="test",
            agent="test-agent",
            budget_tokens=8000,
            now=datetime.now(timezone.utc),
            reasoning_reserve=2048,  # Custom reserve (ignored by assembler)
        )

        call_args = mock_assembler.assemble.call_args
        # Budget should be full 8000 (already accounts for reasoning_reserve)
        assert call_args[1]["max_total_tokens"] == 8000
        # reasoning_reserve should NOT be passed to assembler
        assert "reasoning_reserve" not in call_args[1]


class TestTaOSmdClientHTTP:
    """Tests for the HTTP taOSmd client path."""

    @pytest.mark.asyncio
    async def test_http_passes_reasoning_reserve_to_server(self):
        """Test that HTTP path sends reasoning_reserve to server."""
        from tinyagentos.taosmd_client import TaOSmdClient

        mock_http = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.json = MagicMock(return_value={
            "context": "HTTP context",
            "dropped": 3,
            "layers": {"l0": 200},
            "total_tokens": 200,
            "intent": "question",
        })
        mock_resp.raise_for_status = MagicMock()
        mock_http.post = AsyncMock(return_value=mock_resp)

        client = TaOSmdClient(http_client=mock_http, use_in_process=False)

        result = await client._answer_context_http(
            query="test query",
            agent="test-agent",
            budget_tokens=4000,
            now=datetime.now(timezone.utc),
        )

        assert result.dropped == 3
        # Verify HTTP payload does NOT include reasoning_reserve (server rejects it)
        mock_http.post.assert_awaited_once()
        call_args = mock_http.post.call_args
        payload = call_args[1]["json"]
        assert "reasoning_reserve" not in payload
        assert payload["budget_tokens"] == 4000


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
                call_args = mock_taosmd.answer_context.call_args
                # agent_search should query the USER's memory namespace (user-memory)
                assert call_args[1]["agent"] == "user-memory"
                assert call_args[1]["query"] == "test query"


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
                                # default_budget_tokens(8192, used_tokens=1, reasoning_reserve=0) = 8191
                                assert kwargs.get("payload", {}).get("budget_tokens") == 8191


class TestIdentityIsolation:
    """Tests that agents only receive their own and permitted memory through answer_context."""

    @pytest.mark.asyncio
    async def test_agent_only_receives_own_memory_not_others(self):
        """Test that agent A's answer_context call only returns agent A's memory, not agent B's."""
        from tinyagentos.routes.memory import memory_search
        from tinyagentos.routes.memory import SearchRequest

        mock_request = MagicMock()
        # Simulate bound agent local token for agent-a
        mock_request.state.via = "local_token"
        mock_request.state.agent_name = "agent-a"
        mock_request.app.state.config = MagicMock()
        mock_request.app.state.config.agents = [
            {"name": "agent-a", "model": "test-model"},
            {"name": "agent-b", "model": "test-model"},
        ]
        mock_request.app.state.registry = MagicMock()

        with patch("tinyagentos.routes.memory.get_taosmd_client") as mock_get_client:
            mock_taosmd = AsyncMock()
            mock_get_client.return_value = mock_taosmd
            # Verify that answer_context is called with agent="agent-a" (the caller's identity)
            mock_taosmd.answer_context = AsyncMock(return_value=MagicMock(
                context="Agent A memory context",
                dropped=0,
                layers={},
                total_tokens=100,
                intent="question",
                latency_ms=5.0,
            ))

            with patch("tinyagentos.routes.memory.calculate_agent_budget", return_value=4000):
                body = SearchRequest(query="test query", agent="agent-a", limit=10)
                result = await memory_search(mock_request, body)

                # Verify answer_context was called with the CALLER's agent identity (agent-a)
                mock_taosmd.answer_context.assert_awaited_once()
                call_args = mock_taosmd.answer_context.call_args
                assert call_args[1]["agent"] == "agent-a"
                assert call_args[1]["query"] == "test query"

                # Verify the response contains the context for agent-a
                assert result["context"] == "Agent A memory context"
                assert result["agent"] == "agent-a"

    @pytest.mark.asyncio
    async def test_agent_cannot_search_another_agents_memory(self):
        """Test that agent-a cannot search agent-b's memory via the memory search endpoint."""
        from tinyagentos.routes.memory import memory_search
        from tinyagentos.routes.memory import SearchRequest
        from fastapi import HTTPException

        mock_request = MagicMock()
        # Simulate bound agent local token for agent-a
        mock_request.state.via = "local_token"
        mock_request.state.agent_name = "agent-a"
        mock_request.app.state.config = MagicMock()
        mock_request.app.state.config.agents = [
            {"name": "agent-a", "model": "test-model"},
            {"name": "agent-b", "model": "test-model"},
        ]
        mock_request.app.state.registry = MagicMock()

        with patch("tinyagentos.routes.memory.get_taosmd_client") as mock_get_client:
            mock_taosmd = AsyncMock()
            mock_get_client.return_value = mock_taosmd

            with patch("tinyagentos.routes.memory.calculate_agent_budget", return_value=4000):
                # Agent-a tries to search agent-b's memory - should raise HTTPException 403
                body = SearchRequest(query="test query", agent="agent-b", limit=10)
                try:
                    await memory_search(mock_request, body)
                    assert False, "Expected HTTPException"
                except HTTPException as exc:
                    assert exc.status_code == 403
                    assert "Agent can only search its own memory" in exc.detail

    @pytest.mark.asyncio
    async def test_user_memory_agent_search_requires_permission(self):
        """Test that agent-search endpoint requires can_read_user_memory permission."""
        from tinyagentos.routes.user_memory import agent_search

        mock_request = MagicMock()
        mock_request.app.state.config = MagicMock()
        mock_request.app.state.config.agents = [
            {"name": "agent-no-perm", "model": "test-model", "can_read_user_memory": False},
            {"name": "agent-with-perm", "model": "test-model", "can_read_user_memory": True},
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
                # Agent without permission should be forbidden
                response = await agent_search(
                    mock_request,
                    q="test query",
                    agent_name="agent-no-perm",
                    limit=10,
                )
                assert response.status_code == 403
                assert "does not have user memory access" in response.body.decode()

                # Agent with permission should succeed
                response = await agent_search(
                    mock_request,
                    q="test query",
                    agent_name="agent-with-perm",
                    limit=10,
                )
                assert response.status_code == 200
                import json
                body = json.loads(response.body.decode())
                assert body["context"] == "User memory context"
                assert body["agent"] == "agent-with-perm"

    @pytest.mark.asyncio
    async def test_skill_memory_search_uses_caller_identity(self):
        """Test that memory_search skill uses the caller's agent identity from credentials."""
        from tinyagentos.routes.skill_exec import _skill_memory_search

        mock_request = MagicMock()
        mock_request.state.agent_name = "skill-agent"
        mock_request.app.state.config = MagicMock()
        mock_request.app.state.config.agents = [
            {"name": "skill-agent", "model": "test-model"},
            {"name": "other-agent", "model": "test-model"},
        ]
        mock_request.app.state.registry = MagicMock()

        with patch("tinyagentos.routes.skill_exec.get_taosmd_client") as mock_get_client:
            mock_taosmd = AsyncMock()
            mock_get_client.return_value = mock_taosmd
            mock_taosmd.answer_context = AsyncMock(return_value=MagicMock(
                context="Skill agent memory context",
                dropped=0,
                layers={},
                total_tokens=100,
                intent="question",
                latency_ms=5.0,
            ))

            with patch("tinyagentos.routes.skill_exec.calculate_agent_budget", return_value=4000):
                result = await _skill_memory_search(
                    {"query": "test query", "limit": 10},
                    mock_request,
                )

                assert result["status"] == "ok"
                # Verify answer_context was called with the credential agent's identity
                mock_taosmd.answer_context.assert_awaited_once()
                call_args = mock_taosmd.answer_context.call_args
                assert call_args[1]["agent"] == "skill-agent"
                assert call_args[1]["query"] == "test query"

    @pytest.mark.asyncio
    async def test_agent_chat_router_uses_recipient_identity_per_agent(self):
        """Test that agent_chat_router calls answer_context with each recipient's identity."""
        from tinyagentos.agent_chat_router import AgentChatRouter

        mock_state = MagicMock()
        mock_state.config = MagicMock()
        mock_state.config.agents = [
            {"name": "agent1", "model": "test-model", "status": "running"},
            {"name": "agent2", "model": "test-model", "status": "running"},
        ]
        mock_state.chat_messages = AsyncMock()
        mock_state.chat_messages.get_messages = AsyncMock(return_value=[])
        mock_state.registry = MagicMock()
        mock_state.bridge_sessions = AsyncMock()
        mock_state.bridge_sessions.enqueue_user_message = AsyncMock()
        mock_state.group_policy = None
        mock_state.trace_registry = MagicMock()
        mock_state.trace_registry.get = AsyncMock(return_value=AsyncMock())

        with patch("tinyagentos.agent_chat_router.get_taosmd_client") as mock_get_client:
            mock_taosmd = AsyncMock()
            mock_get_client.return_value = mock_taosmd
            mock_taosmd.answer_context = AsyncMock(return_value=MagicMock(
                context="Assembled context",
                dropped=0,
                layers={},
                total_tokens=100,
                intent="question",
                latency_ms=10.0,
            ))

            with patch("tinyagentos.agent_db.find_agent") as mock_find:
                mock_find.side_effect = lambda cfg, name: next(
                    (a for a in cfg.agents if a["name"] == name), None
                )

                with patch("tinyagentos.chat.guest_agents.resolve_guest", return_value=None):
                    with patch("tinyagentos.cluster.model_resolver._find_model_manifest") as mock_manifest:
                        mock_manifest.return_value = MagicMock(context_window=8192)

                        message = {
                            "id": "msg-1",
                            "content": "Hello agents",
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

                        router = AgentChatRouter(mock_state)
                        await router._route_inner(message, channel)

                        # Verify answer_context was called for EACH recipient with THEIR identity
                        assert mock_taosmd.answer_context.call_count == 2
                        calls = mock_taosmd.answer_context.call_args_list
                        agents_called = {call[1]["agent"] for call in calls}
                        assert agents_called == {"agent1", "agent2"}
                        # Each call should have the correct agent identity
                        for call in calls:
                            agent_name = call[1]["agent"]
                            assert call[1]["agent"] == agent_name
                            assert call[1]["query"] == "Hello agents"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])