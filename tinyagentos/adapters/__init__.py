"""Adapter registry — maps framework IDs to metadata and verification status."""
from __future__ import annotations

# Each entry describes one adapter that ships with TinyAgentOS.
# verification_status values:
#   beta   -- primary/recommended; basic integration solid, some edge cases unverified
#   alpha  -- adapter exists, happy path only, not battle-tested
#   broken -- known to be non-functional on current codebase
_REGISTRY: list[dict] = [
    {
        "id": "openclaw",
        "name": "OpenClaw",
        "description": "OpenClaw LXC-native agent via HTTP proxy",
        "verification_status": "beta",
    },
    {
        "id": "smolagents",
        "name": "SmolAgents",
        "description": "Lightweight code-first agent framework by Hugging Face",
        "verification_status": "alpha",
    },
    {
        "id": "generic",
        "name": "Generic",
        "description": "Fallback adapter — echos messages; use as a starting point",
        "verification_status": "alpha",
    },
    {
        "id": "pocketflow",
        "name": "PocketFlow",
        "description": "Graph-based flow execution with OpenAI-compatible backend",
        "verification_status": "alpha",
    },
    {
        "id": "langroid",
        "name": "Langroid",
        "description": "Task-based multi-agent framework using Langroid ChatAgent",
        "verification_status": "alpha",
    },
    {
        "id": "openai-agents-sdk",
        "name": "OpenAI Agents SDK",
        "description": "OpenAI Agents SDK with Runner.run_sync",
        "verification_status": "alpha",
    },
    {
        "id": "agent_zero",
        "name": "Agent Zero",
        "description": "Proxies messages to the Agent Zero HTTP API",
        "verification_status": "alpha",
    },
    {
        "id": "hermes",
        "name": "Hermes",
        "description": "Hermes Agent Gateway (NousResearch) bridge over the OpenAI-compatible API",
        "verification_status": "beta",
    },
    {
        "id": "deer-flow",
        "name": "DeerFlow",
        "description": "DeerFlow LangGraph SuperAgent harness (runs API bridge)",
        "verification_status": "alpha",
    },
    {
        "id": "ironclaw",
        "name": "IronClaw",
        "description": "IronClaw gateway proxy",
        "verification_status": "alpha",
    },
    {
        "id": "microclaw",
        "name": "MicroClaw",
        "description": "MicroClaw gateway proxy",
        "verification_status": "alpha",
    },
    {
        "id": "moltis",
        "name": "Moltis",
        "description": "Moltis gateway proxy",
        "verification_status": "alpha",
    },
    {
        "id": "nanoclaw",
        "name": "NanoClaw",
        "description": "NanoClaw gateway proxy",
        "verification_status": "alpha",
    },
    {
        "id": "nullclaw",
        "name": "NullClaw",
        "description": "NullClaw gateway proxy",
        "verification_status": "alpha",
    },
    {
        "id": "picoclaw",
        "name": "PicoClaw",
        "description": "PicoClaw gateway proxy",
        "verification_status": "alpha",
    },
    {
        "id": "shibaclaw",
        "name": "ShibaClaw",
        "description": "ShibaClaw gateway proxy",
        "verification_status": "alpha",
    },
    {
        "id": "zeroclaw",
        "name": "ZeroClaw",
        "description": "ZeroClaw gateway proxy",
        "verification_status": "alpha",
    },
]

_VALID_STATUSES = {"beta", "alpha", "broken"}


def list_frameworks() -> list[dict]:
    """Return every registered adapter with id, name, description, and verification_status."""
    return [dict(entry) for entry in _REGISTRY]
