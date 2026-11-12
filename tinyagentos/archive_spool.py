"""Lossless archive spool — failed archive writes are spooled durably and replayed.

This module provides a zero-loss layer for the taOSmd Archive. When
archive.record() fails for any reason, the event is appended to a JSONL spool
file with full fidelity (no stripping, no truncation). A background drain task
replays spooled events when the archive becomes available again.

Delivery is AT-LEAST-ONCE. After a crash, the .draining file is replayed in
full, which may cause duplicate deliveries. Callers must be idempotent or use
an archive-side dedup key (out of scope). If append-back of a failed line
raises, the loop stops, .draining is kept, and lines already appended back
may be replayed twice on the next drain.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_drain_lock = asyncio.Lock()


def _spool_default(obj: Any) -> dict:
    """JSON default serializer for spool entries.

    Returns a type-tagged representation that preserves byte-exact data.
    """
    if isinstance(obj, bytes):
        return {"__spool_type__": "bytes", "b64": base64.b64encode(obj).decode()}
    if isinstance(obj, datetime):
        return {"__spool_type__": "datetime", "iso": obj.isoformat()}
    if isinstance(obj, Path):
        return {"__spool_type__": "Path", "str": str(obj)}
    return {"__spool_type__": type(obj).__name__, "repr": repr(obj)}


def _spool_restore(d: dict) -> Any:
    """Object hook for json.loads to restore tagged values.

    Turns {"__spool_type__":"bytes","b64":...} back into bytes,
    "datetime" back into datetime.fromisoformat(iso),
    "Path" back into Path(str); leaves any other tag as the dict.
    """
    if not isinstance(d, dict) or "__spool_type__" not in d:
        return d
    tag = d["__spool_type__"]
    if tag == "bytes" and "b64" in d:
        return base64.b64decode(d["b64"])
    if tag == "datetime" and "iso" in d:
        return datetime.fromisoformat(d["iso"])
    if tag == "Path" and "str" in d:
        return Path(d["str"])
    return d


def _append_spool_line(spool_path: Path, line: str) -> None:
    """Append a line to the spool file with flush and fsync for durability."""
    spool_path.parent.mkdir(parents=True, exist_ok=True)
    file_existed = spool_path.exists()
    dir_fd = None
    try:
        with spool_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
            if not file_existed:
                dir_fd = os.open(spool_path.parent, os.O_RDONLY)
                os.fsync(dir_fd)
    finally:
        if dir_fd is not None:
            os.close(dir_fd)


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
    except Exception as e:  # noqa: BLE001
        logger.error("archive.record failed for event_type=%s: %s", event_type, e)
        utc_now = datetime.now(UTC).isoformat()
        spool_entry = {
            "utc": utc_now,
            "event_type": event_type,
            "data": data,
            "agent_name": agent_name,
            "summary": summary,
            "error": str(e),
        }
        try:
            line = json.dumps(spool_entry, ensure_ascii=False, default=_spool_default)
            await asyncio.to_thread(_append_spool_line, spool_path, line)
        except Exception as spool_e:  # noqa: BLE001
            logger.critical(
                "archive spool append failed for event_type=%s spool_path=%s error=%s",
                event_type,
                spool_path,
                spool_e,
            )


async def drain_spool(archive: Any, spool_path: Path) -> None:
    """Replay spooled events through archive.record, keeping failures.

    Atomically claims the spool file by renaming it to .draining, then to a
    temp .replay.<pid> file for processing. Replays entries from the temp file,
    appends failed lines back to a fresh spool file with full durability.
    On success, removes the temp file. On append-back failure, renames the
    temp file back to .draining for retry.

    No .done index is used; after any crash the .draining file is replayed in
    full (at-least-once delivery). Failed or malformed lines are appended back
    to spool.jsonl immediately as they are encountered. If any append-back
    raises, the loop stops, .draining is restored, and a critical log is emitted
    without event content. Lines already appended back may be replayed twice.

    Args:
        archive: The taOSmd Archive instance (must have a .record method).
        spool_path: Path to the spool JSONL file.
    """
    async with _drain_lock:
        draining_path = spool_path.with_name(spool_path.name + ".draining")
        replay_path = spool_path.with_name(f".replay.{os.getpid()}")

        # If a .draining file exists from a crashed earlier drain, use it
        if draining_path.exists():
            os.replace(draining_path, replay_path)
        elif spool_path.exists():
            # Atomically claim the spool file, then move to replay temp file
            os.replace(spool_path, draining_path)
            os.replace(draining_path, replay_path)
        else:
            return

        # Read the replay file in a thread (blocking I/O off the event loop)
        try:
            content = await asyncio.to_thread(replay_path.read_text, encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            logger.critical(
                "archive spool: failed to read replay file %s error=%s",
                replay_path,
                e,
            )
            # Restore .draining for retry
            try:
                os.replace(replay_path, draining_path)
            except Exception:
                pass
            return

        lines = content.splitlines()

        append_back_failed = False
        for line in lines:
            line = line.rstrip("\n")
            if not line.strip():
                continue

            try:
                entry = json.loads(line, object_hook=_spool_restore)
            except json.JSONDecodeError:
                logger.warning("archive spool: skipping malformed line: %s", line[:100])
                try:
                    await asyncio.to_thread(_append_spool_line, spool_path, line)
                except Exception as e:  # noqa: BLE001
                    logger.critical(
                        "archive spool: append-back failed for malformed line spool_path=%s error=%s",
                        spool_path,
                        e,
                    )
                    append_back_failed = True
                    break
                continue

            try:
                await archive.record(
                    entry["event_type"],
                    entry["data"],
                    agent_name=entry.get("agent_name"),
                    summary=entry.get("summary"),
                )
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "archive spool replay failed for event_type=%s: %s",
                    entry.get("event_type"),
                    e,
                )
                try:
                    await asyncio.to_thread(_append_spool_line, spool_path, line)
                except Exception as append_e:  # noqa: BLE001
                    logger.critical(
                        "archive spool: append-back failed spool_path=%s error=%s",
                        spool_path,
                        append_e,
                    )
                    append_back_failed = True
                    break

        if append_back_failed:
            # Restore .draining for retry; replay_path becomes .draining
            try:
                os.replace(replay_path, draining_path)
            except Exception as e:  # noqa: BLE001
                logger.critical(
                    "archive spool: failed to restore .draining file error=%s", e
                )
            return

        # All lines processed successfully; remove temp replay file
        try:
            replay_path.unlink()
        except Exception as e:  # noqa: BLE001
            logger.warning("archive spool: failed to remove replay file: %s", e)