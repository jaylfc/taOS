"""Framework update orchestrator. Owned one module; kicked off from the
route handler via asyncio.create_task per Phase 1's fire-and-track model.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from tinyagentos.containers import (
    exec_in_container, snapshot_create, snapshot_list, snapshot_delete,
)

logger = logging.getLogger(__name__)

SNAPSHOT_PREFIX = "pre-framework-update-"
UPDATE_DEADLINE_SECONDS = 120


async def _prune_old_snapshots(container: str, *, keep: int) -> None:
    """Keep the `keep` most recent pre-framework-update-* snapshots.
    `snapshot_list` returns newest first, so we delete the tail."""
    snaps = await snapshot_list(container, prefix=SNAPSHOT_PREFIX)
    for extra in snaps[keep:]:
        await snapshot_delete(container, extra["name"])


async def _wait_for_bootstrap_ping(
    agent: dict, *, started_at: int, deadline_seconds: int | None = None,
) -> bool:
    """Poll `agent['bootstrap_last_seen_at']` every 500 ms. Returns True the
    first time it exceeds `started_at` (meaning the bridge has called
    `/api/openclaw/bootstrap` since the update started). False on deadline.

    `deadline_seconds=None` reads `UPDATE_DEADLINE_SECONDS` at call time,
    so tests that monkeypatch the module attribute are honoured. Using
    the module attr as a default value bound it at definition time and
    silently ignored the patch — the missing-bootstrap test then waited
    the full 120 s on every CI run instead of the intended 1 s.
    """
    if deadline_seconds is None:
        deadline_seconds = UPDATE_DEADLINE_SECONDS
    deadline = time.time() + deadline_seconds
    while time.time() < deadline:
        last = agent.get("bootstrap_last_seen_at") or 0
        if last > started_at:
            return True
        await asyncio.sleep(0.5)
    return False


def _iso_utc_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")


async def _read_installed_tag(container: str) -> str:
    rc, out = await exec_in_container(
        container, ["cat", "/opt/taos/framework.version"], timeout=10,
    )
    return out.strip() if rc == 0 else ""


async def _mark_failed(agent: dict, reason: str, *, save_config, snapshot: str | None = None) -> None:
    agent["framework_update_status"] = "failed"
    agent["framework_update_started_at"] = None
    agent["framework_update_last_error"] = reason[:500]
    if snapshot is not None:
        agent["framework_last_snapshot"] = snapshot
    await save_config()


async def start_update(agent: dict, manifest: dict, latest: dict, *, save_config) -> None:
    """Full update cycle for one agent. Exceptions inside are caught and
    turned into a 'failed' state — never re-raised (caller is fire-and-forget)."""
    try:
        started_at = int(time.time())
        agent["framework_update_status"] = "updating"
        agent["framework_update_started_at"] = started_at
        agent["framework_update_last_error"] = None
        await save_config()

        container = f"taos-agent-{agent['name']}"
        snap = f"{SNAPSHOT_PREFIX}{latest['tag']}-{_iso_utc_compact()}"
        try:
            await snapshot_create(container, snap)
            agent["framework_last_snapshot"] = snap
            await save_config()
            await _prune_old_snapshots(container, keep=3)
        except Exception as e:
            return await _mark_failed(agent, f"snapshot failed: {e}", save_config=save_config)

        try:
            rc, stderr = await exec_in_container(container, [
                manifest["install_script"], manifest["id"],
                latest["tag"], latest["asset_url"],
            ], timeout=UPDATE_DEADLINE_SECONDS)
        except asyncio.TimeoutError:
            return await _mark_failed(agent, "install script timed out",
                                       save_config=save_config, snapshot=snap)

        if rc != 0:
            return await _mark_failed(agent, f"install script rc={rc}: {stderr[:400]}",
                                       save_config=save_config, snapshot=snap)

        if not await _wait_for_bootstrap_ping(agent, started_at=started_at):
            return await _mark_failed(agent, "bridge did not reconnect within 120s",
                                       save_config=save_config, snapshot=snap)

        installed_tag = await _read_installed_tag(container)
        if installed_tag != latest["tag"]:
            return await _mark_failed(
                agent,
                f"version mismatch: installed={installed_tag!r} expected={latest['tag']!r}",
                save_config=save_config, snapshot=snap,
            )

        agent["framework_version_tag"] = installed_tag
        agent["framework_version_sha"] = latest["sha"]
        agent["framework_update_status"] = "idle"
        agent["framework_update_started_at"] = None
        await save_config()
    except Exception as e:
        logger.exception("unexpected error in start_update for %s", agent.get("name"))
        await _mark_failed(agent, f"unexpected: {e}", save_config=save_config)
