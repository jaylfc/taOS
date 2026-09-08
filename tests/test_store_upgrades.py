"""Existing-DB upgrade tests for stores with guarded _post_init column migrations.

BaseStore.init() runs SCHEMA (CREATE TABLE + CREATE INDEX) before _post_init
migrations.  Any CREATE INDEX in SCHEMA that references a column added by
_post_init bricks boot on existing databases — the column does not exist yet
when the index is created.

These tests simulate the upgrade path by seeding a "v0" database (only the
original columns, no migration columns) and then verifying that store.init()
successfully adds the migration columns via _post_init.

Audit (2026-07-17): no store currently has a SCHEMA CREATE INDEX referencing a
_post_init-added column.  These tests are a regression gate.
"""

import json
import sqlite3
import time
from pathlib import Path

import pytest

from tinyagentos.notifications import NotificationStore
from tinyagentos.board_audit import BoardAuditLog
from tinyagentos.decisions.decision_store import DecisionStore
from tinyagentos.projects.project_store import ProjectStore
from tinyagentos.projects.invite_store import ProjectInviteStore
from tinyagentos.projects.canvas.store import ProjectCanvasStore
from tinyagentos.projects.task_store import ProjectTaskStore
from tinyagentos.chat.channel_store import ChatChannelStore
from tinyagentos.notes.shared_docs_store import SharedDocsStore
from tinyagentos.contacts_store import ContactsStore
from tinyagentos.hub.identity import fingerprint as _compute_fingerprint


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _seed_db(db_path: Path, schema_sql: str):
    """Create a fresh SQLite database with *only* the given schema (no
    migrations, no _post_init).  Used to build the v0 baseline."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript(schema_sql)
    conn.commit()
    conn.close()


def _column_names(db_path: Path, table: str) -> set[str]:
    """Return the set of column names present in *table* right now."""
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    conn.close()
    return {r[1] for r in rows}


# ---------------------------------------------------------------------------
# NotificationStore — columns archived + data added in _post_init
# ---------------------------------------------------------------------------

NOTIF_V0_SCHEMA = """\
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    level TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    read INTEGER NOT NULL DEFAULT 0,
    source TEXT
);
CREATE TABLE IF NOT EXISTS notification_prefs (
    event_type TEXT PRIMARY KEY,
    muted INTEGER NOT NULL DEFAULT 0
);
"""


@pytest.mark.asyncio
class TestNotificationStoreUpgrade:
    async def test_upgrade_adds_archived_and_data_columns(self, tmp_path):
        db_path = tmp_path / "notif.db"
        _seed_db(db_path, NOTIF_V0_SCHEMA)

        store = NotificationStore(db_path)
        await store.init()
        try:
            cols = _column_names(db_path, "notifications")
            assert "archived" in cols, "archived column missing after upgrade"
            assert "data" in cols, "data column missing after upgrade"
        finally:
            await store.close()

    async def test_upgrade_preserves_existing_data(self, tmp_path):
        db_path = tmp_path / "notif.db"
        _seed_db(db_path, NOTIF_V0_SCHEMA)
        # Insert a row before upgrade
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO notifications (timestamp, level, title, message) VALUES (?, ?, ?, ?)",
            (int(time.time()), "info", "Test", "Hello"),
        )
        conn.commit()
        conn.close()

        store = NotificationStore(db_path)
        await store.init()
        try:
            cursor = await store._db.execute(
                "SELECT id, title, message, archived FROM notifications"
            )
            rows = await cursor.fetchall()
            assert len(rows) == 1
            assert rows[0][1] == "Test"
            assert rows[0][2] == "Hello"
            assert rows[0][3] == 0  # DEFAULT for archived
        finally:
            await store.close()

    async def test_add_works_after_upgrade(self, tmp_path):
        db_path = tmp_path / "notif.db"
        _seed_db(db_path, NOTIF_V0_SCHEMA)
        store = NotificationStore(db_path)
        await store.init()
        try:
            await store.add("Title", "Body", level="info")
            cursor = await store._db.execute("SELECT COUNT(*) FROM notifications")
            count = (await cursor.fetchone())[0]
            assert count == 1
        finally:
            await store.close()


# ---------------------------------------------------------------------------
# BoardAuditLog — columns project_id + detail added in _post_init
# ---------------------------------------------------------------------------

BOARD_AUDIT_V0_SCHEMA = """\
CREATE TABLE IF NOT EXISTS board_audit (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    event TEXT NOT NULL,
    actor TEXT,
    from_status TEXT,
    to_status TEXT,
    ts TEXT NOT NULL
);
"""


@pytest.mark.asyncio
class TestBoardAuditUpgrade:
    async def test_upgrade_adds_project_id_and_detail_columns(self, tmp_path):
        db_path = tmp_path / "audit.db"
        _seed_db(db_path, BOARD_AUDIT_V0_SCHEMA)
        store = BoardAuditLog(db_path)
        await store.init()
        try:
            cols = _column_names(db_path, "board_audit")
            assert "project_id" in cols, "project_id column missing after upgrade"
            assert "detail" in cols, "detail column missing after upgrade"
        finally:
            await store.close()

    async def test_upgrade_allows_record(self, tmp_path):
        db_path = tmp_path / "audit.db"
        _seed_db(db_path, BOARD_AUDIT_V0_SCHEMA)
        store = BoardAuditLog(db_path)
        await store.init()
        try:
            eid = await store.record(
                task_id="task-1",
                event="test",
                actor="user",
                from_status="todo",
                to_status="done",
                detail={"note": "upgraded"},
            )
            assert eid
            cursor = await store._db.execute("SELECT COUNT(*) FROM board_audit")
            count = (await cursor.fetchone())[0]
            assert count == 1
        finally:
            await store.close()


# ---------------------------------------------------------------------------
# DecisionStore — column metadata added in _post_init
# ---------------------------------------------------------------------------

DECISIONS_V0_SCHEMA = """\
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_agent TEXT,
    project_id TEXT,
    user_id TEXT,
    question TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'yes_no',
    options TEXT,
    context TEXT,
    priority INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    answer TEXT,
    created_at REAL NOT NULL,
    answered_at REAL,
    deadline REAL,
    checkpoint_ref TEXT,
    parent_decision_id TEXT,
    timeline_id TEXT
);
"""


@pytest.mark.asyncio
class TestDecisionStoreUpgrade:
    async def test_upgrade_adds_metadata_column(self, tmp_path):
        db_path = tmp_path / "decisions.db"
        _seed_db(db_path, DECISIONS_V0_SCHEMA)
        store = DecisionStore(db_path)
        await store.init()
        try:
            cols = _column_names(db_path, "decisions")
            assert "metadata" in cols, "metadata column missing after upgrade"
        finally:
            await store.close()


# ---------------------------------------------------------------------------
# ProjectStore — multiple migration columns on project_members + projects
# ---------------------------------------------------------------------------

PROJECTS_V0_SCHEMA = """\
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    created_by TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    archived_at REAL,
    deleted_at REAL,
    settings TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS project_members (
    project_id TEXT NOT NULL,
    member_id TEXT NOT NULL,
    member_kind TEXT NOT NULL DEFAULT 'user',
    role TEXT NOT NULL DEFAULT 'member',
    source_agent_id TEXT,
    memory_seed TEXT,
    added_at REAL,
    PRIMARY KEY (project_id, member_id)
);
"""


@pytest.mark.asyncio
class TestProjectStoreUpgrade:
    async def test_upgrade_adds_project_member_columns(self, tmp_path):
        db_path = tmp_path / "projects.db"
        _seed_db(db_path, PROJECTS_V0_SCHEMA)
        store = ProjectStore(db_path)
        await store.init()
        try:
            pcols = _column_names(db_path, "projects")
            mcols = _column_names(db_path, "project_members")
            assert "user_id" in pcols, "user_id missing on projects"
            assert "lead_member_id" in pcols, "lead_member_id missing on projects"
            assert "can_edit_canvas" in mcols, "can_edit_canvas missing on project_members"
            assert "can_read_canvas" in mcols, "can_read_canvas missing on project_members"
            assert "is_lead" in mcols, "is_lead missing on project_members"
        finally:
            await store.close()


# ---------------------------------------------------------------------------
# ProjectInviteStore — column redeemed_request_id added in _post_init
# ---------------------------------------------------------------------------

INVITES_V0_SCHEMA = """\
CREATE TABLE IF NOT EXISTS project_invites (
    invite_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    pin_hash TEXT,
    scopes TEXT NOT NULL DEFAULT '[]',
    approval_mode TEXT NOT NULL DEFAULT 'auto',
    check_interval_secs INTEGER NOT NULL DEFAULT 600,
    created_by TEXT,
    created_ts REAL NOT NULL,
    expires_ts REAL,
    redeem_attempts INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'open',
    redeemed_by TEXT
);
"""


@pytest.mark.asyncio
class TestProjectInviteStoreUpgrade:
    async def test_upgrade_adds_redeemed_request_id_column(self, tmp_path):
        db_path = tmp_path / "invites.db"
        _seed_db(db_path, INVITES_V0_SCHEMA)
        store = ProjectInviteStore(db_path)
        await store.init()
        try:
            cols = _column_names(db_path, "project_invites")
            assert "redeemed_request_id" in cols, "redeemed_request_id missing"
        finally:
            await store.close()


# ---------------------------------------------------------------------------
# ProjectCanvasStore — column element_id added in _post_init
# ---------------------------------------------------------------------------

CANVAS_V0_SCHEMA = """\
CREATE TABLE IF NOT EXISTS project_canvas_elements (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    author_kind TEXT NOT NULL DEFAULT 'user',
    author_id TEXT,
    x REAL NOT NULL DEFAULT 0,
    y REAL NOT NULL DEFAULT 0,
    w REAL NOT NULL DEFAULT 100,
    h REAL NOT NULL DEFAULT 100,
    rotation REAL NOT NULL DEFAULT 0,
    z_index INTEGER NOT NULL DEFAULT 0,
    payload TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    deleted_at REAL
);
"""


@pytest.mark.asyncio
class TestCanvasStoreUpgrade:
    async def test_upgrade_adds_element_id_column(self, tmp_path):
        db_path = tmp_path / "canvas.db"
        _seed_db(db_path, CANVAS_V0_SCHEMA)
        store = ProjectCanvasStore(db_path)
        await store.init()
        try:
            cols = _column_names(db_path, "project_canvas_elements")
            assert "element_id" in cols, "element_id missing"
        finally:
            await store.close()


# ---------------------------------------------------------------------------
# ProjectTaskStore — column element_id added in _post_init
# ---------------------------------------------------------------------------

TASKS_V0_SCHEMA = """\
CREATE TABLE IF NOT EXISTS project_tasks (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    parent_task_id TEXT,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'todo',
    priority INTEGER NOT NULL DEFAULT 0,
    labels TEXT NOT NULL DEFAULT '[]',
    assignee_id TEXT,
    claimed_by TEXT,
    claimed_at REAL,
    closed_at REAL,
    closed_by TEXT,
    close_reason TEXT,
    created_by TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
"""


@pytest.mark.asyncio
class TestProjectTaskStoreUpgrade:
    async def test_upgrade_adds_element_id_column(self, tmp_path):
        db_path = tmp_path / "tasks.db"
        _seed_db(db_path, TASKS_V0_SCHEMA)
        store = ProjectTaskStore(db_path)
        await store.init()
        try:
            cols = _column_names(db_path, "project_tasks")
            assert "element_id" in cols, "element_id missing"
        finally:
            await store.close()


# ---------------------------------------------------------------------------
# ChatChannelStore — column project_id added in _post_init
# ---------------------------------------------------------------------------

CHANNELS_V0_SCHEMA = """\
CREATE TABLE IF NOT EXISTS chat_channels (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'public',
    description TEXT NOT NULL DEFAULT '',
    topic TEXT,
    members TEXT NOT NULL DEFAULT '[]',
    settings TEXT NOT NULL DEFAULT '{}',
    created_by TEXT,
    created_at REAL NOT NULL,
    last_message_at REAL
);
"""


@pytest.mark.asyncio
class TestChatChannelStoreUpgrade:
    async def test_upgrade_adds_project_id_column(self, tmp_path):
        db_path = tmp_path / "channels.db"
        _seed_db(db_path, CHANNELS_V0_SCHEMA)
        store = ChatChannelStore(db_path)
        await store.init()
        try:
            cols = _column_names(db_path, "chat_channels")
            assert "project_id" in cols, "project_id missing"
        finally:
            await store.close()


# ---------------------------------------------------------------------------
# SharedDocsStore — columns permission, action, discuss_channel_id added
# ---------------------------------------------------------------------------

SHARED_DOCS_V0_SCHEMA = """\
CREATE TABLE IF NOT EXISTS shared_docs (
    id TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT 'project',
    project_id TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    archived_at REAL
);
CREATE TABLE IF NOT EXISTS shared_doc_members (
    doc_id TEXT NOT NULL,
    member_type TEXT NOT NULL,
    member_id TEXT NOT NULL,
    standing_instruction TEXT,
    created_at REAL NOT NULL,
    PRIMARY KEY (doc_id, member_type, member_id)
);
CREATE TABLE IF NOT EXISTS shared_doc_entries (
    id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL,
    author_type TEXT NOT NULL,
    author_id TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS shared_doc_entry_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id TEXT NOT NULL,
    rev_index INTEGER NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    author_type TEXT NOT NULL,
    author_id TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""


@pytest.mark.asyncio
class TestSharedDocsStoreUpgrade:
    async def test_upgrade_adds_migration_columns(self, tmp_path):
        db_path = tmp_path / "shared_docs.db"
        _seed_db(db_path, SHARED_DOCS_V0_SCHEMA)
        store = SharedDocsStore(db_path)
        await store.init()
        try:
            cols = _column_names(db_path, "shared_doc_members")
            assert "permission" in cols, "permission missing"
            assert "action" in cols, "action missing"
            assert "discuss_channel_id" in cols, "discuss_channel_id missing"
        finally:
            await store.close()


# ---------------------------------------------------------------------------
# ContactsStore — column peer_fingerprint added in _post_init
# ---------------------------------------------------------------------------

CONTACTS_V0_SCHEMA = """\
CREATE TABLE IF NOT EXISTS contacts (
    contact_id        TEXT PRIMARY KEY,
    hub_username      TEXT NOT NULL UNIQUE,
    display_name      TEXT NOT NULL,
    ed25519_pub       TEXT NOT NULL,
    x25519_pub        TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'pending',
    local_crm_id      TEXT,
    created_at        REAL NOT NULL,
    revoked_at        REAL
);
CREATE TABLE IF NOT EXISTS peer_links (
    contact_id              TEXT PRIMARY KEY REFERENCES contacts(contact_id),
    inbound_token_hash      TEXT NOT NULL,
    outbound_token          TEXT NOT NULL,
    endpoints               TEXT NOT NULL DEFAULT '[]',
    established_at          REAL NOT NULL,
    last_seen_at            REAL,
    revoked_at              REAL
);
CREATE INDEX IF NOT EXISTS idx_peer_links_token_hash ON peer_links(inbound_token_hash);
CREATE TABLE IF NOT EXISTS peer_nonces (
    nonce                   TEXT NOT NULL,
    contact_id              TEXT NOT NULL,
    kind                    TEXT NOT NULL DEFAULT '',
    seen_at                 REAL NOT NULL,
    PRIMARY KEY (contact_id, kind, nonce)
);
"""


@pytest.mark.asyncio
class TestContactsStoreUpgrade:
    async def test_upgrade_adds_peer_fingerprint_column(self, tmp_path):
        db_path = tmp_path / "contacts.db"
        _seed_db(db_path, CONTACTS_V0_SCHEMA)
        store = ContactsStore(db_path)
        await store.init()
        try:
            cols = _column_names(db_path, "contacts")
            assert "peer_fingerprint" in cols, "peer_fingerprint missing after upgrade"
        finally:
            await store.close()

    async def test_upgrade_add_contact_works_after_upgrade(self, tmp_path):
        db_path = tmp_path / "contacts.db"
        _seed_db(db_path, CONTACTS_V0_SCHEMA)
        store = ContactsStore(db_path)
        await store.init()
        try:
            await store.add_contact(
                contact_id="hub:test",
                hub_username="test",
                display_name="Test",
                ed25519_pub="ab" * 32,
                x25519_pub="cd" * 32,
                peer_fingerprint="deadbeef",
            )
            contact = await store.get_contact("hub:test")
            assert contact is not None
            assert contact["peer_fingerprint"] == "deadbeef"
        finally:
            await store.close()

    async def test_upgrade_backfills_fingerprint_for_existing_rows(
        self, tmp_path
    ):
        """Regression: rows predating peer_fingerprint must get backfilled.

        Without backfill, block_peer (routes/hub.py) resolves peers by
        fingerprint only and silently fails to revoke the peer link for
        every pre-existing contact — the block path fails open.
        """
        db_path = tmp_path / "contacts.db"
        _seed_db(db_path, CONTACTS_V0_SCHEMA)
        # Seed a pre-existing contact with key material but no fingerprint
        # column (the v0 schema has no peer_fingerprint).
        now = time.time()
        db = sqlite3.connect(str(db_path))
        db.execute(
            "INSERT INTO contacts "
            "(contact_id, hub_username, display_name, ed25519_pub, x25519_pub,"
            " status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("hub:testpeer", "testpeer", "Test Peer",
             "ab" * 32, "cd" * 32, "active", now),
        )
        db.commit()
        db.close()

        store = ContactsStore(db_path)
        await store.init()
        try:
            contact = await store.get_contact("hub:testpeer")
            assert contact is not None
            assert contact["peer_fingerprint"] != "", (
                "peer_fingerprint not backfilled for pre-existing row"
            )
            expected = _compute_fingerprint("ab" * 32)
            assert contact["peer_fingerprint"] == expected, (
                f"fingerprint mismatch: {contact['peer_fingerprint']} != {expected}"
            )
        finally:
            await store.close()

    async def test_upgrade_skips_malformed_ed25519_during_backfill(
        self, tmp_path
    ):
        """Backfill must not brick boot when a v0 row has non-hex key material.

        _compute_fingerprint calls bytes.fromhex, which raises ValueError on
        odd-length strings, non-hex chars, or embedded NULs.  A single bad row
        must never crash init() or abort the rest of the backfill.
        """
        db_path = tmp_path / "contacts.db"
        _seed_db(db_path, CONTACTS_V0_SCHEMA)
        now = time.time()
        db = sqlite3.connect(str(db_path))
        # Row with valid hex — must be backfilled.
        db.execute(
            "INSERT INTO contacts "
            "(contact_id, hub_username, display_name, ed25519_pub, x25519_pub,"
            " status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("hub:valid", "valid", "Valid", "ab" * 32, "cd" * 32,
             "active", now),
        )
        # Row with malformed hex — must NOT crash init().
        db.execute(
            "INSERT INTO contacts "
            "(contact_id, hub_username, display_name, ed25519_pub, x25519_pub,"
            " status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("hub:badhex", "badhex", "Bad Hex", "not-hex-data!!!", "cd" * 32,
             "active", now),
        )
        db.commit()
        db.close()

        store = ContactsStore(db_path)
        await store.init()  # must not raise
        try:
            # Valid row is backfilled.
            valid = await store.get_contact("hub:valid")
            assert valid is not None
            assert valid["peer_fingerprint"] == _compute_fingerprint("ab" * 32)

            # Bad row is reachable (not bricked), fingerprint stays empty.
            bad = await store.get_contact("hub:badhex")
            assert bad is not None
            assert bad["peer_fingerprint"] == "", (
                "malformed-hex row must keep empty fingerprint, not crash"
            )
        finally:
            await store.close()


# ---------------------------------------------------------------------------
# Regression: no SCHEMA CREATE INDEX references a _post_init-added column
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestNoIndexOnMigrationColumns:
    """Smoke test: verify that for every store with _post_init ADD COLUMN,
    the SCHEMA does NOT contain a CREATE INDEX that references any of those
    columns.  Such an index would brick boot on existing databases."""

    async def test_notifications_no_index_on_archived_or_data(self, tmp_path):
        db_path = tmp_path / "notif.db"
        _seed_db(db_path, NOTIF_V0_SCHEMA)
        store = NotificationStore(db_path)
        await store.init()  # must not crash
        await store.close()

    async def test_board_audit_no_index_migration_cols(self, tmp_path):
        db_path = tmp_path / "audit.db"
        _seed_db(db_path, BOARD_AUDIT_V0_SCHEMA)
        store = BoardAuditLog(db_path)
        await store.init()  # must not crash
        await store.close()

    async def test_decisions_no_index_on_metadata(self, tmp_path):
        db_path = tmp_path / "decisions.db"
        _seed_db(db_path, DECISIONS_V0_SCHEMA)
        store = DecisionStore(db_path)
        await store.init()  # must not crash
        await store.close()

    async def test_projects_no_index_on_migration_cols(self, tmp_path):
        db_path = tmp_path / "projects.db"
        _seed_db(db_path, PROJECTS_V0_SCHEMA)
        store = ProjectStore(db_path)
        await store.init()  # must not crash
        await store.close()

    async def test_invites_no_index_on_redeemed_request_id(self, tmp_path):
        db_path = tmp_path / "invites.db"
        _seed_db(db_path, INVITES_V0_SCHEMA)
        store = ProjectInviteStore(db_path)
        await store.init()  # must not crash
        await store.close()

    async def test_canvas_no_index_on_element_id(self, tmp_path):
        db_path = tmp_path / "canvas.db"
        _seed_db(db_path, CANVAS_V0_SCHEMA)
        store = ProjectCanvasStore(db_path)
        await store.init()  # must not crash
        await store.close()

    async def test_tasks_no_index_on_element_id(self, tmp_path):
        db_path = tmp_path / "tasks.db"
        _seed_db(db_path, TASKS_V0_SCHEMA)
        store = ProjectTaskStore(db_path)
        await store.init()  # must not crash
        await store.close()

    async def test_channels_no_index_on_project_id(self, tmp_path):
        db_path = tmp_path / "channels.db"
        _seed_db(db_path, CHANNELS_V0_SCHEMA)
        store = ChatChannelStore(db_path)
        await store.init()  # must not crash
        await store.close()

    async def test_shared_docs_no_index_on_migration_cols(self, tmp_path):
        db_path = tmp_path / "shared_docs.db"
        _seed_db(db_path, SHARED_DOCS_V0_SCHEMA)
        store = SharedDocsStore(db_path)
        await store.init()  # must not crash
        await store.close()
