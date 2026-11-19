"""Per-project A2A (agent-to-agent) coordination channel.

Owns the invariant that every active project has exactly one chat channel
with `name="a2a"`, `type="group"`, `settings.kind="a2a"`. Single source of
truth: `ensure_a2a_channel`. Called from project route hooks and from the
startup backfill.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

A2A_NAME = "a2a"
A2A_TYPE = "group"
A2A_KIND = "a2a"

# Per-project lock so concurrent ensure_a2a_channel calls (e.g. simultaneous
# add_member requests during backfill) serialize on the read-modify-write of
# channel members. Without this, two callers can each compute a stale member
# diff and clobber each other's add/remove operations.
_A2A_LOCKS: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


async def _find_a2a_channels(channel_store, project_id: str) -> list[dict]:
    """Return active, fully-identified A2A channel rows for a project, oldest-first.

    Identification requires all four conditions:
    - project_id matches
    - not archived
    - name == A2A_NAME
    - type == A2A_TYPE
    - settings.kind == A2A_KIND

    The store already returns rows ordered by created_at ASC, so the first
    element of the returned list is the canonical A2A channel. Archived rows
    are excluded so a previously-archived duplicate can never be re-elected
    as canonical.
    """
    channels = await channel_store.list_channels(
        project_id=project_id, archived=False,
    )
    return [
        ch for ch in channels
        if ch.get("name") == A2A_NAME
        and ch.get("type") == A2A_TYPE
        and (ch.get("settings") or {}).get("kind") == A2A_KIND
    ]


def _build_agent_lookups(config):
    """Return (by_id, by_name) dicts from config.agents, or (None, None) when config is None."""
    if config is None:
        return None, None
    agents = getattr(config, "agents", None) or []
    by_id: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    for agent in agents:
        aid = agent.get("id")
        name = agent.get("name")
        if aid:
            by_id[aid] = agent
        if name:
            by_name[name] = agent
    return by_id, by_name


async def _resolve_member_names(
    member_rows: list[dict], config, agent_registry=None
) -> set[str]:
    """Convert project_members rows to agent names for channel membership.

    project_members.member_id stores an agent's hex id (e.g. "91a640130122")
    for native members, or a clone slug like "<source_id>-<project_slug>" for
    clones. Both need resolving to a name before being stored in the channel's
    members list, which the @mention parser and router expect.

    Resolution strategy, in order per member:
    - config.agents: id match first, then name match (handles the case where
      names are already stored for older rows that used names as member_id).
    - Human members (member_kind="human"): kept under their contact id.
      They are not agents, so no config entry will ever match them, and the
      contact id is the identity the peer/collab paths address them by.
    - Agent registry: a native member whose canonical id is absent from
      config.agents (registered via the agent registry, not the static
      config) resolves to its registry handle — the routing name the
      @mention parser and router look up via get_by_handle. An identity
      with no handle falls back to the slug of its display name/framework.
    - Members matching none of the above (deleted agents) are dropped with
      a debug log. A registry lookup that raises is treated as no match,
      so an unusable registry cannot fail the whole channel sync.

    If config is None (test path), member_id is returned as-is so that tests
    that already store names in project_members continue to work unmodified.
    """
    by_id, by_name = _build_agent_lookups(config)
    if by_id is None:
        return {m["member_id"] for m in member_rows}

    resolved: set[str] = set()
    for m in member_rows:
        mid = m["member_id"]
        agent = by_id.get(mid) or by_name.get(mid)
        if agent is not None:
            name = agent.get("name")
            if name:
                resolved.add(name)
            continue
        if m.get("member_kind") == "human":
            resolved.add(mid)
            continue
        if agent_registry is not None:
            try:
                identity = await agent_registry.get(mid)
            except Exception:
                # An unusable registry (e.g. uninitialised in a minimal
                # app, or a transient store error) must not fail the
                # whole channel sync: treat it as no match and drop
                # the member like any other unknown id.
                logger.debug(
                    "a2a: registry lookup for member_id %r failed — skipping",
                    mid,
                    exc_info=True,
                )
                identity = None
            if identity is not None:
                handle = identity.get("handle")
                if not handle:
                    from tinyagentos.agent_registry_store import (
                        agent_slug_or_fallback,
                    )

                    handle = agent_slug_or_fallback(
                        identity.get("display_name")
                        or identity.get("framework")
                        or mid
                    )
                resolved.add(handle)
                continue
        logger.debug(
            "a2a: member_id %r not found in config agents — skipping", mid
        )
    return resolved


def _resolve_lead_names(project: dict, config) -> list[str]:
    """Return the resolved agent name for the project's single exclusive lead.

    The lead designation lives on the project as a single ``lead_member_id``
    pointer (D7), not on the members. At most one name is ever returned; an
    empty list means no lead. Uses the same config-lookup strategy as
    _resolve_member_names so the quiet-filter and settings.leads stay in the
    name-space the @mention parser and router expect.
    """
    lead_id = project.get("lead_member_id")
    if not lead_id:
        return []
    by_id, by_name = _build_agent_lookups(config)
    if by_id is None:
        # No config (test path): use the member id directly.
        return [lead_id]
    agent = by_id.get(lead_id) or by_name.get(lead_id)
    if agent is None:
        logger.debug("a2a: lead member_id %r not found in config — skipping", lead_id)
        return []
    name = agent.get("name")
    return [name] if name else []


async def ensure_a2a_channel(
    channel_store, project_store, project_id: str, *, config=None,
    agent_registry=None
) -> dict:
    """Create the A2A channel for project_id if missing, sync its members
    to the project's current native+clone members, return the channel row.

    config: the app's AppConfig (or any object with a .agents list of dicts
    with "id" and "name" keys). When provided, project member IDs are resolved
    to agent names before being stored in the channel — the @mention parser and
    router both operate on names. When None (test path with name-keyed members),
    member_ids are used as-is.

    agent_registry: the app's AgentRegistryStore (or None). When provided,
    native members absent from config.agents resolve through the registry to
    their handle; human members are always kept under their contact id.
    Call sites that run without it keep the previous behaviour (registry-only
    members are dropped on sync), so every production call site passes it.

    Also syncs settings.leads from the project's exclusive lead_member_id so that
    the router can give the lead visibility into all channel messages regardless
    of response_mode.

    Idempotent. Serialized per project_id via _A2A_LOCKS to prevent racing
    member-sync diffs when multiple callers fire concurrently.
    """
    async with _A2A_LOCKS[project_id]:
        project = await project_store.get_project(project_id)
        project_members = await project_store.list_members(project_id)
        expected = await _resolve_member_names(
            project_members, config, agent_registry
        )
        expected_leads = _resolve_lead_names(project, config)

        matches = await _find_a2a_channels(channel_store, project_id)
        if not matches:
            created_by = project.get("created_by", "system") if project else "system"
            return await channel_store.create_channel(
                name=A2A_NAME,
                type=A2A_TYPE,
                created_by=created_by,
                members=sorted(expected),
                description="Agent coordination channel.",
                settings={"kind": A2A_KIND, "leads": expected_leads},
                project_id=project_id,
            )

        # Reconcile duplicates: oldest is canonical, archive the rest.
        # Defensive: pre-lock data, manual DB tampering, or migrations could
        # have produced more than one A2A channel for a project.
        existing = matches[0]
        for dup in matches[1:]:
            logger.warning(
                "a2a duplicate channel %s for project %s — archiving",
                dup.get("id"), project_id,
            )
            await channel_store.set_settings(dup["id"], {"archived": True})

        # Sync members.
        current = set(existing.get("members") or [])
        to_add = expected - current
        to_remove = current - expected
        for slug in sorted(to_add):
            await channel_store.add_member(existing["id"], slug)
        for slug in sorted(to_remove):
            await channel_store.remove_member(existing["id"], slug)

        # Sync settings.leads — always write so the field stays current even if
        # the member set didn't change (e.g. is_lead toggled without add/remove).
        current_settings = dict(existing.get("settings") or {})
        leads_changed = current_settings.get("leads") != expected_leads
        if leads_changed:
            current_settings["leads"] = expected_leads
            await channel_store.set_settings(existing["id"], current_settings)

        if not to_add and not to_remove and not leads_changed:
            return existing

        return await channel_store.get_channel(existing["id"])


async def backfill_all(
    channel_store, project_store, *, config=None, agent_registry=None
) -> int:
    """Call ensure_a2a_channel for every active project. Returns count synced.

    Per-project failures are logged and do not stop the loop.
    """
    projects = await project_store.list_projects(status="active")
    count = 0
    for p in projects:
        try:
            await ensure_a2a_channel(
                channel_store, project_store, p["id"], config=config,
                agent_registry=agent_registry,
            )
            count += 1
        except Exception:
            logger.exception("a2a backfill failed for project %s", p.get("id"))
    return count
