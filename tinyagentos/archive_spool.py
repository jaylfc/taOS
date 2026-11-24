"""Lossless archive spool — failed archive writes are spooled durably and replayed.

This module provides a zero-loss layer for the taOSmd Archive. When
archive.record() fails for any reason, the event is appended to a JSONL spool
file with full fidelity (no stripping, no truncation). A background drain task
replays spooled events when the archive becomes available again.
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


def _append_spool_line(spool_path: Path, line: str) -> None:
    """Append a line to the spool file with flush and fsync for durability."""
    spool_path.parent.mkdir(parents=True, exist_ok=True)
    # Check if file exists before opening to know whether to fsync parent
    file_existed = spool_path.exists()
    with spool_path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())
        # If this is a newly created file, fsync the parent directory
        if not file_existed:
            dir_fd = os.open(spool_path.parent, os.O_RDONLY)
            os.fsync(dir_fd)
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
            _append_spool_line(spool_path, line)
        except Exception as spool_e:  # noqa: BLE001
            logger.critical(
                "archive spool append failed for event_type=%s; entry=%s; error=%s",
                event_type,
                repr(spool_entry),
                spool_e,
            )


async def drain_spool(archive: Any, spool_path: Path) -> None:
    """Replay spooled events through archive.record, keeping failures.

    Atomically claims the spool file by renaming it to .draining, replays
    entries from the claimed file, appends failed lines back to a fresh spool
    file with full durability, and only then removes the .draining file.
    A module-level lock prevents concurrent drains.

    Args:
        archive: The taOSmd Archive instance (must have a .record method).
        spool_path: Path to the spool JSONL file.
    """
    async with _drain_lock:
        draining_path = spool_path.with_name(spool_path.name + ".draining")
        done_path = draining_path.with_suffix(draining_path.suffix + ".done")

        # If a .draining file exists from a crashed earlier drain, replay that first
        if draining_path.exists():
            replay_path = draining_path
        elif spool_path.exists():
            # Atomically claim the spool file
            os.replace(spool_path, draining_path)
            replay_path = draining_path
        else:
            return

        # Load progress tracking from .done file if it exists
        processed_indices: set[int] = set()
        if done_path.exists():
            try:
                # Parse indices from the done file
                for line in done_path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        try:
                            processed_indices.add(int(line.strip()))
                        except ValueError:
                            logger.warning("archive spool: invalid index in .done file: %s", line)
            except Exception as e:  # noqa: BLE001
                logger.warning("archive spool: failed to read .done file: %s", e)
                # If we can't read the done file, start fresh (will reprocess all)

        failed_lines: list[str] = []
        successful_count = 0

        # Read the replay file line by line (not all at once)
        with replay_path.open(encoding="utf-8") as f:
            for i, line in enumerate(f):
                line = line.rstrip("\n")
                if not line.strip():
                    continue
                    
                # Skip lines that have already been processed (crash recovery)
                if i in processed_indices:
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
                    # Track successful processing
                    processed_indices.add(i)
                    successful_count += 1
                except Exception as e:  # noqa: BLE001
                    logger.error(
                        "archive spool replay failed for event_type=%s: %s",
                        entry.get("event_type"),
                        e,
                    )
                    failed_lines.append(line)

        # If we had any successful records, persist progress atomically
        if successful_count > 0:
            # Write the updated .done file atomically
            try:
                done_path.write_text(
                    "\n".join(str(idx) for idx in sorted(processed_indices)) + "\n",
                    encoding="utf-8",
                )
                # Ensure the .done file is written to disk
                with done_path.open("r+b") as f:
                    os.fsync(f.fileno())
            except Exception as e:  # noqa: BLE001
                logger.warning("archive spool: failed to write .done file: %s", e)

        # Append failed lines back to spool.jsonl with full durability
        all_append_success = True
        for line in failed_lines:
            try:
                _append_spool_line(spool_path, line)
            except Exception as e:  # noqa: BLE001
                logger.critical("archive spool: failed to append failed line back to spool: %s", e)
                all_append_success = False

        # Only now remove the .draining file if all append-backs succeeded
        if all_append_success:
            try:
                draining_path.unlink()
                # Also clean up the .done file since we're done with it
                if done_path.exists():
                    try:
                        done_path.unlink()
                    except Exception:  # noqa: BLE001, S110
                        pass
            except Exception as e:  # noqa: BLE001
                logger.warning("archive spool: failed to remove .draining file: %s", e)
        else:
            # If append-back failed, keep .draining file for retry
            # Also keep the .done file as is since we didn't process successfully
            # Events are preserved in .draining for the next drain retry
            logger.critical(
                "archive spool: append-back failures - keeping .draining file for retry; events preserved for retry (failed: %d)",
                len(failed_lines),
            )
            # Return early as per CR 4538090707 - don't unlink .draining when any append-back fails
            return