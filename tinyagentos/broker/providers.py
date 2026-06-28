"""Curated provider registry for the Secrets Broker.

Code-seeded plain data (no DB table in P0). Each provider declares how
taOS reaches it (base_url, auth_style), which SecretsStore secret holds the
real key (secret_ref), and the default scopes a grant gets when the caller
does not name any. ``kind`` drives the lifecycle:

  - "mcp"        : reached as an MCP server; the broker authorizes the call
                   and injects the real key from SecretsStore at proxy time.
  - "http-proxy" : reached over plain HTTP; approval mints a virtual token
                   (sk-taos-secret-...) the agent presents instead of the
                   real key, so the real key never leaves taOS.

auth_style is one of {"bearer", "header:X-Api-Key", "query:api_key"} and is
editable data, not behaviour. None of the fields are secrets.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Provider:
    id: str
    display_name: str
    kind: str  # "mcp" or "http-proxy"
    base_url: str
    auth_style: str  # "bearer" | "header:X-Api-Key" | "query:api_key"
    secret_ref: str  # SecretsStore secret name holding the real key
    default_scopes: list[str] = field(default_factory=list)
    notes: str = ""


_PROVIDER_LIST: list[Provider] = [
    Provider(
        id="exa",
        display_name="Exa",
        kind="mcp",
        base_url="https://api.exa.ai",
        auth_style="bearer",
        secret_ref="EXA_API_KEY",
        default_scopes=["search"],
        notes="Neural search API.",
    ),
    Provider(
        id="tavily",
        display_name="Tavily",
        kind="mcp",
        base_url="https://api.tavily.com",
        auth_style="bearer",
        secret_ref="TAVILY_API_KEY",
        default_scopes=["search"],
        notes="Search API for agents.",
    ),
    Provider(
        id="github",
        display_name="GitHub",
        kind="mcp",
        base_url="https://api.github.com",
        auth_style="bearer",
        secret_ref="GITHUB_TOKEN",
        default_scopes=["repos:read", "prs:read", "issues:read"],
        notes="Repos, PRs, issues. Write scopes (repos:write, ...) opt-in.",
    ),
    Provider(
        id="firecrawl",
        display_name="Firecrawl",
        kind="mcp",
        base_url="https://api.firecrawl.dev",
        auth_style="bearer",
        secret_ref="FIRECRAWL_API_KEY",
        default_scopes=["scrape", "crawl"],
        notes="Web scrape and crawl.",
    ),
    Provider(
        id="brave",
        display_name="Brave Search",
        kind="mcp",
        base_url="https://api.search.brave.com",
        auth_style="header:X-Api-Key",
        secret_ref="BRAVE_API_KEY",
        default_scopes=["search"],
        notes="Brave web search API.",
    ),
    Provider(
        id="jina",
        display_name="Jina",
        kind="mcp",
        base_url="https://r.jina.ai",
        auth_style="bearer",
        secret_ref="JINA_API_KEY",
        default_scopes=["reader", "embeddings"],
        notes="Reader and embeddings.",
    ),
    Provider(
        id="openai",
        display_name="OpenAI",
        kind="http-proxy",
        base_url="https://api.openai.com/v1",
        auth_style="bearer",
        secret_ref="OPENAI_API_KEY",
        default_scopes=["chat", "embeddings"],
        notes="HTTP proxy over /v1/*; exercises the virtual-key fallback.",
    ),
]

PROVIDERS: dict[str, Provider] = {p.id: p for p in _PROVIDER_LIST}


def get_provider(provider_id: str) -> Provider | None:
    """Return the :class:`Provider` for *provider_id*, or None if unknown."""
    return PROVIDERS.get(provider_id)
