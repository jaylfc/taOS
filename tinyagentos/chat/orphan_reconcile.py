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
import re
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Canonical minted agent-id format: ``{slug}-{YYYYMMDD}-{HHMMSS}`` (see
# tinyagentos.agent_registry_store.mint_canonical_id), optionally with a
# 2-hex collision suffix. A member matching this is unambiguously an agent.
_CANONICAL_AGENT_ID_RE = re.compile(r"-\d{8}-\d{6}(?:-[0-9a-f]{2})?$")


def _archive_timestamp() -> str:
    """UTC timestamp as YYYYMMDDTHHMMSS, matching agent_archive naming."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def _other_member(channel: dict) -> str | None:
    """Return the single non-"user" member of a 1:1 DM channel, or None.

    Note: this does NOT assert the member is an agent -- a user<->human DM has
    the same ``["user", <handle>]`` shape. Agent classification is done
    separately by ``_is_known_agent_member``.
    """
    members = channel.get("members") or []
    others = [m for m in members if m != "user"]
    if "user" in members and len(others) == 1:
        return others[0]
    return None


def _is_known_agent_member(member: str, known_agent_slugs: set[str]) -> bool:
    """True only when *member* is (or was) an agent, never a human handle.

    A member counts as an agent when it matches the canonical minted-agent-id
    format, or when it resolves to a slug that backs a known/former agent
    (live, archived, or in the registry). A bare human handle that matches no
    known agent and is not canonical-id-shaped is NOT an agent, so its DM is
    left untouched.
    """
    if not member:
        return False
    if _CANONICAL_AGENT_ID_RE.search(member):
        return True
    return member in known_agent_slugs


def _is_orphan_agent_dm(channel: dict, known_agent_slugs: set[str]) -> bool:
    """True only for 1:1 *agent* DM channels (``type='dm'`` with a single
    agent member alongside "user", and no project link). Human DMs and
    user<->human DMs are rejected because their other member is not a known
    or canonical-id-shaped agent."""
    if channel.get("type") != "dm":
        return False
    if channel.get("project_id"):
        return False
    member = _other_member(channel)
    if member is None:
        return False
    return _is_known_agent_member(member, known_agent_slugs)


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


def _registry_slugs(registry_ids) -> set[str]:
    """Derive agent slugs from registry canonical ids.

    A canonical id is ``{slug}-{YYYYMMDD}-{HHMMSS}[-xx]``; stripping the
    timestamp suffix yields the slug a DM channel stores as its member. Lets
    the reconcile recognise a former agent whose config row is gone but whose
    registry record (and thus identity) persists -- e.g. the live-Pi orphans
    'hermes-confirm' and 'deerflow-test'.
    """
    slugs: set[str] = set()
    for cid in registry_ids or ():
        if not cid:
            continue
        slug = _CANONICAL_AGENT_ID_RE.sub("", cid)
        if slug and slug != cid:
            slugs.add(slug)
    return slugs


async def archive_orphan_channel(chat_channels, channel: dict, agent_id: str) -> None:
    """Archive a single orphan DM channel, tagging it with the archive linkage.

    Mirrors the channel-side of ``archive_agent_fully`` step 4b so the channel
    looks identical to one archived through the normal path.
    """
    slug = _other_member(channel) or ""
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


async def reconcile_orphan_dm_channels(
    config, chat_channels, registry_ids=None
) -> list[dict]:
    """Archive agent DM channels whose agent is in no list.

    A channel is an orphan when (1) it is an agent DM -- the non-"user" member
    is a known/former agent (canonical-id-shaped, or a slug present in the live
    config, the archived config, or the registry) and NOT a human handle -- and
    (2) that member backs no LIVE and no ARCHIVED agent. Such channels are
    archived (and linked) rather than left live. Human DMs and user<->human DMs
    are never touched, because their other member is not a known agent.

    ``registry_ids`` is an optional iterable of canonical agent ids (e.g. from
    the agent registry) used to recognise a former agent whose config row is
    gone but whose identity persists.

    Returns a list of ``{"channel_id", "slug", "archived_agent_id"}`` for the
    channels archived on this pass (empty when nothing to do). Idempotent:
    once archived, a channel no longer appears in the non-archived listing, so
    a second run is a no-op.
    """
    live = _live_agent_slugs(config)
    archived = _archived_agent_slugs(config)
    backed = live | archived
    # Slugs known to be agents (so a plain-slug former agent like the live-Pi
    # orphans is classified as an agent DM, not a human DM). Registry slugs and
    # backed slugs together; canonical-id-shaped members are matched by regex.
    known_agent_slugs = backed | _registry_slugs(registry_ids)

    results: list[dict] = []
    channels = await chat_channels.list_channels(archived=False)
    for ch in channels:
        if not _is_orphan_agent_dm(ch, known_agent_slugs):
            continue
        slug = _other_member(ch)
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
