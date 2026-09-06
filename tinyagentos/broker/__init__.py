"""taOS Secrets Broker (P0): grant ledger + lifecycle service.

Backend-only. No MCP server, HTTP proxy, or routes yet. The broker hands
external agents time-boxed, revocable, per-(agent, provider) access to
third-party secrets, routed and audited through taOS. It never stores
plaintext provider keys; those live in ``tinyagentos.secrets.SecretsStore``
under each provider's ``secret_ref``.
"""
from __future__ import annotations

from tinyagentos.broker.providers import PROVIDERS, Provider, get_provider
from tinyagentos.broker.service import BrokerService
from tinyagentos.broker.store import BrokerStore

__all__ = [
    "PROVIDERS",
    "Provider",
    "get_provider",
    "BrokerService",
    "BrokerStore",
]
