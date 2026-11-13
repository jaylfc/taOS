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
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_drain_lock = asyncio.Lock()
_spool_file_lock = threading.Lock()


def _escape_user_dict_with_tag(data: Any) -> Any:
    """Walk data recursively and replace dicts containing __spool_type__ with escaped form.

    A dict like {"__spool_type__": "Path", "str": "/tmp"} becomes
    {"__spool_type__": "escaped_dict", "items": [["str", "/tmp"]]}.
    The escaped form is not a dict, so object_hook won't touch its keys on replay.
    """
    if isinstance(data, dict):
        if "__spool_type__" in data:
            items = []
            for k, v in data.items():
                items.append([k, _escape_user_dict_with_tag(v)])
            return {"__spool_type__": "escaped_dict", "items": items}
        return {k: _escape_user_dict_with_tag(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_escape_user_dict_with_tag(v) for v in data]
    if isinstance(data, tuple):
        return tuple(_escape_user_dict_with_tag(v) for v in data)
    return data


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
    parent_existed = spool_path.parent.exists()
    spool_path.parent.mkdir(parents=True, exist_ok=True)
    file_existed = spool_path.exists()
    dir_fd = None
    try:
        with _spool_file_lock, spool_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
            if not file_existed:
                dir_fd = os.open(spool_path.parent, os.O_RDONLY)
                os.fsync(dir_fd)
    finally:
        if dir_fd is not None:
            os.close(dir_fd)
    # Directory durability: if we just created the parent dir, fsync its parent
    if not parent_existed and spool_path.parent.parent is not None:
        try:
            parent_parent_dir_fd = os.open(spool_path.parent.parent, os.O_RDONLY)
            os.fsync(parent_parent_dir_fd)
            os.close(parent_parent_dir_fd)
        except Exception as e:  # noqa: BLE001
            logger.debug("fsync parent parent dir failed: %s", e)


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
            "data": _escape_user_dict_with_tag(data),
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

    Atomically claims the spool file by renaming it to .draining for processing.
    Replays entries from .draining, appends failed lines back to spool.jsonl with
    full durability. On success, removes .draining. Failed or malformed lines are
    appended back to spool.jsonl immediately as they are encountered. If any
    append-back raises, the loop stops and .draining is preserved for retry.

    No .done index is used; after any crash the .draining file is replayed in full
    (at-least-once delivery). On startup, any legacy .replay.* files stranded next
    to the spool are recovered by appending their lines to .draining.

    Args:
        archive: The taOSmd Archive instance (must have a .record method).
        spool_path: Path to the spool JSONL file.
    """
    # Manually acquire the drain lock to allow early returns without double-release
    await _drain_lock.acquire()
    try:
        draining_path = spool_path.with_name(spool_path.name + ".draining")

        # Item 1: Recover legacy .replay.* files stranded by earlier #4288 builds
        replay_files = list(spool_path.parent.glob(".replay.*"))
        if replay_files:
            # Create .draining if it doesn't exist, append lines and unlink replay files
            dfd = None
            try:
                dfd = os.open(draining_path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o644)
                for rf in replay_files:
                    try:
                        content = rf.read_text(encoding="utf-8")
                        for line in content.split("\n"):
                            if line.strip():
                                os.write(dfd, (line + "\n").encode("utf-8"))
                    except Exception:
                        pass
                os.fsync(dfd)
            finally:
                if dfd is not None:
                    os.close(dfd)
            # Unlink the recovered replay files
            for rf in replay_files:
                try:
                    rf.unlink()
                except Exception:
                    pass

        # Item 5: Claim the spool file with threading lock to prevent writer/drain race.
        # Run the rename via asyncio.to_thread so the lock is not taken on the event loop.
        _spool_file_lock.acquire()
        try:
            if spool_path.exists():
                os.replace(spool_path, draining_path)
            else:
                # No spool file to claim; drain has nothing to process
                return
        finally:
            _spool_file_lock.release()

        # If .draining already existed (from a previous crash), use it in place.
        # If we just claimed the spool, .draining now contains the spool content.
        if not draining_path.exists():
            # Claim failed or spool didn't exist
            return

        # Read the drain file in a thread (blocking I/O off the event loop)
        try:
            content = await asyncio.to_thread(draining_path.read_text, encoding="utf-8")
        except Exception as e:
            logger.critical(
                "archive spool: failed to read drain file %s error=%s",
                draining_path,
                e,
            )
            return

        # Item 2: Split on \n only, not splitlines() which also splits on U+2028, U+2029
        lines = content.split("\n")

        # Process each line
        append_back_failed = False
        for idx, line in enumerate(lines):
            line = line.rstrip("\n")
            if not line.strip():
                continue

            # 1-based line number for logging (item 4: no content in logs)
            line_number = idx + 1

            try:
                entry = json.loads(line, object_hook=_spool_restore)
            except Exception as e:  # noqa: BLE001 - catch all parse errors, not just JSONDecodeError
                # Item 3: Catch Exception (not just JSONDecodeError) around json.loads
                # Item 4: Log spool path and line number only, not line content
                logger.warning(
                    "archive spool: skipping malformed line %s spool_path=%s",
                    line_number,
                    spool_path,
                )
                try:
                    await asyncio.to_thread(_append_spool_line, spool_path, line)
                except Exception as e:  # noqa: BLE001
                    logger.critical(
                        "archive spool: append-back failed spool_path=%s error=%s",
                        spool_path,
                        e,
                    )
                    append_back_failed = True
                    break
                continue

            # Validate entry is a dict with required fields
            if not isinstance(entry, dict) or "event_type" not in entry or "data" not in entry:
                # Treat non-dict or missing required fields as malformed
                logger.warning(
                    "archive spool: skipping line %s spool_path=%s: not a valid event",
                    line_number,
                    spool_path,
                )
                try:
                    await asyncio.to_thread(_append_spool_line, spool_path, line)
                except Exception as e:  # noqa: BLE001
                    logger.critical(
                        "archive spool: append-back failed spool_path=%s error=%s",
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
            # .draining is preserved for retry; do not remove it
            return

        # All lines processed successfully; remove .draining
        try:
            draining_path.unlink()
        except Exception as e:
            logger.warning("archive spool: failed to remove drain file: %s", e)
    finally:
        _drain_lock.release()