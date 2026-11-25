"""Backfill existing chat turns into taOSmd's ingest (owner-run).

    python -m tinyagentos.turn_backfill --data-dir <data dir> [--rate 20]
        [--limit N] [--enqueue-only] [--requeue-failed] [--taosmd-url URL]

Reads the chat store read-only, queues every message that has not been queued
(same turn id as the live path, ``chat:<message id>``, so a turn is never
queued twice), then delivers the queue to taOSmd at ``--rate`` turns a second.

* Resumable: a per-channel cursor is saved after each batch, and queued or
  delivered turns are skipped by key, so an interrupted run continues where it
  stopped and a repeat run does nothing.
* Rate limited: both the queueing and the delivery stop to keep to ``--rate``.
* A turn's index is its position in its channel (oldest first, deleted
  messages keep their place), which is where the live counter's one-time seed
  puts the next new turn.
* Run it as the install owner against the data directory of the install; it
  never writes to the chat store.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sqlite3
import sys
from pathlib import Path

import httpx

from tinyagentos.session_turns import UNKNOWN, session_date
from tinyagentos.turn_ingest import (
    TurnIngestQueue,
    TurnIngestWorker,
    agent_shelves,
    enqueue_turn,
    http_sender,
    role_for,
)
from tinyagentos.user_memory import DEFAULT_SETTINGS, LEGACY_USER_ID, user_namespace

logger = logging.getLogger("tinyagentos.turn_backfill")


def _capture_conversations(conn, user_id: str, owner_id: str | None) -> bool:
    """The person's ``capture_conversations`` setting, as the live path reads it:
    their own row, else (the owner only) the legacy shared row, else defaults."""
    ids = [user_id] + ([LEGACY_USER_ID] if owner_id and user_id == owner_id else [])
    for uid in ids:
        row = conn.execute(
            "SELECT settings FROM user_memory_settings WHERE user_id = ?", (uid,)
        ).fetchone()
        if row:
            return bool({**DEFAULT_SETTINGS, **json.loads(row[0])}.get("capture_conversations"))
    return bool(DEFAULT_SETTINGS["capture_conversations"])


async def backfill(
    data_dir: Path,
    config,
    queue: TurnIngestQueue,
    *,
    rate: float = 20.0,
    limit: int | None = None,
    batch: int = 200,
) -> dict:
    """Queue the chat turns not yet queued.  Returns counts."""
    chat = sqlite3.connect(f"file:{data_dir / 'chat.db'}?mode=ro", uri=True)
    um_path = data_dir / "user_memory.db"
    um = sqlite3.connect(f"file:{um_path}?mode=ro", uri=True) if um_path.exists() else None
    owner_id = None
    try:
        from tinyagentos.auth import AuthManager
        primary = AuthManager(data_dir).get_primary_user()
        owner_id = primary["id"] if primary else None
    except Exception:
        logger.warning("install owner unknown: legacy 'user' authored turns get no person shelf",
                       exc_info=True)
    gap = 1.0 / rate if rate > 0 else 0.0
    seen = queued = skipped = 0
    capture_cache: dict[str, bool] = {}
    try:
        channels = chat.execute("SELECT id, members FROM chat_channels ORDER BY created_at").fetchall()
        for channel_id, members_json in channels:
            channel = {"members": json.loads(members_json or "[]")}
            source = f"chat:{channel_id}"
            done = int(await queue.get_cursor(source) or 0)
            ordinal = done
            while True:
                rows = chat.execute(
                    "SELECT id, author_id, author_type, content, created_at, deleted_at "
                    "FROM chat_messages WHERE channel_id = ? "
                    "ORDER BY created_at, rowid LIMIT ? OFFSET ?",
                    (channel_id, batch, ordinal),
                ).fetchall()
                if not rows:
                    break
                for mid, author_id, author_type, content, created_at, deleted_at in rows:
                    seen += 1
                    index = ordinal
                    ordinal += 1
                    if deleted_at is not None or not (content or "").strip():
                        skipped += 1
                        continue
                    shelves = agent_shelves(config, channel)
                    if author_type == "user" and author_id:
                        uid = owner_id if author_id == LEGACY_USER_ID and owner_id else author_id
                        if uid not in capture_cache:
                            capture_cache[uid] = (
                                _capture_conversations(um, uid, owner_id) if um else True)
                        if capture_cache[uid]:
                            shelves.append(user_namespace(str(uid)))
                    fields = {
                        "session_id": channel_id,
                        "turn_index": index,
                        "session_date": session_date(created_at),
                        "turn_owner": author_id or UNKNOWN,
                    }
                    queued += await enqueue_turn(
                        queue, turn_id=f"chat:{mid}", shelves=shelves,
                        role=role_for(author_type), text=content, fields=fields)
                    if gap:
                        await asyncio.sleep(gap)
                    if limit is not None and queued >= limit:
                        await queue.set_cursor(source, str(ordinal))
                        return {"seen": seen, "queued": queued, "skipped": skipped,
                                "stopped": "limit"}
                await queue.set_cursor(source, str(ordinal))
    finally:
        chat.close()
        if um:
            um.close()
    return {"seen": seen, "queued": queued, "skipped": skipped, "stopped": "complete"}


async def _main(args) -> int:
    from tinyagentos.config import load_config

    data_dir = Path(args.data_dir)
    config = load_config(data_dir / "config.yaml")
    queue = TurnIngestQueue(data_dir / "turn_ingest.db")
    await queue.init()
    try:
        if args.requeue_failed:
            print(f"requeued failed: {await queue.requeue_failed()}")
        result = await backfill(data_dir, config, queue, rate=args.rate, limit=args.limit)
        print(f"backfill: {result}")
        if not args.enqueue_only:
            base = args.taosmd_url or getattr(config, "memory_url", "") or "http://localhost:7900"
            async with httpx.AsyncClient(timeout=30) as client:
                worker = TurnIngestWorker(
                    queue, http_sender(lambda: client, lambda: base), rate_per_sec=args.rate)
                while True:
                    delivered = await worker.drain_once()
                    counts = await queue.counts()
                    print(f"delivered {delivered}; queue {counts}")
                    if counts["pending"] == 0 or delivered == 0:
                        break
        counts = await queue.counts()
        print(f"queue: {counts}")
        return 0 if counts["failed"] == 0 else 1
    finally:
        await queue.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--rate", type=float, default=20.0, help="turns per second")
    ap.add_argument("--limit", type=int, default=None, help="stop after queueing N turns")
    ap.add_argument("--enqueue-only", action="store_true")
    ap.add_argument("--requeue-failed", action="store_true")
    ap.add_argument("--taosmd-url", default=None)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    return asyncio.run(_main(args))


if __name__ == "__main__":
    sys.exit(main())
