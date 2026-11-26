"""Shared SSE replay helpers for the durable outbox (all agents).

Both the registry-agent self chat route and the openclaw events route use
this to replay persisted outbox rows before subscribing to the live stream,
with dedupe against the live queue.
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


def resume_id(request) -> int | None:
    """Last-Event-ID header (or ?since=) as an int; anything else is absent."""
    raw = request.headers.get("last-event-id") or request.query_params.get("since")
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def frame_id(frame: str) -> int | None:
    if not frame.startswith("id: "):
        return None
    try:
        return int(frame.split("\n", 1)[0][4:])
    except ValueError:
        return None


async def outbox_event_stream(bridge, outbox, slug, last_id, request=None):
    """Async generator: replay outbox rows, then live subscribe, deduped."""
    replay = []
    if outbox is not None:
        try:
            replay = await (
                outbox.since(slug, last_id) if last_id is not None
                else outbox.undelivered(slug)
            )
        except Exception:
            logger.exception("outbox replay failed for %s", slug)

    seen = {rid for rid, _ in replay}

    async def _mark_delivered(row_id: int) -> None:
        if outbox is None:
            return
        try:
            await outbox.mark_delivered(row_id)
        except Exception:
            logger.exception("outbox mark_delivered failed for %s", slug)

    try:
        for row_id, payload in replay:
            yield (
                f"id: {row_id}\nevent: user_message\n"
                f"data: {json.dumps(payload)}\n\n"
            )
            await bridge.mark_user_message_delivered(slug, payload)
            await _mark_delivered(row_id)
        async for frame in bridge.subscribe(slug):
            rid = frame_id(frame)
            if rid is not None:
                if rid in seen or (last_id is not None and rid <= last_id):
                    continue
                seen.add(rid)
            yield frame
            if rid is not None:
                await _mark_delivered(rid)
            if request is not None:
                try:
                    if await request.is_disconnected():
                        break
                except Exception:
                    pass
    except Exception:
        logger.exception("outbox SSE generator error for %s", slug)
