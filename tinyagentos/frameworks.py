"""Framework manifest registry with update-metadata and validation."""
from __future__ import annotations

from tinyagentos.shortcuts.validation import validate_shortcuts


class FrameworkManifestError(ValueError):
    """Raised when a framework manifest entry is invalid or incomplete."""


FRAMEWORKS: dict[str, dict] = {
    "openclaw": {
        "id": "openclaw",
        "name": "OpenClaw",
        "description": "OpenClaw LXC-native agent via HTTP proxy",
        "verification_status": "beta",
        "release_source": "github:jaylfc/openclaw",
        "release_asset_pattern": "openclaw-taos-fork-linux-{arch}.tgz",
        "install_script": "/usr/local/bin/taos-framework-update",
        "service_name": "openclaw",
        "slash_commands": [
            {"name": "help", "description": "List OpenClaw commands"},
            {"name": "clear", "description": "Clear conversation history"},
            {"name": "compact", "description": "Summarise and compact context"},
            {"name": "cost", "description": "Show token spend for this session"},
        ],
    },
    "smolagents": {
        "id": "smolagents",
        "name": "SmolAgents",
        "description": "Lightweight code-first agent framework by Hugging Face — taOS SSE bridge verified in group chat",
        "verification_status": "beta",
        "slash_commands": [
            {"name": "help", "description": "Show SmolAgents help"},
        ],
    },
    "generic": {
        "id": "generic",
        "name": "Generic",
        "description": "Fallback adapter — echos messages; use as a starting point",
        "verification_status": "alpha",
    },
    "pocketflow": {
        "id": "pocketflow",
        "name": "PocketFlow",
        "description": "Graph-based flow execution with OpenAI-compatible backend — taOS SSE bridge verified in group chat",
        "verification_status": "beta",
        "slash_commands": [
            {"name": "help", "description": "Show PocketFlow help"},
        ],
    },
    "langroid": {
        "id": "langroid",
        "name": "Langroid",
        "description": "Task-based multi-agent framework using Langroid ChatAgent — taOS SSE bridge verified in group chat",
        "verification_status": "beta",
        "slash_commands": [
            {"name": "help", "description": "Show Langroid help"},
        ],
    },
    "openai-agents-sdk": {
        "id": "openai-agents-sdk",
        "name": "OpenAI Agents SDK",
        "description": "OpenAI Agents SDK with OpenAIChatCompletionsModel — taOS SSE bridge verified in group chat",
        "verification_status": "beta",
        "slash_commands": [
            {"name": "help", "description": "Show Agents SDK help"},
        ],
    },
    "hermes": {
        "id": "hermes",
        "name": "Hermes",
        "description": "Hermes Agent Gateway (NousResearch) — taOS bridge to api_server on :8642",
        "verification_status": "beta",
        "slash_commands": [
            {"name": "help", "description": "List available commands"},
            {"name": "clear", "description": "Clear the session context"},
            {"name": "model", "description": "Show or change active model"},
        ],
    },
    "agent_zero": {
        "id": "agent_zero",
        "name": "Agent Zero",
        "description": "Proxies messages to the Agent Zero HTTP API (agent0ai/agent-zero)",
        "verification_status": "alpha",
    },
    "ironclaw": {
        "id": "ironclaw",
        "name": "IronClaw",
        "description": "OpenClaw-inspired Rust agent focused on privacy and security (nearai/ironclaw)",
        "verification_status": "alpha",
    },
    "microclaw": {
        "id": "microclaw",
        "name": "MicroClaw",
        "description": "Agentic AI assistant in Rust, inspired by NanoClaw (microclaw/microclaw)",
        "verification_status": "alpha",
    },
    "moltis": {
        "id": "moltis",
        "name": "Moltis",
        "description": "Secure persistent personal agent server in Rust (moltis-org/moltis)",
        "verification_status": "alpha",
    },
    "nanoclaw": {
        "id": "nanoclaw",
        "name": "NanoClaw",
        "description": "Lightweight container-based OpenClaw alternative on Anthropic Agents SDK (qwibitai/nanoclaw)",
        "verification_status": "alpha",
    },
    "nullclaw": {
        "id": "nullclaw",
        "name": "NullClaw",
        "description": "Fully autonomous AI assistant infrastructure in Zig (nullclaw/nullclaw)",
        "verification_status": "alpha",
    },
    "picoclaw": {
        "id": "picoclaw",
        "name": "PicoClaw",
        "description": "Tiny, fast, and deployable anywhere — automate the mundane, unleash creativity (sipeed/picoclaw)",
        "verification_status": "alpha",
    },
    "shibaclaw": {
        "id": "shibaclaw",
        "name": "ShibaClaw",
        "description": "Self-hosted AI agent with 5-layer prompt injection protection (RikyZ90/ShibaClaw)",
        "verification_status": "alpha",
    },
    "zeroclaw": {
        "id": "zeroclaw",
        "name": "ZeroClaw",
        "description": "Fast, small, fully autonomous AI personal assistant in Rust (zeroclaw-labs/zeroclaw)",
        "verification_status": "alpha",
    },
}

_REQUIRED_UPDATE_FIELDS = (
    "release_source",
    "release_asset_pattern",
    "install_script",
    "service_name",
)


def validate_framework_manifest(
    fw_id: str,
    entry: dict,
    *,
    require_update_fields: bool = False,
) -> None:
    """Validate a framework manifest entry.

    Raises FrameworkManifestError if required fields are absent.
    """
    for base_field in ("id", "name"):
        if base_field not in entry:
            raise FrameworkManifestError(
                f"Framework {fw_id!r}: missing required field {base_field!r}"
            )

    if require_update_fields:
        missing = [f for f in _REQUIRED_UPDATE_FIELDS if f not in entry]
        if missing:
            raise FrameworkManifestError(
                f"Framework {fw_id!r}: missing update fields: {missing}"
            )

    shortcuts = entry.get("shortcuts")
    if shortcuts is not None:
        try:
            validate_shortcuts(shortcuts)
        except ValueError as exc:
            raise ValueError(f"framework '{fw_id}': {exc}") from exc
