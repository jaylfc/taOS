"""agent_dm_owner -- record who owns each agent DM channel.

An agent DM channel is created with the members ``["user", <agent slug>]``;
the literal ``"user"`` stands for "the person who deployed the agent", not a
real user id. Channel reads compare the caller's user id to the member list,
so without this step the agent's owner would lose their own DM. This step
stamps ``settings.owner_user_id`` on every agent DM channel from the agent's
registry owner. The channel's reader check accepts that id as a member.

Idempotent: a channel that already carries ``owner_user_id`` is not touched,
and an agent the registry cannot attribute is left alone (admins still read it).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def agent_owner_for_slug(config, registry, slug: str) -> str | None:
    """The registry owner of the config agent named *slug*, or None."""
    if registry is None or not slug:
        return None
    for agent in getattr(config, "agents", None) or []:
        if agent.get("name") == slug:
            cid = agent.get("registry_canonical_id")
            if not cid:
                return None
            try:
                row = await registry.get(cid)
            except RuntimeError:
                return None
            return (row or {}).get("user_id") or None
    return None


async def backfill_agent_dm_owners(config, registry, chat_channels) -> list[str]:
    """Stamp ``owner_user_id`` on agent DM channels; return the channel ids changed."""
    changed: list[str] = []
    for channel in await chat_channels.list_channels():
        if channel.get("type") != "dm":
            continue
        settings = channel.get("settings") or {}
        if settings.get("owner_user_id"):
            continue
        members = channel.get("members") or []
        others = [m for m in members if m != "user"]
        if "user" not in members or len(others) != 1:
            continue
        owner = await agent_owner_for_slug(config, registry, others[0])
        if not owner:
            continue
        await chat_channels.set_settings(channel["id"], {"owner_user_id": owner})
        changed.append(channel["id"])
    return changed
