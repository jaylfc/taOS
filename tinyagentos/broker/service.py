"""Secrets Broker lifecycle service (P0).

Wraps a :class:`BrokerStore` and the code-seeded provider registry. Enforces
deny-by-default (no active grant -> not authorized) and expiry at check time
(a grant past its expires_at is never authorized, even before a sweep flips
its status). Notification dispatch and the external MCP/HTTP surfaces are
P1+; this layer only manages the grant ledger.
"""
from __future__ import annotations

import time

from tinyagentos.broker.providers import PROVIDERS, get_provider
from tinyagentos.broker.store import BrokerStore

# Hard cap on a grant window: 7 days.
MAX_WINDOW_SECONDS = 7 * 24 * 3600  # 604800


class BrokerService:
    def __init__(self, store: BrokerStore):
        self.store = store

    async def request_access(
        self,
        agent_identity: str,
        provider_id: str,
        scopes: list[str] | None = None,
        reason: str | None = None,
        request_id: str | None = None,
    ) -> str:
        """Create a pending grant for (agent, provider). Returns the grant id.

        Raises ValueError if the provider is unknown. Defaults scopes to the
        provider's default_scopes when none are named. Notification dispatch
        is P1; this only records the pending request.
        """
        provider = get_provider(provider_id)
        if provider is None:
            raise ValueError(f"unknown provider_id: {provider_id!r}")
        if scopes is None:
            scopes = list(provider.default_scopes)
        return await self.store.create_pending_grant(
            agent_identity=agent_identity,
            provider_id=provider_id,
            scopes=scopes,
            request_id=request_id,
            note=reason,
        )

    async def approve(
        self, grant_id: str, window_seconds: float, *, now: float | None = None
    ) -> dict:
        """Approve a grant for a bounded window.

        Sets status=active, granted_at=now, expires_at=now+window (capped at
        MAX_WINDOW_SECONDS). Raises ValueError if window_seconds <= 0, the grant
        is unknown, or the grant is not currently ``pending``. For http-proxy
        providers, also mints and returns a virtual cred token under "token".

        Only a pending grant can be approved, so an approve call can never
        resurrect a grant the user previously denied or revoked (nor re-activate
        an expired one).
        """
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        if now is None:
            now = time.time()
        window = min(float(window_seconds), float(MAX_WINDOW_SECONDS))

        grant = await self.store.get_grant(grant_id)
        if grant is None:
            raise ValueError(f"unknown grant_id: {grant_id!r}")
        if grant["status"] != "pending":
            raise ValueError(
                f"grant {grant_id!r} is {grant['status']}, not pending; cannot approve"
            )

        expires_at = now + window
        await self.store.set_grant_status(
            grant_id, "active", granted_at=now, expires_at=expires_at
        )

        result = await self.store.get_grant(grant_id)
        provider = PROVIDERS.get(grant["provider_id"])
        if provider is not None and provider.kind == "http-proxy":
            result["token"] = await self.store.mint_virtual_cred(grant_id)
        return result

    async def deny(self, grant_id: str) -> bool:
        return await self.store.set_grant_status(grant_id, "denied")

    async def revoke(self, grant_id: str) -> bool:
        """Revoke a grant and delete any virtual creds minted for it."""
        ok = await self.store.set_grant_status(grant_id, "revoked")
        await self.store.delete_virtual_creds_for_grant(grant_id)
        return ok

    async def is_authorized(
        self, agent_identity: str, provider_id: str, *, now: float | None = None
    ) -> bool:
        """True iff an active, unexpired grant exists for (agent, provider).

        Scopes refine WHAT an authorized agent may do, not WHETHER it is
        authorized, so an empty-scope active grant still authorizes access.
        Deny-by-default: no active grant -> False.
        """
        if now is None:
            now = time.time()
        grant = await self.store.find_active_grant(agent_identity, provider_id, now)
        return grant is not None

    async def authorize_token(
        self, token: str, *, now: float | None = None
    ) -> dict | None:
        """Resolve an http-proxy virtual token to its grant context.

        Returns {agent_identity, provider_id, scopes} when the token maps to an
        active, unexpired grant, else None.
        """
        if now is None:
            now = time.time()
        cred = await self.store.lookup_virtual_cred(token)
        if cred is None:
            return None
        grant = await self.store.get_grant(cred["grant_id"])
        if grant is None or grant["status"] != "active":
            return None
        expires_at = grant["expires_at"]
        if expires_at is not None and expires_at <= now:
            return None
        return {
            "agent_identity": grant["agent_identity"],
            "provider_id": grant["provider_id"],
            "scopes": grant["scopes"],
        }

    async def sweep_expired(self, now: float | None = None) -> int:
        if now is None:
            now = time.time()
        return await self.store.sweep_expired(now)
