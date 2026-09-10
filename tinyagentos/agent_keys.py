from __future__ import annotations

import logging
import os

from tinyagentos.config import save_config_locked
from tinyagentos.llm_proxy import LLMProxy

logger = logging.getLogger(__name__)


async def ensure_agent_llm_key(
    config: "AppConfig",
    agent_dict: dict,
    proxy: LLMProxy | None,
    data_dir,
    existing_llm_key: str | None = None,
) -> tuple[str | None, str | None]:
    """Ensure an agent has an LLM key, minting via proxy if possible or applying fallback.

    This unified helper replaces three fragmented code paths:
    1. Deployer inline logic (tinyagentos/deployer.py:210-218)
    2. Update-agent-model re-scope failure discard (tinyagentos/routes/agents.py:1633-1637)
    3. Bootstrap validation before returning 409 (tinyagentos/routes/openclaw.py:90-100)

    Args:
        config: The AppConfig object (needed for saving)
        agent_dict: The agent record dict (will be mutated if we mint a new key)
        proxy: The LLMProxy instance (may be None)
        data_dir: The data directory (Path or similar) for master key retrieval
        existing_llm_key: Optional existing llm_key from the agent record (used when updating agents)

    Returns:
        tuple[str | None, str | None]: (key_or_none, reason_or_none)
            - key: Non-None means success with the key
            - reason: Non-None means failure with the reason string

    Side effects:
        - On success (key minted): mutates agent_dict["llm_key"] and calls save_config_locked(config, config.config_path)
        - On master key fallback: mutates agent_dict["llm_key"] and calls save_config_locked(config, config.config_path)
        - On proxy-not-running-with-existing-key: logs warning but does NOT mutate (already has a key)
        - On proxy-not-running-no-existing-key: logs warning (deferred) but does NOT mutate
    """
    from tinyagentos.litellm_config import get_litellm_master_key

    # If proxy is not running, we cannot mint per-agent keys. Two distinct sub-cases:
    if not proxy or not proxy.is_running():
        if existing_llm_key is not None:
            # Agent already has a key; this is an update path (e.g. update_agent_model re-scope).
            # The stale per-agent key is being replaced with None; we should ensure a key exists.
            # We cannot re-mint because the proxy isn't running, so we fall back to master key logic.
            # Preserve the existing key to avoid infinite recursion.
            pass  # fall through to master key logic below
        else:
            # Agent has never had a key and proxy isn't running.
            # This is a deploy-time scenario (proxy.is_running() == False) where we must defer.
            logger.warning(
                "ensure_agent_llm_key: proxy not running; key generation deferred for agent %s",
                agent_dict.get("name", "<unknown>"),
            )
            return None, "proxy not running; key generation deferred"

    # Proxy is running (or we treat it as such for fallback). Try to mint a scoped key.
    permitted = agent_dict.get("permitted_models") or [agent_dict.get("model")] or ["default"]
    new_key = None
    if proxy is not None:
        new_key = await proxy.create_agent_key(agent_dict["name"], models=permitted)

    if new_key is not None:
        # Mint succeeded: persist the scoped key.
        agent_dict["llm_key"] = new_key
        await save_config_locked(config, config.config_path)
        logger.info(
            "ensure_agent_llm_key: minted per-agent virtual key for agent %s",
            agent_dict.get("name", "<unknown>"),
        )
        return new_key, None

    # Mint failed: handle the two distinct cases.
    # 1. DB configured but mint failed -> refuse with the existing error text
    if proxy is not None and getattr(proxy, "database_url", None) is not None:
        db_url = proxy.database_url
        db_host = db_url.split("@")[-1] if "@" in db_url else db_url
        error_msg = (
            "per-agent LiteLLM virtual key mint failed despite DB "
            f"configured at {db_host}. This is a LiteLLM/DB fault "
            "(migration pending, DB unreachable, or master-key "
            "drift), not a missing-DB capability gap, so the deploy "
            "is refused rather than silently using the shared "
            "master key. Fix the LiteLLM Postgres connection."
        )
        logger.error("ensure_agent_llm_key: %s", error_msg)
        return None, error_msg

    # 2. Routing-only (no DB) -> apply deployer fallback policy
    why = "LiteLLM is running in routing-only mode (no Postgres DATABASE_URL configured)"
    fallback_disabled = os.environ.get(
        "TAOS_DISABLE_AGENT_MASTER_KEY_FALLBACK", ""
    ).strip().lower() in ("1", "true", "yes")

    if fallback_disabled:
        error_msg = (
            f"per-agent LiteLLM virtual key could not be minted: {why}. "
            "The master-key fallback is disabled "
            "(TAOS_DISABLE_AGENT_MASTER_KEY_FALLBACK is set), so the "
            "deploy is refused. Configure a Postgres database for "
            "LiteLLM to issue per-agent scoped keys, or unset that "
            "variable on a single-user instance."
        )
        logger.error("ensure_agent_llm_key: %s", error_msg)
        return None, error_msg

    # Master key fallback is enabled.
    master_key = get_litellm_master_key(data_dir)
    agent_dict["llm_key"] = master_key
    await save_config_locked(config, config.config_path)
    logger.warning(
        "ensure_agent_llm_key: %s; falling back to the shared LiteLLM master "
        "key. This agent has full LiteLLM admin access. Configure "
        "Postgres-backed virtual keys for per-agent isolation, or "
        "set TAOS_DISABLE_AGENT_MASTER_KEY_FALLBACK=1 to refuse "
        "instead.",
        why,
    )
    return master_key, None
