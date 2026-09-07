"""Unit test verifying the fix for agent self-service routes."""
import pytest
from tinyagentos.auth_middleware import _AGENT_TOKEN_PATHS, _is_exempt


class TestAgentTokenPaths:
    """Test that agent self-service routes are in _AGENT_TOKEN_PATHS."""

    def test_agent_models_path_is_allowlisted(self):
        "/api/agents/me/models should be in _AGENT_TOKEN_PATHS."
        assert "/api/agents/me/models" in _AGENT_TOKEN_PATHS

    def test_agent_model_path_is_allowlisted(self):
        "/api/agents/me/model should be in _AGENT_TOKEN_PATHS."
        assert "/api/agents/me/model" in _AGENT_TOKEN_PATHS

    def test_bogus_path_is_not_allowlisted(self):
        "A random path should NOT be in _AGENT_TOKEN_PATHS."
        assert "/api/random/path" not in _AGENT_TOKEN_PATHS

    def test_is_exempt_returns_false_for_agent_paths(self):
        "Agent paths should NOT be exempt (they use _AGENT_TOKEN_PATHS instead)."
        assert _is_exempt("GET", "/api/agents/me/models") is False
        assert _is_exempt("POST", "/api/agents/me/model") is False


class TestAgentTokenPathsMiddlewareLogic:
    """Test the middleware logic for agent token paths."""

    def test_get_agent_models_allowlisted(self):
        "GET /api/agents/me/models is in _AGENT_TOKEN_PATHS -> Bearer token accepted."
        assert "/api/agents/me/models" in _AGENT_TOKEN_PATHS

    def test_post_agent_model_allowlisted(self):
        "POST /api/agents/me/model is in _AGENT_TOKEN_PATHS -> Bearer token accepted."
        assert "/api/agents/me/model" in _AGENT_TOKEN_PATHS

    def test_bogus_not_allowlisted(self):
        "Bogus path should not be allowlisted, ensuring skeleton key protection."
        assert "/api/agents/nonexistent" not in _AGENT_TOKEN_PATHS


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))