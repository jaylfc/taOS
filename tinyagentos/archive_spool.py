"""Lossless archive spool — failed archive writes are spooled durably and replayed.

This module provides a zero-loss layer for the taOSmd Archive. When
archive.record() fails for any reason, the event is appended to a JSONL spool
file with full fidelity (no stripping, no truncation). A background drain task
replays spooled events when the archive becomes available again.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


async def record_lossless(
    archive: Any,
    event_type: str,
    data: dict,
    *,
    agent_name: str | None = None,
    summary: str | None = None,
    spool_path: Path,
) -> None:
    """Call archive.record with the given arguments; on ANY exception, spool the event.

    The event is appended as one JSON line to spool_path with the exact same
    arguments that were passed to archive.record, plus a UTC timestamp and the
    exception message. The spool file is opened in append mode, flushed, and
    fsynced for durability. Never raises into the caller.

    Args:
        archive: The taOSmd Archive instance (must have a .record method).
        event_type: The event type string (e.g., "conversation", "tool_call").
        data: The event data dictionary.
        agent_name: Optional agent name.
        summary: Optional summary string.
        spool_path: Path to the spool JSONL file.
    """
    try:
        await archive.record(
            event_type,
            data,
            agent_name=agent_name,
            summary=summary,
        )
    except Exception as e:
        logger.error("archive.record failed for event_type=%s: %s", event_type, e)
        utc_now = datetime.now(timezone.utc).isoformat()
        spool_entry = {
            "utc": utc_now,
            "event_type": event_type,
            "data": data,
            "agent_name": agent_name,
            "summary": summary,
            "error": str(e),
        }
        try:
            spool_path.parent.mkdir(parents=True, exist_ok=True)
            with spool_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(spool_entry, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
        except Exception as spool_e:
            logger.critical(
                "archive spool append failed for event_type=%s; entry=%s; error=%s",
                event_type,
                json.dumps(spool_entry, ensure_ascii=False),
                spool_e,
            )


async def drain_spool(archive: Any, spool_path: Path) -> None:
    """Replay spooled events through archive.record, keeping failures.

    Reads the spool file line by line, calls archive.record for each entry,
    and rewrites the spool atomically with only the lines that still fail.
    Uses a temp file + fsync + os.replace for atomicity.

    Args:
        archive: The taOSmd Archive instance (must have a .record method).
        spool_path: Path to the spool JSONL file.
    """
    if not spool_path.exists():
        return

    lines = spool_path.read_text(encoding="utf-8").splitlines()
    if not lines:
        return

    failed_lines: list[str] = []

    for line in lines:
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("archive spool: skipping malformed line: %s", line[:100])
            failed_lines.append(line)
            continue

        try:
            await archive.record(
                entry["event_type"],
                entry["data"],
                agent_name=entry.get("agent_name"),
                summary=entry.get("summary"),
            )
        except Exception as e:
            logger.error(
                "archive spool replay failed for event_type=%s: %s",
                entry.get("event_type"),
                e,
            )
            failed_lines.append(line)

    if failed_lines:
        temp_path = spool_path.with_suffix(spool_path.suffix + ".tmp")
        try:
            temp_path.parent.mkdir(parents=True, exist_ok=True)
            with temp_path.open("w", encoding="utf-8") as f:
                for line in failed_lines:
                    f.write(line + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, spool_path)
        except Exception as e:
            logger.critical("archive spool atomic rewrite failed: %s", e)
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except Exception:
                pass
    else:
        try:
            spool_path.unlink()
        except Exception as e:
            logger.warning("archive spool: failed to remove empty spool file: %s", e)