"""GitHub App installation token minting + per-agent cache.

Layered on top of ``tinyagentos.github_app`` which handles JWT generation
and the raw ``POST /app/installations/{id}/access_tokens`` call. This
module adds:

- Lifetime-aware caching: tokens are cached until 5 min before expiry
  (GitHub issues tokens valid for 1 hour).
- Per-agent lookups: ``get_agent_github_token(agent_name)`` queries the
  SecretsStore for ``github-installation`` secrets the agent is granted,
  mints a token scoped to those repos, and returns it.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx
    from tinyagentos.config import AppConfig
    from tinyagentos.secrets import SecretsStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lifetime-aware token cache
# ---------------------------------------------------------------------------

# key = "app_id:installation_id" -> (token, expiry_unix_timestamp)
_lifetime_cache: dict[str, tuple[str, float]] = {}
_MAX_CACHE_SIZE = 100
_REFRESH_WINDOW = 300  # Refresh when < 5 min remain (token lifetime is 1 hour)


def _cache_key(app_id: str, installation_id: int) -> str:
    return f"{app_id}:{installation_id}"


def _evict_if_full() -> None:
    if len(_lifetime_cache) >= _MAX_CACHE_SIZE:
        # Evict the entry with the oldest expiry timestamp (LRU by deadline)
        oldest_key = min(_lifetime_cache, key=lambda k: _lifetime_cache[k][1])
        del _lifetime_cache[oldest_key]


def _cached_token_lifetime(key: str) -> str | None:
    """Return a cached token if it still has > 5 min of life."""
    entry = _lifetime_cache.get(key)
    if entry:
        token, expiry = entry
        if time.time() < expiry - _REFRESH_WINDOW:
            return token
        del _lifetime_cache[key]
    return None


async def mint_installation_token(
    installation_id: int,
    repo: str,
    app_id: str,
    private_key: str,
    http_client: httpx.AsyncClient,
) -> str | None:
    """Mint a short-lived GitHub App installation token.

    The token is scoped to all repos the installation has access to.
    The *repo* parameter is accepted for future per-repo scoping but
    is not currently enforced by the GitHub API.

    Results are cached with lifetime awareness — the cache tracks the
    actual ``expires_at`` from GitHub and auto-refreshes when fewer
    than 5 minutes remain.
    """
    key = _cache_key(app_id, installation_id)
    cached = _cached_token_lifetime(key)
    if cached:
        return cached

    from tinyagentos.github_app import generate_jwt, _auth_headers, _INSTALL_TOKEN_URL

    try:
        jwt = generate_jwt(app_id, private_key)
        url = _INSTALL_TOKEN_URL.format(installation_id=installation_id)
        # Optionally scope to a single repo via the repository_ids parameter.
        # The standard flow mints a token valid for all repos the installation
        # can see; we don't currently resolve repo -> repo_id, so skip for now.
        resp = await http_client.post(
            url,
            headers=_auth_headers(jwt),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        token = data.get("token")
        if token:
            # GitHub returns an expires_at ISO-8601 string (e.g.,
            # "2025-01-01T12:00:00Z"). Parse it out so we track
            # real lifetime rather than a fixed TTL.
            expires_str = data.get("expires_at", "")
            if expires_str:
                try:
                    from datetime import datetime, timezone as dt_timezone
                    expires_dt = datetime.fromisoformat(
                        expires_str.replace("Z", "+00:00")
                    )
                    expires_ts = expires_dt.timestamp()
                except (ValueError, TypeError):
                    expires_ts = time.time() + 3600  # fallback: 1 hour
            else:
                expires_ts = time.time() + 3600

            _evict_if_full()
            _lifetime_cache[key] = (token, expires_ts)
        return token
    except Exception as exc:
        logger.exception(
            "Failed to mint installation token for installation %s: %s",
            installation_id,
            exc,
        )
        return None


async def get_agent_github_token(
    agent_name: str,
    secrets_store: SecretsStore,
    config: AppConfig,
    http_client: httpx.AsyncClient,
) -> str | None:
    """Return a short-lived GitHub token for *agent_name*, or None.

    Looks up all ``github-installation`` secrets the agent is granted
    access to via ``SecretsStore.get_agent_github_installations()`` and
    tries each installation in turn until a token is successfully minted.

    The minted token is scoped to the **entire** installation (all repos
    the installation can access), not to individual repos.  Per-repo
    grants in the UI reflect which agents can *request* a token; the
    backend does not currently enforce per-repo token scoping via GitHub's
    ``repository_ids`` parameter — that is tracked as a future enhancement.

    Returns None if the agent has no GitHub App access grants, the
    GitHub App is not configured, or token minting fails for all
    installations.
    """
    if not config.github_app_id or not config.github_app_private_key:
        return None

    installations = await secrets_store.get_agent_github_installations(agent_name)
    if not installations:
        return None

    # Try each granted installation until we mint a valid token.
    for inst in installations:
        iid = inst.get("installation_id")
        repo = inst.get("repo_full_name", "")

        if not iid:
            continue

        token = await mint_installation_token(
            installation_id=iid,
            repo=repo,
            app_id=config.github_app_id,
            private_key=config.github_app_private_key,
            http_client=http_client,
        )
        if token:
            return token

    return None
