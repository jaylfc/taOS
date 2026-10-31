"""Registry guest agents for chat.

A guest is an active agent-registry row holding an unexpired ``chat_session``
grant. It never enters ``config.agents``, so the chat router and the Messages
agent list resolve it here instead of through ``find_agent``.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from tinyagentos.agent_token_auth import _grant_unexpired
from tinyagentos.chat.orphan_reconcile import _CANONICAL_AGENT_ID_RE

logger = logging.getLogger(__name__)

CHAT_SCOPE = "chat_session"


def _stores(state):
    registry = getattr(state, "agent_registry", None)
    grants = getattr(state, "agent_grants", None)
    if registry is None or grants is None:
        return None, None
    return registry, grants


async def _has_chat_grant(grants, canonical_id: str) -> bool:
    now = datetime.now(timezone.utc)
    for g in await grants.list_grants(canonical_id):
        if g.get("scope") == CHAT_SCOPE and _grant_unexpired(g.get("expires_at"), now):
            return True
    return False


def _guest(row: dict, slug: str) -> dict:
    return {
        "name": slug,
        "canonical_id": row["canonical_id"],
        "handle": row.get("handle", ""),
        "guest": True,
    }


async def resolve_guest(state, slug) -> dict | None:
    """Return the guest entry for *slug*, or None when it is not a guest."""
    registry, grants = _stores(state)
    if registry is None or not slug:
        return None
    try:
        row = await registry.get_by_slug(slug)
        if not row or row.get("status") != "active":
            return None
        if not await _has_chat_grant(grants, row["canonical_id"]):
            return None
        return _guest(row, slug)
    except Exception:  # noqa: BLE001 - a guest lookup failure must not break routing
        logger.warning("guest lookup failed for %s", slug, exc_info=True)
        return None


async def list_guests(state) -> list[dict]:
    """Return every active registry agent holding an unexpired chat_session grant."""
    registry, grants = _stores(state)
    if registry is None:
        return []
    out: list[dict] = []
    try:
        for row in await registry.list_all(status="active"):
            cid = row.get("canonical_id") or ""
            slug = _CANONICAL_AGENT_ID_RE.sub("", cid)
            if not slug or slug == cid:
                continue
            if await _has_chat_grant(grants, cid):
                out.append(_guest(row, slug))
    except Exception:  # noqa: BLE001
        logger.warning("guest listing failed", exc_info=True)
        return []
    return out
