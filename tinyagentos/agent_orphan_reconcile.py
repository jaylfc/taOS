"""Orphaned agent-container reconciliation.

A taOS agent container is named ``taos-agent-<slug>`` (legacy: ``taos-<slug>``).
Every removal path is supposed to archive the container alongside the agent
record, but a record can lose its container link, or a deploy can be cleaned up
partially, leaving a real container running with NO backing agent in any list
(live or archived). Those containers are invisible in the UI yet still consume
RAM/disk on the host.

This mirrors the channel-side :mod:`tinyagentos.chat.orphan_reconcile`: it finds
taOS containers with no backing agent record and reports them (and optionally
archives them through the same snapshot-then-archive path the delete flow uses,
so they too get a restore point). It is idempotent and only ever touches
containers whose name carries the taOS prefix -- a non-taOS container on the
same host is never inspected or removed.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _slug_from_container_name(name: str) -> str:
    """Strip the taOS container prefix to recover the agent slug.

    Returns an empty string when nothing survives -- a container named exactly
    ``taos-`` or ``taos-agent-`` is not a valid agent container and callers must
    skip it rather than build a malformed (empty-slug) archive row.
    """
    slug = name
    for prefix in ("taos-agent-", "taos-"):
        if slug.startswith(prefix):
            slug = slug[len(prefix):]
            break
    return slug.strip()


def _backed_container_names(config) -> set[str]:
    """Container names that DO belong to a known agent (live or archived).

    For each live agent we accept both the current ``taos-agent-<slug>`` and
    legacy ``taos-<slug>`` names plus any explicit ``container_name``. For each
    archived entry we accept the recorded ``container_name`` and the derived
    names from its slug. Anything not in this set is, by definition, unbacked.
    """
    from tinyagentos.containers import candidate_agent_container_names

    backed: set[str] = set()
    for agent in getattr(config, "agents", []) or []:
        slug = agent.get("name")
        if slug:
            backed.update(candidate_agent_container_names(slug, agent.get("display_name")))
        explicit = agent.get("container_name") or agent.get("container")
        if explicit:
            backed.add(explicit)
    for entry in getattr(config, "archived_agents", []) or []:
        explicit = entry.get("container_name")
        if explicit:
            backed.add(explicit)
        slug = entry.get("archived_slug") or (entry.get("original") or {}).get("name")
        if slug:
            backed.update(candidate_agent_container_names(slug))
    return backed


async def find_orphaned_agent_containers(config) -> list[dict]:
    """Return taOS containers with no backing agent record (live or archived).

    Each item is ``{"name", "project"}``. Only containers carrying the taOS
    prefix are considered; non-taOS containers are never returned.
    """
    from tinyagentos.containers import list_all_taos_containers

    backed = _backed_container_names(config)
    orphans: list[dict] = []
    for c in await list_all_taos_containers():
        if c["name"] not in backed:
            orphans.append(c)
    return orphans


async def reconcile_orphaned_agent_containers(
    request, *, clean: bool = False
) -> list[dict]:
    """Find (and optionally clean) taOS containers with no backing agent.

    With ``clean=False`` (default) this only reports -- safe to call anywhere.
    With ``clean=True`` each orphan is run through the same snapshot-then-archive
    path the delete flow uses (:func:`archive_agent_fully` semantics) by way of
    :func:`destroy_container` AFTER a snapshot, so an operator keeps a restore
    point. The project principle is "nothing is truly deleted": we snapshot the
    orphan, then remove the live container, recording a tombstone-style archive
    entry so it shows in Archived and can be restored or purged.

    Idempotent: once an orphan is archived its container is gone, so a second
    pass finds nothing. Never touches non-taOS containers (the listing is
    prefix-filtered) and never touches a container that backs a known agent.

    Returns a list of ``{"name", "project", "action"}`` describing this pass.
    """
    from datetime import datetime, timezone

    from tinyagentos.containers import (
        snapshot_create,
        stop_container,
        destroy_container,
    )
    from tinyagentos.config import save_config_locked

    config = request.app.state.config
    orphans = await find_orphaned_agent_containers(config)
    results: list[dict] = []

    for orphan in orphans:
        name = orphan["name"]
        # A container named exactly ``taos-`` / ``taos-agent-`` strips to an
        # empty slug: it is not a valid taOS agent container, so never archive
        # it (an empty-slug row breaks the Archived UI + restore/purge).
        slug = _slug_from_container_name(name)
        if not slug:
            logger.warning(
                "orphan container reconcile: skipping bare-prefix container %s "
                "(no slug after stripping taOS prefix)",
                name,
            )
            results.append({**orphan, "action": "skipped_empty_slug"})
            continue

        if not clean:
            logger.info("orphan container reconcile: found unbacked container %s", name)
            results.append({**orphan, "action": "found"})
            continue

        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        snapshot_name = f"taos-archive-{ts}"
        try:
            await stop_container(name, force=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("orphan container reconcile: stop failed for %s: %s", name, exc)

        snap = await snapshot_create(name, snapshot_name)
        if not snap.get("success"):
            # Snapshot is the restore guarantee; without it we do NOT remove the
            # container (never destroy without a restore point). Report and skip.
            logger.warning(
                "orphan container reconcile: snapshot failed for %s (%s); "
                "leaving container intact",
                name, (snap.get("output") or "").strip(),
            )
            results.append({**orphan, "action": "snapshot_failed"})
            continue

        import uuid

        archive_id = uuid.uuid4().hex[:12]
        # ``slug`` was derived (and validated non-empty) at the top of the loop.
        archive_entry = {
            "id": archive_id,
            "archived_at": ts,
            "archived_slug": slug,
            "container_name": name,
            "snapshot_name": snapshot_name,
            "export_path": None,
            "archive_dir": f"archive/{slug}-{ts}",
            "original": {"name": slug},
            "orphan_reconciled": True,
        }
        config.archived_agents.append(archive_entry)
        await save_config_locked(config, config.config_path)
        logger.info(
            "orphan container reconcile: archived unbacked container %s as %s",
            name, archive_id,
        )
        results.append({**orphan, "action": "archived", "archive_id": archive_id})

    return results
