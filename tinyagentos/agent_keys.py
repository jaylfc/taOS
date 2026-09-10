"""Agent LLM key lifecycle helpers."""
from __future__ import annotations

from typing import Any

from tinyagentos.config import save_config_locked


async def re_mint_agent_key(
    agent_name: str,
    agent: dict[str, Any],
    proxy: Any,
    config: Any,
    config_path: str,
    models: list[str] | None = None,
) -> str | None:
    """Re-mint a missing per-agent LiteLLM key and persist it.

    Returns the new plaintext key on success, or None if the proxy is
    unavailable or the mint failed.
    """
    if proxy is None or not proxy.is_running():
        return None
    if models is None:
        models = [m for m in [agent.get("model"), *(agent.get("fallback_models") or [])] if m]
    try:
        new_key = await proxy.create_agent_key(agent_name, models=models or None)
    except Exception:
        return None
    if not new_key:
        return None
    agent["llm_key"] = new_key
    await save_config_locked(config, config_path)
    return new_key
