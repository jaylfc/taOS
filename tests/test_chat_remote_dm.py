"""Tests for remote DM message handling and peer outbox — E1 collab.

Covers:
- ChatMessageStore migration: remote_msg_id / delivered_at columns
- send_remote_message dedup via unique constraint
- PeerOutboxStore enqueue / dequeue / retry / purge
- Upgrade from a pre-change chat.db (boot-brick regression gate)
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest
import pytest_asyncio

from tinyagentos.chat.message_store import ChatMessageStore
from tinyagentos.chat.peer_outbox import PeerOutboxStore


# ---------------------------------------------------------------------------
# ChatMessageStore — upgrade from pre-change DB
# ---------------------------------------------------------------------------

MESSAGES_V0_SCHEMA = """\
CREATE TABLE IF NOT EXISTS chat_messages (
    id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    thread_id TEXT,
    author_id TEXT NOT NULL,
    author_type TEXT NOT NULL DEFAULT 'user',
    content TEXT NOT NULL DEFAULT '',
    content_type TEXT NOT NULL DEFAULT 'text',
    content_blocks TEXT NOT NULL DEFAULT '[]',
    embeds TEXT NOT NULL DEFAULT '[]',
    components TEXT NOT NULL DEFAULT '[]',
    attachments TEXT NOT NULL DEFAULT '[]',
    reactions TEXT NOT NULL DEFAULT '{}',
    state TEXT NOT NULL DEFAULT 'complete',
    edited_at REAL,
    deleted_at REAL,
    expires_at REAL,
    pinned INTEGER NOT NULL DEFAULT 0,
    ephemeral INTEGER NOT NULL DEFAULT 0,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_channel ON chat_messages(channel_id, created_at);
CREATE INDEX IF NOT EXISTS idx_chat_messages_thread ON chat_messages(thread_id);
"""


def _seed_db(db_path: Path, schema_sql: str) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(schema_sql)
    conn.commit()
    conn.close()


def _column_names(db_path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    conn.close()
    return {r[1] for r in rows}


@pytest.mark.asyncio
class TestChatMessageStoreUpgradeE1:
    """Upgrade from v0 (pre-collab) DB must add remote_msg_id + delivered_at."""

    async def test_upgrade_adds_remote_dm_columns(self, tmp_path: Path):
        db_path = tmp_path / "chat.db"
        _seed_db(db_path, MESSAGES_V0_SCHEMA)

        store = ChatMessageStore(db_path)
        await store.init()
        try:
            cols = _column_names(db_path, "chat_messages")
            assert "remote_msg_id" in cols, "remote_msg_id missing after upgrade"
            assert "delivered_at" in cols, "delivered_at missing after upgrade"
        finally:
            await store.close()

    async def test_upgrade_preserves_existing_data(self, tmp_path: Path):
        db_path = tmp_path / "chat.db"
        _seed_db(db_path, MESSAGES_V0_SCHEMA)
        # Insert a row before the upgrade
        conn = sqlite3.connect(str(db_path))
        now = time.time()
        conn.execute(
            """INSERT INTO chat_messages
               (id, channel_id, author_id, author_type, content, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("msg-pre", "ch1", "user1", "user", "hello before", now),
        )
        conn.commit()
        conn.close()

        store = ChatMessageStore(db_path)
        await store.init()
        try:
            cursor = await store._db.execute(
                "SELECT id, content, remote_msg_id, delivered_at FROM chat_messages"
            )
            rows = await cursor.fetchall()
            assert len(rows) == 1
            assert rows[0][1] == "hello before"
            assert rows[0][2] is None  # remote_msg_id default
            assert rows[0][3] is None  # delivered_at default
        finally:
            await store.close()

    async def test_send_works_after_upgrade(self, tmp_path: Path):
        db_path = tmp_path / "chat.db"
        _seed_db(db_path, MESSAGES_V0_SCHEMA)
        store = ChatMessageStore(db_path)
        await store.init()
        try:
            msg = await store.send_message(
                channel_id="ch1",
                author_id="user1",
                author_type="user",
                content="post-upgrade",
            )
            assert msg["id"]
            assert msg["content"] == "post-upgrade"
            # New columns default to NULL for local messages
            assert "remote_msg_id" in msg
            assert "delivered_at" in msg
            assert msg["remote_msg_id"] is None
            assert msg["delivered_at"] is None
        finally:
            await store.close()


# ---------------------------------------------------------------------------
# send_remote_message — dedup via unique constraint
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def msg_store(tmp_path):
    s = ChatMessageStore(tmp_path / "chat.db")
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
class TestSendRemoteMessage:
    async def test_first_insert_succeeds(self, msg_store):
        msg = await msg_store.send_remote_message(
            channel_id="dm-remote-1",
            author_id="hub:hogne",
            contact_id="hub:hogne",
            remote_msg_id="rm-001",
            content="hello from hogne",
        )
        assert msg is not None
        assert msg["channel_id"] == "dm-remote-1"
        assert msg["author_id"] == "hub:hogne"
        assert msg["author_type"] == "user"
        assert msg["content"] == "hello from hogne"
        assert msg["remote_msg_id"] == "rm-001"
        assert msg["delivered_at"] is not None

    async def test_duplicate_returns_none(self, msg_store):
        msg1 = await msg_store.send_remote_message(
            channel_id="dm-remote-1",
            author_id="hub:hogne",
            contact_id="hub:hogne",
            remote_msg_id="rm-001",
            content="first",
        )
        assert msg1 is not None

        msg2 = await msg_store.send_remote_message(
            channel_id="dm-remote-1",
            author_id="hub:hogne",
            contact_id="hub:hogne",
            remote_msg_id="rm-001",
            content="second — different content, same remote_msg_id",
        )
        assert msg2 is None  # dedup

    async def test_same_remote_id_different_channel_both_succeed(self, msg_store):
        """remote_msg_id dedup is scoped to channel_id."""
        m1 = await msg_store.send_remote_message(
            channel_id="ch-a",
            author_id="hub:hogne",
            contact_id="hub:hogne",
            remote_msg_id="rm-shared",
            content="in channel A",
        )
        m2 = await msg_store.send_remote_message(
            channel_id="ch-b",
            author_id="hub:hogne",
            contact_id="hub:hogne",
            remote_msg_id="rm-shared",
            content="in channel B — same remote_msg_id, different channel",
        )
        assert m1 is not None
        assert m2 is not None
        assert m1["channel_id"] == "ch-a"
        assert m2["channel_id"] == "ch-b"

    async def test_remote_author_id_namespaced(self, msg_store):
        msg = await msg_store.send_remote_message(
            channel_id="dm-remote-1",
            author_id="hub:jaylfc",
            contact_id="hub:jaylfc",
            remote_msg_id="rm-002",
            content="hello from jay",
        )
        assert msg["author_id"] == "hub:jaylfc"
        assert msg["author_type"] == "user"

    async def test_send_remote_message_with_metadata(self, msg_store):
        msg = await msg_store.send_remote_message(
            channel_id="dm-remote-1",
            author_id="hub:hogne",
            contact_id="hub:hogne",
            remote_msg_id="rm-meta",
            content="with metadata",
            metadata={"hops_since_user": 0, "delivered_via": "peer"},
        )
        assert msg["metadata"]["delivered_via"] == "peer"
        assert msg["metadata"]["contact_id"] == "hub:hogne"

    async def test_contact_id_stored_in_metadata(self, msg_store):
        msg = await msg_store.send_remote_message(
            channel_id="dm-remote-1",
            author_id="hub:jaylfc",
            contact_id="hub:jaylfc",
            remote_msg_id="rm-contact-test",
            content="contact id test",
        )
        assert msg["metadata"]["contact_id"] == "hub:jaylfc"

    async def test_empty_remote_msg_id_rejected(self, msg_store):
        with pytest.raises(ValueError, match="remote_msg_id must be a non-empty string"):
            await msg_store.send_remote_message(
                channel_id="dm-remote-1",
                author_id="hub:hogne",
                contact_id="hub:hogne",
                remote_msg_id="",
                content="empty remote_msg_id",
            )

    async def test_null_like_remote_msg_id_rejected(self, msg_store):
        with pytest.raises(ValueError, match="remote_msg_id must be a non-empty string"):
            await msg_store.send_remote_message(
                channel_id="dm-remote-1",
                author_id="hub:hogne",
                contact_id="hub:hogne",
                remote_msg_id=None,
                content="None remote_msg_id",
            )


# ---------------------------------------------------------------------------
# PeerOutboxStore
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def outbox(tmp_path):
    s = PeerOutboxStore(tmp_path / "outbox.db")
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
class TestPeerOutboxStore:
    async def test_enqueue_and_dequeue(self, outbox):
        rid = await outbox.enqueue(
            contact_id="hub:hogne",
            envelope={"kind": "chat", "body": {"content": "hello"}},
        )
        assert rid

        due = await outbox.dequeue_due("hub:hogne", limit=10)
        assert len(due) == 1
        assert due[0]["id"] == rid
        assert due[0]["contact_id"] == "hub:hogne"
        env = json.loads(due[0]["envelope"])
        assert env["kind"] == "chat"

    async def test_mark_sent_removes(self, outbox):
        rid = await outbox.enqueue(
            contact_id="hub:hogne",
            envelope={"kind": "chat"},
        )
        ok = await outbox.mark_sent(rid)
        assert ok is True
        due = await outbox.dequeue_due("hub:hogne")
        assert due == []

    async def test_mark_sent_nonexistent(self, outbox):
        ok = await outbox.mark_sent("does-not-exist")
        assert ok is False

    async def test_mark_failed_backoff(self, outbox):
        rid = await outbox.enqueue(
            contact_id="hub:hogne",
            envelope={"kind": "chat"},
        )
        await outbox.mark_failed(rid)

        # After first failure, next_retry_at should be ~60s in the future
        due_now = await outbox.dequeue_due("hub:hogne")
        assert due_now == []  # not due yet

        # Check the row exists but with incremented attempts
        count = await outbox.count_for_contact("hub:hogne")
        assert count == 1

    async def test_mark_failed_multiple_retries(self, outbox):
        rid = await outbox.enqueue(
            contact_id="hub:hogne",
            envelope={"kind": "chat"},
        )
        for _ in range(3):
            await outbox.mark_failed(rid)
        # Row should still exist with attempts=3
        count = await outbox.count_for_contact("hub:hogne")
        assert count == 1

    async def test_purge_for_contact(self, outbox):
        await outbox.enqueue(contact_id="hub:a", envelope={"kind": "chat"})
        await outbox.enqueue(contact_id="hub:a", envelope={"kind": "ack"})
        await outbox.enqueue(contact_id="hub:b", envelope={"kind": "chat"})

        deleted = await outbox.purge_for_contact("hub:a")
        assert deleted == 2
        assert await outbox.count_for_contact("hub:a") == 0
        assert await outbox.count_for_contact("hub:b") == 1

    async def test_count_for_contact(self, outbox):
        for _ in range(3):
            await outbox.enqueue(contact_id="hub:hogne", envelope={"kind": "chat"})
        assert await outbox.count_for_contact("hub:hogne") == 3
        assert await outbox.count_for_contact("hub:nonexistent") == 0

    async def test_dequeue_due_respects_limit(self, outbox):
        for i in range(5):
            await outbox.enqueue(
                contact_id="hub:hogne",
                envelope={"kind": "chat", "seq": i},
            )
        due = await outbox.dequeue_due("hub:hogne", limit=2)
        assert len(due) == 2

    async def test_dequeue_due_future_retry_not_returned(self, outbox):
        # Enqueue with a retry time far in the future
        await outbox.enqueue(
            contact_id="hub:hogne",
            envelope={"kind": "chat"},
            next_retry_at=time.time() + 3600,
        )
        due = await outbox.dequeue_due("hub:hogne")
        assert due == []

    async def test_mark_failed_returns_true_while_pending(self, outbox):
        rid = await outbox.enqueue(
            contact_id="hub:hogne",
            envelope={"kind": "chat"},
        )
        # First failure — still pending
        ok = await outbox.mark_failed(rid)
        assert ok is True
        assert await outbox.count_for_contact("hub:hogne") == 1

    async def test_mark_failed_terminal_after_max_attempts(self, outbox):
        rid = await outbox.enqueue(
            contact_id="hub:hogne",
            envelope={"kind": "chat"},
        )
        # Retry up to _MAX_ATTEMPTS (10)
        for attempt in range(10):
            ok = await outbox.mark_failed(rid)
            if attempt < 9:
                assert ok is True, f"attempt {attempt+1} should still be pending"
            else:
                # 10th mark_failed should hit max attempts and delete
                assert ok is False, f"attempt {attempt+1} should be terminal"
        assert await outbox.count_for_contact("hub:hogne") == 0


# ---------------------------------------------------------------------------
# Outbox upgrade — no columns added, but verify init is idempotent
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestPeerOutboxUpgrade:
    async def test_init_idempotent_on_existing_db(self, tmp_path: Path):
        db_path = tmp_path / "outbox.db"
        s1 = PeerOutboxStore(db_path)
        await s1.init()
        await s1.enqueue(contact_id="hub:hogne", envelope={"kind": "chat"})
        await s1.close()

        # Re-open — must work without error
        s2 = PeerOutboxStore(db_path)
        await s2.init()
        try:
            count = await s2.count_for_contact("hub:hogne")
            assert count == 1
        finally:
            await s2.close()
