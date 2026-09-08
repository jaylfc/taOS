"""Adapter registry — maps framework IDs to metadata and verification status."""
from __future__ import annotations

# Each entry describes one adapter that ships with TinyAgentOS.
# verification_status values (see docs/superpowers/specs/… §Framework verification model):
#   tested       -- Layer 4 round-trip passes on ARM64 + x86_64, fork PR merged,
#                   persistence audit clean → deployable with no warning
#   beta         -- Layer 4 passes on one arch, fork PR open, persistence audit
#                   clean → deployable with a one-line warning label in the wizard
#   experimental -- functional end-to-end but one or more audit items open →
#                   wizard greys out the tile; requires "I understand" click-through
#   broken       -- known-failing on current TAOS release → wizard blocks
#                   deployment with a link to the tracking_issue
#
# Optional fields:
#   tracking_issue -- URL to the tracking issue (required for broken; advisory for
#                     experimental). Shown inline in the deploy wizard.
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
        "verification_status": "experimental",
    },
    {
        "id": "generic",
        "name": "Generic",
        "description": "Fallback adapter — echos messages; use as a starting point",
        "verification_status": "experimental",
    },
    {
        "id": "pocketflow",
        "name": "PocketFlow",
        "description": "Graph-based flow execution with OpenAI-compatible backend",
        "verification_status": "experimental",
    },
    {
        "id": "langroid",
        "name": "Langroid",
        "description": "Task-based multi-agent framework using Langroid ChatAgent",
        "verification_status": "experimental",
    },
    {
        "id": "openai-agents-sdk",
        "name": "OpenAI Agents SDK",
        "description": "OpenAI Agents SDK with Runner.run_sync",
        "verification_status": "experimental",
    },
    {
        "id": "agent_zero",
        "name": "Agent Zero",
        "description": "Proxies messages to the Agent Zero HTTP API",
        "verification_status": "experimental",
    },
    {
        "id": "hermes",
        "name": "Hermes",
        "description": "Hermes Agent Gateway (NousResearch) bridge over the OpenAI-compatible API",
        "verification_status": "tested",
    },
    {
        "id": "opencrabs",
        "name": "OpenCrabs",
        "description": "Autonomous single-binary Rust agent (OpenClaw-inspired) driven via `opencrabs run`",
        "verification_status": "beta",
    },
    {
        "id": "deer-flow",
        "name": "DeerFlow",
        "description": "DeerFlow LangGraph SuperAgent harness (runs API bridge)",
        "verification_status": "experimental",
    },
    {
        "id": "ironclaw",
        "name": "IronClaw",
        "description": "IronClaw gateway proxy",
        "verification_status": "experimental",
    },
    {
        "id": "microclaw",
        "name": "MicroClaw",
        "description": "MicroClaw gateway proxy",
        "verification_status": "experimental",
    },
    {
        "id": "moltis",
        "name": "Moltis",
        "description": "Moltis gateway proxy",
        "verification_status": "experimental",
    },
    {
        "id": "nanoclaw",
        "name": "NanoClaw",
        "description": "NanoClaw gateway proxy",
        "verification_status": "experimental",
    },
    {
        "id": "nullclaw",
        "name": "NullClaw",
        "description": "NullClaw gateway proxy",
        "verification_status": "experimental",
    },
    {
        "id": "picoclaw",
        "name": "PicoClaw",
        "description": "PicoClaw gateway proxy",
        "verification_status": "experimental",
    },
    {
        "id": "shibaclaw",
        "name": "ShibaClaw",
        "description": "ShibaClaw gateway proxy",
        "verification_status": "experimental",
    },
    {
        "id": "zeroclaw",
        "name": "ZeroClaw",
        "description": "ZeroClaw gateway proxy",
        "verification_status": "experimental",
    },
    {
        "id": "omp",
        "name": "OMP (Oh My Pi)",
        "description": "OMP coding agent via the Agent Client Protocol (omp acp)",
        "verification_status": "experimental",
    },
]

_VALID_STATUSES = {"tested", "beta", "experimental", "broken"}

# Validate the registry at import time so a typo in a status field is
# caught immediately rather than silently propagating.
assert all(
    e["verification_status"] in _VALID_STATUSES for e in _REGISTRY
), f"Invalid verification_status in _REGISTRY: {[e for e in _REGISTRY if e['verification_status'] not in _VALID_STATUSES]}"


def list_frameworks() -> list[dict]:
    """Return every registered adapter with id, name, description, and verification_status.

    Each dict may also carry optional fields such as ``tracking_issue`` for
    broken/experimental entries.
    """
    return [dict(entry) for entry in _REGISTRY]
