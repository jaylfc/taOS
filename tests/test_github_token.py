"""Tests for the github_token module — token minting, caching, and
per-agent token lookups."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestMintInstallationToken:
    """Tests for mint_installation_token()."""

    @pytest.fixture
    def clean_cache(self):
        """Clear the lifetime cache before each test."""
        import tinyagentos.github_token as gt
        gt._lifetime_cache.clear()
        yield
        gt._lifetime_cache.clear()

    @pytest.mark.asyncio
    async def test_mints_token_via_github_api(self, clean_cache):
        """mint_installation_token() calls the GitHub API and returns a token."""
        from tinyagentos.github_token import mint_installation_token

        http_client = AsyncMock()
        http_client.post = AsyncMock(return_value=MagicMock(
            status_code=201,
            json=MagicMock(return_value={
                "token": "ghs_test_token_123",
                "expires_at": "2026-12-31T12:00:00Z",
            }),
            raise_for_status=MagicMock(),
        ))

        with patch("tinyagentos.github_app.generate_jwt", return_value="mock.jwt"):
            token = await mint_installation_token(
                installation_id=42,
                repo="owner/repo",
                app_id="123456",
                private_key="fake-key",
                http_client=http_client,
            )

        assert token == "ghs_test_token_123"
        http_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_caches_token_with_lifetime(self, clean_cache):
        """Tokens are cached and returned on subsequent calls."""
        from tinyagentos.github_token import mint_installation_token, _lifetime_cache, _cache_key

        http_client = AsyncMock()
        http_client.post = AsyncMock(return_value=MagicMock(
            status_code=201,
            json=MagicMock(return_value={
                "token": "ghs_cached_token",
                "expires_at": "2026-12-31T12:00:00Z",
            }),
            raise_for_status=MagicMock(),
        ))

        with patch("tinyagentos.github_app.generate_jwt", return_value="mock.jwt"):
            token1 = await mint_installation_token(42, "owner/repo", "123456", "fake-key", http_client)
            assert token1 == "ghs_cached_token"
            assert http_client.post.call_count == 1
            assert _cache_key("123456", 42) in _lifetime_cache

            token2 = await mint_installation_token(42, "owner/repo", "123456", "fake-key", http_client)
            assert token2 == "ghs_cached_token"
            assert http_client.post.call_count == 1

    @pytest.mark.asyncio
    async def test_returns_none_on_api_failure(self, clean_cache):
        """Returns None when the GitHub API call fails."""
        from tinyagentos.github_token import mint_installation_token

        http_client = AsyncMock()
        http_client.post = AsyncMock(side_effect=Exception("Network error"))

        with patch("tinyagentos.github_app.generate_jwt", return_value="mock.jwt"):
            token = await mint_installation_token(
                installation_id=42,
                repo="owner/repo",
                app_id="123456",
                private_key="fake-key",
                http_client=http_client,
            )

        assert token is None

    @pytest.mark.asyncio
    async def test_refreshes_expired_cache_entry(self, clean_cache):
        """When a cached token is near expiry, a new one is minted."""
        from tinyagentos.github_token import mint_installation_token, _lifetime_cache, _cache_key

        _lifetime_cache[_cache_key("123456", 42)] = ("ghs_old_token", time.time() - 60)

        http_client = AsyncMock()
        http_client.post = AsyncMock(return_value=MagicMock(
            status_code=201,
            json=MagicMock(return_value={
                "token": "ghs_new_token",
                "expires_at": "2026-12-31T12:00:00Z",
            }),
            raise_for_status=MagicMock(),
        ))

        with patch("tinyagentos.github_app.generate_jwt", return_value="mock.jwt"):
            token = await mint_installation_token(42, "owner/repo", "123456", "fake-key", http_client)

        assert token == "ghs_new_token"
        assert http_client.post.call_count == 1


class TestGetAgentGitHubToken:
    """Tests for get_agent_github_token()."""

    @pytest.mark.asyncio
    async def test_returns_none_when_app_not_configured(self):
        """Returns None when GitHub App config is missing."""
        from tinyagentos.github_token import get_agent_github_token

        secrets_store = AsyncMock()
        config = MagicMock()
        config.github_app_id = ""
        config.github_app_private_key = ""

        token = await get_agent_github_token(
            "test-agent", secrets_store, config, AsyncMock(),
        )
        assert token is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_installations(self):
        """Returns None when the agent has no github-installation grants."""
        from tinyagentos.github_token import get_agent_github_token

        secrets_store = AsyncMock()
        secrets_store.get_agent_github_installations = AsyncMock(return_value=[])

        config = MagicMock()
        config.github_app_id = "123456"
        config.github_app_private_key = "fake-key"

        token = await get_agent_github_token(
            "test-agent", secrets_store, config, AsyncMock(),
        )
        assert token is None

    @pytest.fixture
    def clean_cache(self):
        import tinyagentos.github_token as gt
        gt._lifetime_cache.clear()
        yield
        gt._lifetime_cache.clear()

    @pytest.mark.asyncio
    async def test_mints_token_for_agent_with_grants(self, clean_cache):
        """Mints a token when the agent has github-installation grants."""
        from tinyagentos.github_token import get_agent_github_token

        secrets_store = AsyncMock()
        secrets_store.get_agent_github_installations = AsyncMock(return_value=[{
            "installation_id": 42,
            "repo_full_name": "owner/repo",
            "permissions": ["contents:read"],
        }])

        config = MagicMock()
        config.github_app_id = "123456"
        config.github_app_private_key = "fake-key"

        http_client = AsyncMock()
        http_client.post = AsyncMock(return_value=MagicMock(
            status_code=201,
            json=MagicMock(return_value={
                "token": "ghs_agent_token",
                "expires_at": "2026-12-31T12:00:00Z",
            }),
            raise_for_status=MagicMock(),
        ))

        with patch("tinyagentos.github_app.generate_jwt", return_value="mock.jwt"):
            token = await get_agent_github_token(
                "test-agent", secrets_store, config, http_client,
            )

        assert token == "ghs_agent_token"
        http_client.post.assert_called_once()
