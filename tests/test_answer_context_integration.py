"""RED tests for answer_context integration.

These tests verify that agent memory context paths route through taOSmd's
answer_context function. They FAIL on current code (raw search) and PASS
when the integration is complete.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# TODO: Import the answer_context client when implemented
# from tinyagentos.taosmd_client import answer_context, TaOSmdClient


class TestAnswerContextIntegration:
    """Tests for the answer_context integration across all call sites."""

    @pytest.mark.asyncio
    async def test_agent_chat_router_uses_answer_context(self):
        """agent_chat_router._route_inner must call answer_context for context."""
        from tinyagentos.agent_chat_router import AgentChatRouter
        from tinyagentos.chat.context_window import build_context_window

        bridge = MagicMock()
        bridge.enqueue_user_message = AsyncMock()
        
        state = MagicMock()
        state.config = MagicMock()
        state.config.agents = [
            {"name": "test-agent", "status": "running", "model": "test-model"}
        ]
        state.chat_messages = MagicMock()
        state.chat_messages.get_messages = AsyncMock(return_value=[
            {"author_id": "user", "author_type": "user", "content": "hello"},
            {"author_id": "test-agent", "author_type": "agent", "content": "hi there"},
        ])
        state.chat_messages.set_metadata = AsyncMock()
        state.chat_channels = MagicMock()
        state.chat_channels.update_last_message_at = AsyncMock()
        state.chat_hub = MagicMock()
        state.chat_hub.broadcast = AsyncMock()
        state.chat_hub.next_seq = MagicMock(return_value=1)
        state.bridge_sessions = bridge
        state.registry = MagicMock()
        state.registry.get = MagicMock(return_value=MagicMock(context_window=8192))

        router = AgentChatRouter(state)
        message = {
            "id": "m1", "channel_id": "c1", "author_id": "user",
            "author_type": "user", "content": "test query", "created_at": 1.0,
        }
        channel = {
            "id": "c1", "type": "dm", "members": ["user", "test-agent"],
            "settings": {"response_mode": "lively", "max_hops": 3, "muted": []},
        }

        # RED: This should call answer_context, not build_context_window
        with patch("tinyagentos.chat.context_window.build_context_window") as mock_build:
            mock_build.return_value = []
            await router._route(message, channel)

        # This assertion will FAIL until answer_context is used
        mock_build.assert_not_called()  # Should not call build_context_window
        # TODO: Assert answer_context was called with correct params

    @pytest.mark.asyncio
    async def test_memory_search_endpoint_uses_answer_context(self):
        """routes/memory.py memory_search must use answer_context for agent callers."""
        from tinyagentos.routes.memory import memory_search
        from tinyagentos.routes.memory import SearchRequest
        from tinyagentos.taosmd_client import AnswerContextResult

        request = MagicMock()
        request.app.state.http_client = MagicMock()
        request.app.state.http_client.get = AsyncMock()
        request.app.state.http_client.post = AsyncMock()
        request.app.state.agent_memory_dir = MagicMock()
        request.app.state.config = MagicMock()
        request.app.state.config.agents = [
            {"name": "test-agent", "model": "test-model"}
        ]
        
        # Mock taosmd_client on app.state
        mock_client = AsyncMock()
        mock_client.answer_context = AsyncMock(return_value=AnswerContextResult(
            context="test context",
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

        # RED: This should call answer_context via a client, not qmd directly
        with patch("tinyagentos.routes.memory._qmd_vsearch") as mock_vsearch:
            mock_vsearch.return_value = []
            result = await memory_search(request, body)

        # This assertion will FAIL until answer_context is used
        mock_vsearch.assert_not_called()  # Should not call _qmd_vsearch directly
        mock_client.answer_context.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_user_memory_agent_search_uses_answer_context(self):
        """routes/user_memory.py agent_search must use answer_context."""
        from tinyagentos.routes.user_memory import agent_search
        from tinyagentos.taosmd_client import AnswerContextResult

        request = MagicMock()
        request.app.state.config = MagicMock()
        request.app.state.config.agents = [
            {"name": "test-agent", "can_read_user_memory": True, "model": "test-model"}
        ]
        request.app.state.user_memory = MagicMock()
        request.app.state.user_memory.search = AsyncMock(return_value=[])
        
        # Mock taosmd_client on app.state
        mock_client = AsyncMock()
        mock_client.answer_context = AsyncMock(return_value=AnswerContextResult(
            context="test context",
            dropped=1,
            budget_tokens=4000,
            used_tokens=3500,
            layers={"l0": 100},
            depth="standard",
            intent="general",
            latency_ms=30.0,
        ))
        request.app.state.taosmd_client = mock_client

        # RED: This should call answer_context, not user_memory.search directly
        result = await agent_search(request, q="test query", agent_name="test-agent", limit=10)

        # This assertion will FAIL until answer_context is used
        request.app.state.user_memory.search.assert_not_called()
        mock_client.answer_context.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_bridge_session_system_prompt_uses_answer_context(self):
        """bridge_session.build_bootstrap_system_prompt must incorporate answer_context."""
        from tinyagentos.bridge_session import build_bootstrap_system_prompt

        agent = {"name": "test-agent", "soul_md": "", "agent_md": "", "memory_plugin": "taosmd"}

        # RED: build_bootstrap_system_prompt should include answer_context output
        prompt = build_bootstrap_system_prompt(agent)
        
        # Currently returns just the assembled prompt without answer_context
        # The taosmd agent_rules are included with the agent name substituted
        assert "test-agent" in prompt  # Agent name should be substituted in rules
        assert "taosmd" in prompt.lower()  # Should mention taosmd
        # TODO: When integrated, prompt should include memory context from answer_context

    @pytest.mark.asyncio
    async def test_prompt_assembly_uses_answer_context(self):
        """prompt_assembly.assemble_system_prompt must use answer_context for memory."""
        from tinyagentos.prompt_assembly import assemble_system_prompt
        from types import SimpleNamespace

        agent = SimpleNamespace(
            slug="test-agent",
            soul_md="Test soul",
            agent_md="Test agent md",
            memory_plugin="taosmd",
        )

        # RED: Should include answer_context output
        prompt = assemble_system_prompt(agent)
        
        # Currently includes agent rules but not answer_context memory
        assert "Test soul" in prompt
        assert "Test agent md" in prompt
        # TODO: Assert answer_context memory context is included

    @pytest.mark.asyncio
    async def test_answer_context_returns_dropped_count(self):
        """answer_context response must include dropped item count for trace visibility."""
        # This test defines the expected contract for answer_context
        # RED: answer_context doesn't exist yet
        
        # Expected response shape:
        # {
        #     "context": "dated session blocks...",
        #     "dropped": 3,  # number of items dropped due to budget
        #     "budget_tokens": 4000,
        #     "used_tokens": 3800,
        # }
        pass

    @pytest.mark.asyncio
    async def test_answer_context_respects_token_budget(self):
        """answer_context must respect the token budget derived from agent's model."""
        # The budget should be a share of the model's context window
        # (not the whole window, per taOSmd spec)
        pass

    @pytest.mark.asyncio
    async def test_answer_context_identity_isolation(self):
        """An agent must only receive its own and permitted memory through answer_context."""
        # Test that agent A cannot see agent B's memory
        pass


class TestAnswerContextClient:
    """Tests for the TaOSmd answer_context client wrapper."""

    @pytest.mark.asyncio
    async def test_client_calls_taosmd_http_api(self):
        """Client must call taOSmd HTTP API (or in-process Python API)."""
        pass

    @pytest.mark.asyncio
    async def test_client_handles_http_errors(self):
        """Client must handle taOSmd unavailable gracefully."""
        pass

    @pytest.mark.asyncio
    async def test_client_passes_agent_identity(self):
        """Client must pass the agent's canonical identity."""
        pass

    @pytest.mark.asyncio
    async def test_client_passes_budget_from_model_context(self):
        """Client must calculate budget from agent's configured model context window."""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
