"""Orphan agent DM channel reconciliation.

A taOS agent gets a 1:1 DM channel (``type="dm"``, members ``["user", slug]``)
created on its first successful deploy. Most removal paths archive the channel
alongside the agent, but a few do not -- a failed deploy that gets cleaned up,
or the hard-delete branch in ``archive_agent_fully`` for a never-used config
row. Those paths can leave the DM channel as a live orphan: it shows in the
Messages app but its agent is in no list (active, failed, or archived).

Project principle: nothing is truly deleted, only archived. So we never
hard-delete an orphan channel here -- we ARCHIVE it and tag it with the same
``settings.archived_agent_id`` linkage ``archive_agent_fully`` uses, so it
behaves like any other archived agent channel (and can be purged later from
the archive section).

Everything here is idempotent and safe to run repeatedly. It only touches
agent DM channels with no backing agent; a2a/group channels, project/idea
channels, and human-to-human DMs are left untouched.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _archive_timestamp() -> str:
    """UTC timestamp as YYYYMMDDTHHMMSS, matching agent_archive naming."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def _agent_member(channel: dict) -> str | None:
    """Return the agent-slug member of a DM channel, or None.

    An agent DM channel has exactly the members ``["user", <slug>]``. The
    non-"user" member is the agent slug. A human-to-human DM has two human
    user ids and no "user" sentinel, so we return None for those.
    """
    members = channel.get("members") or []
    others = [m for m in members if m != "user"]
    if "user" in members and len(others) == 1:
        return others[0]
    return None


def _is_agent_dm(channel: dict) -> bool:
    """True only for 1:1 agent DM channels (``type='dm'`` with a single
    agent-slug member alongside the "user" sentinel, and no project link)."""
    if channel.get("type") != "dm":
        return False
    if channel.get("project_id"):
        return False
    return _agent_member(channel) is not None


def _live_agent_slugs(config) -> set[str]:
    return {a.get("name") for a in config.agents if a.get("name")}


def _archived_agent_slugs(config) -> set[str]:
    """Slugs that have a backing archived entry (by archived_slug or
    original.name). These channels are already accounted for by the archive
    flow, so we never re-touch them here."""
    slugs: set[str] = set()
    for entry in config.archived_agents:
        slug = entry.get("archived_slug")
        if slug:
            slugs.add(slug)
        orig_name = (entry.get("original") or {}).get("name")
        if orig_name:
            slugs.add(orig_name)
    return slugs


async def archive_orphan_channel(chat_channels, channel: dict, agent_id: str) -> None:
    """Archive a single orphan DM channel, tagging it with the archive linkage.

    Mirrors the channel-side of ``archive_agent_fully`` step 4b so the channel
    looks identical to one archived through the normal path.
    """
    slug = _agent_member(channel) or ""
    await chat_channels.set_settings(
        channel["id"],
        {
            "archived": True,
            "archived_at": _archive_timestamp(),
            "archived_agent_id": agent_id,
            "archived_agent_slug": slug,
            "orphan_reconciled": True,
        },
    )


async def reconcile_orphan_dm_channels(config, chat_channels) -> list[dict]:
    """Archive live agent DM channels whose agent is in no list.

    A DM channel is an orphan when its agent-slug member matches no live agent
    (``config.agents``) and no archived agent (``config.archived_agents``).
    Such channels are archived (and linked) rather than left live.

    Returns a list of ``{"channel_id", "slug", "archived_agent_id"}`` for the
    channels archived on this pass (empty when nothing to do). Idempotent:
    once archived, a channel no longer appears in the non-archived listing, so
    a second run is a no-op.
    """
    live = _live_agent_slugs(config)
    archived = _archived_agent_slugs(config)
    backed = live | archived

    results: list[dict] = []
    channels = await chat_channels.list_channels(archived=False)
    for ch in channels:
        if not _is_agent_dm(ch):
            continue
        slug = _agent_member(ch)
        if slug in backed:
            continue
        agent_id = uuid.uuid4().hex[:12]
        try:
            await archive_orphan_channel(chat_channels, ch, agent_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "orphan reconcile: failed to archive channel %s (%s): %s",
                ch.get("id"), slug, exc,
            )
            continue
        logger.info(
            "orphan reconcile: archived orphan DM channel %s for absent agent %s",
            ch.get("id"), slug,
        )
        results.append(
            {"channel_id": ch["id"], "slug": slug, "archived_agent_id": agent_id}
        )
    return results
