"""Per-agent LLM key service (``app.state.llm_proxy``).

LiteLLM removal stage 2b-2a: there is no LiteLLM process any more. Every
agent's chat, stream and embedding goes through the in-process LLM gateway
(``tinyagentos.llm_gateway``), and agents reach it at their own
``127.0.0.1:4000`` through the ``taos-proxy-litellm`` incus proxy device.

What is left here is local and needs no process: minting, re-scoping and
deleting per-agent keys in taOS's own key store (``litellm_keystore``, the
store the gateway authenticates against) and reading their usage from the
budget store. The class and module keep their names until the cosmetic
rename (stage 2b-2b).
"""
from __future__ import annotations

import logging
from pathlib import Path

from tinyagentos.providers import CLOUD_TYPES as CLOUD_BACKEND_TYPES  # noqa: F401  (public re-export)
from tinyagentos.litellm_config import EMBEDDING_ALIAS  # noqa: F401  (public re-export)

__all__ = ["EMBEDDING_ALIAS", "CLOUD_BACKEND_TYPES", "scoped_key_models", "LLMProxy"]

logger = logging.getLogger(__name__)


def scoped_key_models(models: list[str] | None) -> list[str]:
    """Return the model list for a newly-minted agent key.

    ``models or ["default"]`` so an agent deployed without an explicit
    model is scoped to the default chat alias (still usable), not minted
    with an empty allowlist that the gateway would deny-all.
    The embedding alias (``taos-embedding-default``) is always appended
    so an agent that embeds does not lose access on model change.
    """
    return list(dict.fromkeys((models or ["default"]) + [EMBEDDING_ALIAS]))


class LLMProxy:
    """Per-agent key admin over the local key and budget stores.

    ``port`` is the host port the LiteLLM proxy used to listen on
    (``server.litellm_port``). Nothing listens there any more; the startup
    cutover reads it only to recognise proxy devices that still point at it
    and move them to the gateway.
    """

    def __init__(self, port: int = 7834, data_dir: Path | None = None):
        self.port = port
        self._data_dir = Path(data_dir) if data_dir is not None else None
        self._keystore_cache = None
        self._budget_store_cache = None

    def _base(self) -> Path:
        if self._data_dir is None:
            raise RuntimeError("LLMProxy has no data_dir: the key store location is unknown")
        return self._data_dir

    def _keystore(self):
        """Lazily open the local key store."""
        if self._keystore_cache is None:
            from tinyagentos.litellm_keystore import (
                LiteLLMKeyStore,
                default_keystore_path,
            )
            self._keystore_cache = LiteLLMKeyStore(default_keystore_path(self._base()))
        return self._keystore_cache

    def _budget_store(self):
        """Lazily open the local budget store."""
        if self._budget_store_cache is None:
            from tinyagentos.agent_budget_store import (
                AgentBudgetStore,
                default_budget_path,
            )
            self._budget_store_cache = AgentBudgetStore(default_budget_path(self._base()))
        return self._budget_store_cache

    # A legacy LiteLLM Postgres virtual key is unknown to the store:
    # re-scope and delete answer False for it (logged). With LiteLLM gone it
    # authenticates nowhere, so that agent needs a re-key or redeploy.

    async def create_agent_key(self, agent_name: str, models: list[str] | None = None,
                                max_budget: float | None = None) -> str | None:
        """Mint a per-agent key in the local key store."""
        try:
            allowed = scoped_key_models(models)
            token = self._keystore().mint(agent_name, allowed)
        except Exception as e:
            logger.warning("key store mint failed for %s: %s", agent_name, e)
            return None
        if max_budget is not None:
            try:
                self._budget_store().set_budget(agent_name, max_budget)
            except Exception as e:
                logger.warning("budget set failed for %s: %s", agent_name, e)
        return token

    async def update_agent_key(self, key: str, models: list[str]) -> bool:
        """Re-scope an existing key's allowed models in the local key store.

        Keeps the key VALUE unchanged (no container env push / restart needed):
        the framework's ``/v1/models`` with this key then reflects the new
        permitted set. An empty scope is a caller error and is refused.
        """
        if not key or not models:
            logger.warning("update_agent_key needs key + models; refusing")
            return False
        try:
            allowed = scoped_key_models(models)
            ok = self._keystore().set_models(key, allowed)
        except Exception as e:
            logger.warning("key re-scope failed: %s", e)
            return False
        if not ok:
            logger.warning(
                "update_agent_key: key is not in the local key store (a legacy "
                "LiteLLM Postgres key?); not re-scoped"
            )
        return ok

    async def delete_agent_key(self, key: str) -> bool:
        """Delete a per-agent key from the local key store (revoking the
        gateway key minted from it)."""
        if not key:
            return False
        try:
            ok = self._keystore().delete(key)
        except Exception as e:
            logger.warning("key delete failed: %s", e)
            return False
        if not ok:
            logger.warning(
                "delete_agent_key: key is not in the local key store (a legacy "
                "LiteLLM Postgres key?); nothing deleted"
            )
        return ok

    async def get_key_usage(self, key: str) -> dict | None:
        """Usage for an agent's key, from the local key and budget stores.

        Shaped like LiteLLM's ``/key/info`` answer (``{"key", "info": {...}}``)
        so existing readers keep working; ``None`` for an unknown key.
        """
        if not key:
            return None
        try:
            rec = self._keystore().lookup(key)
        except Exception as e:
            logger.warning("key usage lookup failed: %s", e)
            return None
        if rec is None:
            return None
        agent = rec["agent"]
        budget = None
        try:
            budget = self._budget_store().get(agent)
        except Exception as e:
            logger.warning("budget lookup failed for %s: %s", agent, e)
        return {
            "key": None,  # never echo a credential
            "info": {
                "key_alias": f"taos-{agent}",
                "models": list(rec.get("allowed_models") or []),
                "spend": float((budget or {}).get("spend_usd") or 0.0),
                "max_budget": (budget or {}).get("max_budget_usd"),
                "metadata": {"agent": agent, "managed_by": "tinyagentos"},
            },
        }
