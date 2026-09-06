from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx
    from tinyagentos.knowledge_store import KnowledgeStore

logger = logging.getLogger(__name__)

_POLL_LOOP_INTERVAL = 60  # seconds between poll-loop ticks
_MAX_DAILY_INTERVAL = 86400  # 24 hours — polling floor for sub-daily sources
DECAY_FLOOR = 2592000  # 30 days in seconds — absolute minimum polling frequency


def compute_next_interval(
    current_interval: int,
    decay_rate: float,
    changed: bool,
    base_frequency: int,
    stop_after_days: int,
    pinned: bool = False,
) -> int:
    """Compute the next polling interval after a poll.

    Decay floors at 30 days (DECAY_FLOOR). Items never stop polling automatically;
    only manual user action can disable monitoring.

    Returns:
        int: new interval in seconds (always > 0)
    """
    if pinned:
        return base_frequency

    if changed:
        return base_frequency

    new_interval = int(current_interval * decay_rate)

    # Clamp to 30-day floor — never stop automatically regardless of stop_after_days
    new_interval = min(new_interval, DECAY_FLOOR)

    # Cap at 24 hours only for sources whose base frequency is below 24 hours.
    # Sources already at 24-hour frequency (article, youtube) can decay beyond it.
    if base_frequency < _MAX_DAILY_INTERVAL:
        return min(new_interval, _MAX_DAILY_INTERVAL)

    return new_interval


class MonitorService:
    """Background service that polls monitored KnowledgeItems for changes.

    Start with ``start()`` inside the app lifespan and stop with ``stop()``.
    ``poll_item()`` and ``get_due_items()`` are public for testing.
    """

    def __init__(self, store: "KnowledgeStore", http_client: "httpx.AsyncClient") -> None:
        self._store = store
        self._http_client = http_client
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the 60-second poll loop as a background asyncio task."""
        self._task = asyncio.create_task(self._loop())
        logger.info("MonitorService started")

    async def stop(self) -> None:
        """Cancel the poll loop and wait for it to finish."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("MonitorService stopped")

    async def _loop(self) -> None:
        """Main poll loop: runs every 60 seconds."""
        while True:
            try:
                due = await self.get_due_items()
                for item in due:
                    try:
                        await self.poll_item(item["id"])
                    except Exception as exc:
                        logger.warning("poll_item failed for %s: %s", item["id"], exc)
            except Exception as exc:
                logger.warning("MonitorService loop error: %s", exc)
            await asyncio.sleep(_POLL_LOOP_INTERVAL)

    async def get_due_items(self) -> list[dict]:
        """Return items whose next poll time has passed.

        An item is due when ``last_poll + current_interval <= now``.
        Items with ``current_interval == 0`` (files, manual) are excluded.
        Items whose monitor config is missing or empty are excluded.
        """
        now = time.time()
        items = await self._store.list_items(status="ready")
        due = []
        for item in items:
            m = item.get("monitor") or {}
            current_interval = m.get("current_interval", 0)
            last_poll = m.get("last_poll", 0)
            if current_interval <= 0:
                continue
            if last_poll + current_interval <= now:
                due.append(item)
        return due

    async def poll_item(self, item_id: str) -> None:
        """Re-fetch one item, diff against last snapshot, and update monitor config."""
        item = await self._store.get_item(item_id)
        if item is None:
            return

        source_type = item["source_type"]
        monitor = dict(item.get("monitor") or {})

        new_content, changed = await self._fetch_current_content(source_type, item)

        # Record snapshot
        content_hash = hashlib.sha256((new_content or "").encode()).hexdigest()
        old_hash = monitor.get("last_hash", "")
        diff = {"changed": changed, "old_hash": old_hash, "new_hash": content_hash}
        await self._store.add_snapshot(
            item_id,
            content_hash=content_hash,
            diff_json=diff,
            metadata_json={},
        )

        # Update content if changed
        if changed and new_content:
            await self._store.update_item(item_id, content=new_content)

        # Compute next interval
        next_interval = compute_next_interval(
            current_interval=monitor.get("current_interval", monitor.get("frequency", 86400)),
            decay_rate=monitor.get("decay_rate", 1.5),
            changed=changed,
            base_frequency=monitor.get("frequency", 86400),
            stop_after_days=monitor.get("stop_after_days", 14),
            pinned=monitor.get("pinned", False),
        )

        monitor["last_poll"] = time.time()
        monitor["last_hash"] = content_hash
        monitor["current_interval"] = next_interval

        await self._store.update_item(item_id, monitor=monitor)

    async def _fetch_current_content(
        self, source_type: str, item: dict
    ) -> tuple[str, bool]:
        """Fetch the current content for an item and determine if it changed.

        Returns (new_content, changed). For source types without a fetcher
        yet (reddit, youtube, x, github), returns ("", False) as a safe
        no-op until platform adapters are added in later build steps.
        """
        if source_type == "article":
            return await self._fetch_article(item)
        # Platform-specific fetchers added in build steps 3-6
        return "", False

    async def _fetch_article(self, item: dict) -> tuple[str, bool]:
        """Re-fetch an article URL and check if content changed."""
        try:
            resp = await self._http_client.get(
                item["source_url"], timeout=30, follow_redirects=True
            )
            resp.raise_for_status()
            new_content = resp.text
            old_content = item.get("content", "")
            changed = new_content.strip() != old_content.strip()
            return new_content, changed
        except Exception as exc:
            logger.warning("Article re-fetch failed for %s: %s", item["source_url"], exc)
            return "", False
