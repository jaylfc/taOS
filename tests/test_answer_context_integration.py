"""RED tests for answer_context integration.

These tests verify that agent memory context paths route through taOSmd's
answer_context function. They FAIL on current code (raw search) and PASS
when the integration is complete.
"""
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tinyagentos.taosmd_client import AnswerContextResult


class TestAnswerContextIntegration:
    """Tests for the answer_context integration across all call sites."""

    @pytest.mark.asyncio
    async def test_agent_chat_router_uses_answer_context_per_agent(self):
        """agent_chat_router._route_inner must call answer_context per recipient agent."""
        from tinyagentos.agent_chat_router import AgentChatRouter

        bridge = MagicMock()
        bridge.enqueue_user_message = AsyncMock()

        state = MagicMock()
        state.config = MagicMock()
        state.config.agents = [
            {"name": "agent-a", "model": "model-1", "registry_canonical_id": "canonical-a", "status": "running"},
            {"name": "agent-b", "model": "model-2", "registry_canonical_id": "canonical-b", "status": "running"},
        ]
        state.chat_messages = MagicMock()
        state.chat_messages.get_messages = AsyncMock(return_value=[])
        state.chat_messages.set_metadata = AsyncMock()
        state.chat_messages.send_message = AsyncMock(return_value={"id": "sys-msg"})
        state.chat_channels = MagicMock()
        state.chat_channels.update_last_message_at = AsyncMock()
        state.chat_hub = MagicMock()
        state.chat_hub.broadcast = AsyncMock()
        state.chat_hub.next_seq = MagicMock(return_value=1)
        state.bridge_sessions = bridge
        state.registry = MagicMock()
        state.registry.get = MagicMock(return_value=MagicMock(context_window=8192))
        state.http_client = MagicMock()
        state.taosmd_url = "http://localhost:8085"
        state.taosmd_in_process = False

        router = AgentChatRouter(state)
        message = {
            "id": "m1", "channel_id": "c1", "author_id": "user",
            "author_type": "user", "content": "test query", "created_at": 1.0,
        }
        channel = {
            "id": "c1", "type": "dm", "members": ["user", "agent-a", "agent-b"],
            "settings": {"response_mode": "lively", "max_hops": 3, "muted": []},
        }

        # Mock the taosmd client's answer_context method
        mock_client = MagicMock()
        mock_client.answer_context = AsyncMock(return_value=AnswerContextResult(
            context="agent memory context",
            dropped=2,
            budget_tokens=4000,
            used_tokens=3000,
            layers={"l0": 100, "l1": 200},
            depth="standard",
            intent="general",
            latency_ms=50.0,
        ))
        router._taosmd_client = mock_client

        await router._route(message, channel)

        # Should call answer_context once PER agent (2 agents = 2 calls)
        assert mock_client.answer_context.call_count == 2, (
            f"Expected 2 calls to answer_context (one per agent), got {mock_client.answer_context.call_count}"
        )

        # Verify calls use canonical identities
        call_args_list = mock_client.answer_context.call_args_list
        agents_called = [call.kwargs.get("agent") for call in call_args_list]
        assert "canonical-a" in agents_called, f"Expected canonical-a in calls, got {agents_called}"
        assert "canonical-b" in agents_called, f"Expected canonical-b in calls, got {agents_called}"

    @pytest.mark.asyncio
    async def test_agent_chat_router_answer_context_includes_dropped_in_trace(self):
        """answer_context dropped count must be surfaced in agent trace via enqueue_user_message."""
        from tinyagentos.agent_chat_router import AgentChatRouter

        bridge = MagicMock()
        bridge.enqueue_user_message = AsyncMock()

        state = MagicMock()
        state.config = MagicMock()
        state.config.agents = [
            {"name": "test-agent", "model": "test-model", "registry_canonical_id": "canonical-test", "status": "running"},
        ]
        state.chat_messages = MagicMock()
        state.chat_messages.get_messages = AsyncMock(return_value=[])
        state.chat_messages.set_metadata = AsyncMock()
        state.chat_messages.send_message = AsyncMock(return_value={"id": "sys-msg"})
        state.chat_channels = MagicMock()
        state.chat_channels.update_last_message_at = AsyncMock()
        state.chat_hub = MagicMock()
        state.chat_hub.broadcast = AsyncMock()
        state.chat_hub.next_seq = MagicMock(return_value=1)
        state.bridge_sessions = bridge
        state.registry = MagicMock()
        state.registry.get = MagicMock(return_value=MagicMock(context_window=8192))
        state.http_client = MagicMock()
        state.taosmd_url = "http://localhost:8085"
        state.taosmd_in_process = False

        router = AgentChatRouter(state)
        message = {
            "id": "m1", "channel_id": "c1", "author_id": "user",
            "author_type": "user", "content": "test query", "created_at": 1.0,
        }
        channel = {
            "id": "c1", "type": "dm", "members": ["user", "test-agent"],
            "settings": {"response_mode": "lively", "max_hops": 3, "muted": []},
        }

        mock_client = MagicMock()
        mock_client.answer_context = AsyncMock(return_value=AnswerContextResult(
            context="agent memory context",
            dropped=5,  # Specific dropped count to verify
            budget_tokens=4000,
            used_tokens=3000,
            layers={"l0": 100},
            depth="standard",
            intent="general",
            latency_ms=50.0,
        ))
        router._taosmd_client = mock_client

        await router._route(message, channel)

        # Verify enqueue_user_message was called with answer_context_meta including dropped
        bridge.enqueue_user_message.assert_awaited_once()
        call_args = bridge.enqueue_user_message.call_args
        enqueued_msg = call_args.kwargs if call_args.kwargs else call_args.args[1]
        assert "answer_context_meta" in enqueued_msg
        assert enqueued_msg["answer_context_meta"]["answer_context_dropped"] == 5

    @pytest.mark.asyncio
    async def test_memory_search_endpoint_uses_answer_context_for_agent_callers(self):
        """routes/memory.py memory_search must use answer_context for agent callers (no raw qmd fallback)."""
        from tinyagentos.routes.memory import memory_search, SearchRequest
        from tinyagentos.taosmd_client import AnswerContextResult

        request = MagicMock()
        request.app.state.http_client = MagicMock()
        request.app.state.http_client.get = AsyncMock()
        request.app.state.http_client.post = AsyncMock()
        request.app.state.agent_memory_dir = MagicMock()
        request.app.state.config = MagicMock()
        request.app.state.config.agents = [
            {"name": "test-agent", "model": "test-model", "registry_canonical_id": "canonical-test", "status": "running"},
        ]

        # Mock taosmd_client on app.state
        mock_client = AsyncMock()
        mock_client.answer_context = AsyncMock(return_value=AnswerContextResult(
            context="test context from answer_context",
            dropped=2,
            budget_tokens=4000,
            used_tokens=3000,
            layers={"l0": 100, "l1": 200},
            depth="standard",
            intent="general",
            latency_ms=50.0,
        ))
        request.app.state.taosmd_client = mock_client

        # Mock agent bound to local token
        request.state.via = "local_token"
        request.state.agent_name = "test-agent"

        body = SearchRequest(query="test query", agent="test-agent", mode="semantic", limit=10)

        # This should call answer_context via client, NOT _qmd_vsearch
        with patch("tinyagentos.routes.memory._qmd_vsearch") as mock_vsearch:
            mock_vsearch.return_value = []
            result = await memory_search(request, body)

        # Should NOT call _qmd_vsearch (raw qmd) for agent callers
        mock_vsearch.assert_not_called()

        # Should call answer_context
        mock_client.answer_context.assert_awaited_once()
        call_kwargs = mock_client.answer_context.call_args.kwargs
        assert call_kwargs["agent"] == "canonical-test"  # Uses canonical identity
        assert call_kwargs["budget_tokens"] > 0

        # Result should contain answer_context metadata
        assert result["answer_context_meta"]["dropped"] == 2
        assert result["results"][0]["metadata"]["answer_context"] is True

    @pytest.mark.asyncio
    async def test_user_memory_agent_search_uses_answer_context_no_fallback(self):
        """routes/user_memory.py agent_search must use answer_context, no SQLite fallback."""
        from tinyagentos.routes.user_memory import agent_search
        from tinyagentos.taosmd_client import AnswerContextResult

        request = MagicMock()
        request.app.state.config = MagicMock()
        request.app.state.config.agents = [
            {"name": "test-agent", "can_read_user_memory": True, "model": "test-model", "registry_canonical_id": "canonical-test", "status": "running"},
        ]
        request.app.state.user_memory = MagicMock()
        request.app.state.user_memory.search = AsyncMock(return_value=[])

        # Mock taosmd_client on app.state
        mock_client = AsyncMock()
        mock_client.answer_context = AsyncMock(return_value=AnswerContextResult(
            context="test context from answer_context",
            dropped=1,
            budget_tokens=4000,
            used_tokens=3500,
            layers={"l0": 100},
            depth="standard",
            intent="general",
            latency_ms=30.0,
        ))
        request.app.state.taosmd_client = mock_client

        # This should call answer_context, NOT user_memory.search (SQLite)
        result = await agent_search(request, q="test query", agent_name="test-agent", limit=10)

        # Should NOT call user_memory.search (SQLite fallback removed)
        request.app.state.user_memory.search.assert_not_called()

        # Should call answer_context
        mock_client.answer_context.assert_awaited_once()
        call_kwargs = mock_client.answer_context.call_args.kwargs
        assert call_kwargs["agent"] == "test-agent"
        assert call_kwargs["budget_tokens"] > 0

        # Result should contain answer_context metadata
        assert result.body is not None  # JSONResponse
        body = json.loads(result.body.decode())
        assert body["answer_context_meta"]["dropped"] == 1
        assert body["results"][0]["metadata"]["answer_context"] is True

    @pytest.mark.asyncio
    async def test_answer_context_returns_dropped_count(self):
        """answer_context response must include dropped item count for trace visibility."""
        from tinyagentos.taosmd_client import TaOSmdClient, AnswerContextResult

        # This test defines the expected contract for answer_context
        # The client should return dropped count from both HTTP and in-process modes

        # Test HTTP mode returns dropped from response
        http_client = AsyncMock()
        http_client.post = AsyncMock()
        http_response = MagicMock()
        http_response.json = MagicMock(return_value={
            "context": "test",
            "dropped": 3,
            "budget_tokens": 4000,
            "used_tokens": 3800,
            "layers": {"l0": 100},
            "depth": "standard",
            "intent": "general",
            "latency_ms": 50.0,
        })
        http_response.raise_for_status = MagicMock()
        http_client.post.return_value = http_response

        client = TaOSmdClient(http_client=http_client, base_url="http://localhost:8085", in_process=False)
        result = await client.answer_context(
            query="test", agent="test-agent", budget_tokens=4000,
            reasoning_reserve=1024, depth="standard",
        )

        assert result.dropped == 3, f"HTTP mode should return dropped=3, got {result.dropped}"

    @pytest.mark.asyncio
    async def test_answer_context_in_process_returns_dropped_count(self):
        """In-process answer_context must return dropped count from ContextAssembler."""
        from tinyagentos.taosmd_client import TaOSmdClient, AnswerContextResult

        # Mock the ContextAssembler
        mock_assembler = MagicMock()
        mock_assembler.assemble = AsyncMock(return_value={
            "context": "in-process context",
            "dropped": 4,
            "total_tokens": 3500,
            "layers": {"l0": 100, "l1": 200},
            "depth": "standard",
            "intent": "general",
            "latency_ms": 25.0,
        })

        client = TaOSmdClient(http_client=None, base_url="http://localhost:8085", in_process=True)
        client._context_assembler = mock_assembler
        client._in_process = True

        result = await client.answer_context(
            query="test", agent="test-agent", budget_tokens=4000,
            reasoning_reserve=1024, depth="standard",
        )

        assert result.dropped == 4, f"In-process mode should return dropped=4, got {result.dropped}"
        # Verify reasoning_reserve was passed to assembler, not subtracted locally
        mock_assembler.assemble.assert_awaited_once()
        call_kwargs = mock_assembler.assemble.call_args.kwargs
        assert call_kwargs["max_total_tokens"] == 4000  # Full budget passed, not budget - reserve
        assert call_kwargs["reasoning_reserve"] == 1024  # Reserve passed separately

    @pytest.mark.asyncio
    async def test_answer_context_respects_token_budget_share(self):
        """answer_context budget must be a share of model's context window (not whole window)."""
        from tinyagentos.routes.memory import memory_search, SearchRequest
        from tinyagentos.taosmd_client import AnswerContextResult

        request = MagicMock()
        request.app.state.http_client = MagicMock()
        request.app.state.agent_memory_dir = MagicMock()
        request.app.state.config = MagicMock()
        # Agent with 8192 context window -> budget should be ~4096 (50%)
        request.app.state.config.agents = [
            {"name": "test-agent", "model": "large-model", "registry_canonical_id": "canonical-test"},
        ]
        request.app.state.registry = MagicMock()

        mock_client = AsyncMock()
        mock_client.answer_context = AsyncMock(return_value=AnswerContextResult(
            context="test context",
            dropped=0,
            budget_tokens=4096,  # Should be ~50% of 8192
            used_tokens=3000,
            layers={},
            depth="standard",
            intent="general",
            latency_ms=50.0,
        ))
        request.app.state.taosmd_client = mock_client

        # Mock model resolver to return 8192 context window
        with patch("tinyagentos.cluster.model_resolver._find_model_manifest") as mock_manifest:
            mock_manifest.return_value = MagicMock(context_window=8192)

            request.state.via = "local_token"
            request.state.agent_name = "test-agent"

            body = SearchRequest(query="test query", agent="test-agent", mode="semantic", limit=10)
            result = await memory_search(request, body)

        # Verify budget passed to answer_context is ~50% of context window
        mock_client.answer_context.assert_awaited_once()
        call_kwargs = mock_client.answer_context.call_args.kwargs
        assert call_kwargs["budget_tokens"] == 4096, (
            f"Budget should be 50% of 8192 = 4096, got {call_kwargs['budget_tokens']}"
        )

    @pytest.mark.asyncio
    async def test_answer_context_identity_isolation(self):
        """An agent must only receive its own and permitted memory through answer_context."""
        from tinyagentos.taosmd_client import TaOSmdClient, AnswerContextResult

        # This test verifies that the agent identity passed to answer_context
        # is the canonical identity (registry_canonical_id), not just the config name.
        # And that different agents get different contexts.

        http_client = AsyncMock()
        http_client.post = AsyncMock()

        # Track which agents were queried
        called_agents = []

        async def mock_post(url, json, timeout):
            called_agents.append(json["agent"])
            resp = MagicMock()
            resp.json = MagicMock(return_value={
                "context": f"context for {json['agent']}",
                "dropped": 0,
                "budget_tokens": 4000,
                "used_tokens": 1000,
                "layers": {},
                "depth": "standard",
                "intent": "general",
                "latency_ms": 10.0,
            })
            resp.raise_for_status = MagicMock()
            return resp

        http_client.post.side_effect = mock_post

        client = TaOSmdClient(http_client=http_client, base_url="http://localhost:8085", in_process=False)

        # Call for agent-a with canonical identity
        await client.answer_context(
            query="test", agent="canonical-agent-a", budget_tokens=4000,
            reasoning_reserve=1024, depth="standard",
        )

        # Call for agent-b with canonical identity
        await client.answer_context(
            query="test", agent="canonical-agent-b", budget_tokens=4000,
            reasoning_reserve=1024, depth="standard",
        )

        # Verify each call used the correct canonical identity
        assert called_agents == ["canonical-agent-a", "canonical-agent-b"], (
            f"Expected canonical identities, got {called_agents}"
        )

        # Verify agent-a cannot see agent-b's context by checking
        # that separate calls are made with separate identities
        assert len(called_agents) == 2

    @pytest.mark.asyncio
    async def test_agent_chat_router_different_agents_get_separate_contexts(self):
        """Each agent in a multi-recipient message must get its own answer_context call."""
        from tinyagentos.agent_chat_router import AgentChatRouter

        bridge = MagicMock()
        bridge.enqueue_user_message = AsyncMock()

        state = MagicMock()
        state.config = MagicMock()
        state.config.agents = [
            {"name": "agent-a", "model": "model-1", "registry_canonical_id": "canonical-a", "status": "running"},
            {"name": "agent-b", "model": "model-2", "registry_canonical_id": "canonical-b", "status": "running"},
        ]
        state.chat_messages = MagicMock()
        state.chat_messages.get_messages = AsyncMock(return_value=[])
        state.chat_messages.set_metadata = AsyncMock()
        state.chat_messages.send_message = AsyncMock(return_value={"id": "sys-msg"})
        state.chat_channels = MagicMock()
        state.chat_channels.update_last_message_at = AsyncMock()
        state.chat_hub = MagicMock()
        state.chat_hub.broadcast = AsyncMock()
        state.chat_hub.next_seq = MagicMock(return_value=1)
        state.bridge_sessions = bridge
        state.registry = MagicMock()
        state.registry.get = MagicMock(return_value=MagicMock(context_window=8192))
        state.http_client = MagicMock()
        state.taosmd_url = "http://localhost:8085"
        state.taosmd_in_process = False

        router = AgentChatRouter(state)
        message = {
            "id": "m1", "channel_id": "c1", "author_id": "user",
            "author_type": "user", "content": "test query", "created_at": 1.0,
        }
        channel = {
            "id": "c1", "type": "dm", "members": ["user", "agent-a", "agent-b"],
            "settings": {"response_mode": "lively", "max_hops": 3, "muted": []},
        }

        # Track contexts per agent by mocking the client's answer_context method
        contexts_per_agent = {}

        mock_client = MagicMock()
        mock_client.answer_context = AsyncMock()

        async def mock_answer_context_impl(query, agent, budget_tokens, now, reasoning_reserve, depth, **kwargs):
            contexts_per_agent[agent] = f"context-for-{agent}"
            return AnswerContextResult(
                context=f"context-for-{agent}",
                dropped=0,
                budget_tokens=budget_tokens,
                used_tokens=1000,
                layers={},
                depth=depth,
                intent="general",
                latency_ms=10.0,
            )
        mock_client.answer_context.side_effect = mock_answer_context_impl
        router._taosmd_client = mock_client

        await router._route(message, channel)

        # Should have called answer_context for each agent separately
        assert mock_client.answer_context.call_count == 2
        assert "canonical-a" in contexts_per_agent
        assert "canonical-b" in contexts_per_agent
        assert contexts_per_agent["canonical-a"] != contexts_per_agent["canonical-b"]


class TestAnswerContextClient:
    """Tests for the TaOSmd answer_context client wrapper."""

    @pytest.mark.asyncio
    async def test_client_calls_taosmd_http_api(self):
        """Client must call taOSmd HTTP API (or in-process Python API)."""
        from tinyagentos.taosmd_client import TaOSmdClient

        http_client = AsyncMock()
        http_client.post = AsyncMock()
        http_response = MagicMock()
        http_response.json = MagicMock(return_value={
            "context": "http context",
            "dropped": 0,
            "budget_tokens": 4000,
            "used_tokens": 1000,
            "layers": {},
            "depth": "standard",
            "intent": "general",
            "latency_ms": 10.0,
        })
        http_response.raise_for_status = MagicMock()
        http_client.post.return_value = http_response

        client = TaOSmdClient(http_client=http_client, base_url="http://localhost:8085", in_process=False)
        result = await client.answer_context(
            query="test", agent="test-agent", budget_tokens=4000,
            reasoning_reserve=1024, depth="standard",
        )

        http_client.post.assert_awaited_once()
        call_args = http_client.post.call_args
        assert call_args.args[0] == "http://localhost:8085/answer_context"
        payload = call_args.kwargs["json"]
        assert payload["query"] == "test"
        assert payload["agent"] == "test-agent"
        assert payload["budget_tokens"] == 4000
        assert payload["reasoning_reserve"] == 1024
        assert payload["depth"] == "standard"
        assert result.context == "http context"

    @pytest.mark.asyncio
    async def test_client_handles_http_errors(self):
        """Client must handle taOSmd unavailable gracefully (return empty result, not raise)."""
        from tinyagentos.taosmd_client import TaOSmdClient, AnswerContextResult
        import httpx

        http_client = AsyncMock()
        http_client.post = AsyncMock()
        http_client.post.side_effect = httpx.ConnectError("Connection refused")

        client = TaOSmdClient(http_client=http_client, base_url="http://localhost:8085", in_process=False)
        result = await client.answer_context(
            query="test", agent="test-agent", budget_tokens=4000,
            reasoning_reserve=1024, depth="standard",
        )

        # Should return empty result on error, not raise
        assert isinstance(result, AnswerContextResult)
        assert result.context == ""
        assert result.intent == "error"

    @pytest.mark.asyncio
    async def test_client_passes_agent_identity(self):
        """Client must pass the agent's canonical identity through to taOSmd."""
        from tinyagentos.taosmd_client import TaOSmdClient

        http_client = AsyncMock()
        http_client.post = AsyncMock()
        http_response = MagicMock()
        http_response.json = MagicMock(return_value={
            "context": "context",
            "dropped": 0,
            "budget_tokens": 4000,
            "used_tokens": 1000,
            "layers": {},
            "depth": "standard",
            "intent": "general",
            "latency_ms": 10.0,
        })
        http_response.raise_for_status = MagicMock()
        http_client.post.return_value = http_response

        client = TaOSmdClient(http_client=http_client, base_url="http://localhost:8085", in_process=False)
        await client.answer_context(
            query="test", agent="canonical-agent-123", budget_tokens=4000,
            reasoning_reserve=1024, depth="standard",
        )

        call_args = http_client.post.call_args
        assert call_args.kwargs["json"]["agent"] == "canonical-agent-123"

    @pytest.mark.asyncio
    async def test_client_passes_budget_from_model_context(self):
        """Client must pass the budget_tokens calculated from agent's configured model context window."""
        from tinyagentos.taosmd_client import TaOSmdClient

        http_client = AsyncMock()
        http_client.post = AsyncMock()
        http_response = MagicMock()
        http_response.json = MagicMock(return_value={
            "context": "context",
            "dropped": 0,
            "budget_tokens": 4096,
            "used_tokens": 1000,
            "layers": {},
            "depth": "standard",
            "intent": "general",
            "latency_ms": 10.0,
        })
        http_response.raise_for_status = MagicMock()
        http_client.post.return_value = http_response

        client = TaOSmdClient(http_client=http_client, base_url="http://localhost:8085", in_process=False)
        await client.answer_context(
            query="test", agent="test-agent", budget_tokens=4096,
            reasoning_reserve=1024, depth="standard",
        )

        call_args = http_client.post.call_args
        assert call_args.kwargs["json"]["budget_tokens"] == 4096
        # reasoning_reserve should be passed separately, not subtracted from budget
        assert call_args.kwargs["json"]["reasoning_reserve"] == 1024


if __name__ == "__main__":
    pytest.main([__file__, "-v"])