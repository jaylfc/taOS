"""Shared session fields for taOS memory writers.

Every archived or remembered turn carries ``session_id``, ``turn_index`` and
``session_date`` (the turn's own timestamp) so taOSmd's answer_context can
widen a memory hit into its neighbouring turns and date it correctly.
"""

from __future__ import annotations

from datetime import datetime, timezone


def session_date_iso(created_at) -> str:
    """Format a turn's own timestamp (epoch seconds) as ISO 8601 UTC.

    Never uses ingest time. Returns ``""`` when no timestamp is available.
    """
    if created_at is None:
        return ""
    try:
        return datetime.fromtimestamp(float(created_at), tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return ""


def session_fields(session_id: str, turn_index: int, session_date: str) -> dict:
    """Build the {session_id, turn_index, session_date} payload fragment."""
    return {
        "session_id": session_id,
        "turn_index": int(turn_index),
        "session_date": session_date,
    }


async def next_turn_index(count_existing: int) -> int:
    """Next 0-based turn index: the number of turns already stored.

    The count is derived from the stored conversation (passed in by the
    caller), never held in an in-memory dict, so the index survives a
    restart.
    """
    try:
        return int(count_existing or 0)
    except (TypeError, ValueError):
        return 0
