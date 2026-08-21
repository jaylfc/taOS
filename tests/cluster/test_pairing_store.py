"""Tests for cluster node revoke/block/unblock in ClusterPairingStore."""
import pytest
import pytest_asyncio

from tinyagentos.cluster.pairing_store import ClusterPairingStore


@pytest_asyncio.fixture
async def store(tmp_path):
    s = ClusterPairingStore(tmp_path / "pairing.db")
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_get_signing_key_rejects_revoked(store):
    await store.announce("wnode", "http://10.0.0.1:6970", "linux", "abc123" * 0)
    import hashlib
    code_hash = hashlib.sha256(b"secret").hexdigest()
    await store.announce("wnode", "http://10.0.0.1:6970", "linux", code_hash)
    await store.confirm("wnode", "secret")
    key = await store.get_signing_key("wnode")
    assert key is not None
    await store.revoke("wnode")
    assert await store.get_signing_key("wnode") is None


@pytest.mark.asyncio
async def test_get_signing_key_rejects_blocked(store):
    import hashlib
    code_hash = hashlib.sha256(b"secret").hexdigest()
    await store.announce("wnode", "http://10.0.0.1:6970", "linux", code_hash)
    await store.confirm("wnode", "secret")
    assert await store.get_signing_key("wnode") is not None
    await store.block("wnode")
    assert await store.get_signing_key("wnode") is None


@pytest.mark.asyncio
async def test_block_prevents_confirm(store):
    """A blocked worker's announce/confirm flow must be refused until unblocked."""
    import hashlib
    code_hash = hashlib.sha256(b"secret").hexdigest()
    await store.announce("wnode", "http://10.0.0.1:6970", "linux", code_hash)
    await store.confirm("wnode", "secret")
    await store.block("wnode")

    # Re-announce a fresh pending entry (new code).
    code_hash2 = hashlib.sha256(b"secret2").hexdigest()
    await store.announce("wnode", "http://10.0.0.1:6970", "linux", code_hash2)
    # Confirm must refuse while blocked.
    assert await store.confirm("wnode", "secret2") is False
    # State still shows blocked.
    state = await store.pairing_state("wnode")
    assert state["blocked"] is True

    # Unblock allows confirm.
    await store.unblock("wnode")
    assert await store.confirm("wnode", "secret2") is True
    state = await store.pairing_state("wnode")
    assert state["blocked"] is False
    assert state["revoked"] is False


@pytest.mark.asyncio
async def test_revoke_allows_repair(store):
    """A revoked (not blocked) worker can re-pair via confirm."""
    import hashlib
    code_hash = hashlib.sha256(b"secret").hexdigest()
    await store.announce("wnode", "http://10.0.0.1:6970", "linux", code_hash)
    await store.confirm("wnode", "secret")
    await store.revoke("wnode")
    assert await store.get_signing_key("wnode") is None

    # Re-announce + re-confirm (revoked, not blocked -- re-pairing allowed).
    code_hash2 = hashlib.sha256(b"secret2").hexdigest()
    await store.announce("wnode", "http://10.0.0.1:6970", "linux", code_hash2)
    assert await store.confirm("wnode", "secret2") is True
    # New key is live.
    assert await store.get_signing_key("wnode") is not None


@pytest.mark.asyncio
async def test_revoke_isolates_nodes(store):
    """Revoking node A must not affect node B."""
    import hashlib
    ch_a = hashlib.sha256(b"secretA").hexdigest()
    ch_b = hashlib.sha256(b"secretB").hexdigest()
    await store.announce("nodeA", "http://10.0.0.1:6970", "linux", ch_a)
    await store.confirm("nodeA", "secretA")
    await store.announce("nodeB", "http://10.0.0.2:6970", "linux", ch_b)
    await store.confirm("nodeB", "secretB")

    key_b = await store.get_signing_key("nodeB")
    assert key_b is not None

    await store.revoke("nodeA")
    assert await store.get_signing_key("nodeA") is None
    # B untouched.
    assert await store.get_signing_key("nodeB") == key_b
    state_b = await store.pairing_state("nodeB")
    assert state_b["revoked"] is False
    assert state_b["blocked"] is False


@pytest.mark.asyncio
async def test_block_unblock_state_machine(store):
    """block: revoke+block. unblock: clears blocked, token stays dead."""
    import hashlib
    code_hash = hashlib.sha256(b"secret").hexdigest()
    await store.announce("wnode", "http://10.0.0.1:6970", "linux", code_hash)
    await store.confirm("wnode", "secret")

    assert await store.block("wnode") is True
    state = await store.pairing_state("wnode")
    assert state["blocked"] is True
    assert state["revoked"] is True
    assert await store.get_signing_key("wnode") is None

    # Unblock clears blocked; revoked stays.
    assert await store.unblock("wnode") is True
    state = await store.pairing_state("wnode")
    assert state["blocked"] is False
    assert state["revoked"] is True
    # Old key still dead -- must re-pair.
    assert await store.get_signing_key("wnode") is None

    # Re-pair to get a live key.
    code_hash2 = hashlib.sha256(b"secret2").hexdigest()
    await store.announce("wnode", "http://10.0.0.1:6970", "linux", code_hash2)
    assert await store.confirm("wnode", "secret2") is True
    assert await store.get_signing_key("wnode") is not None


@pytest.mark.asyncio
async def test_blocked_column_migration_over_existing_db(tmp_path):
    """The blocked/revoked columns must be retrofitted onto a database created
    BEFORE this change, via the guarded _post_init ALTER TABLE."""
    import aiosqlite

    path = tmp_path / "pairing.db"
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            """
            CREATE TABLE cluster_pairings (
                name                TEXT NOT NULL UNIQUE,
                signing_key         BLOB,
                pending_code_hash   TEXT,
                pending_url         TEXT,
                pending_platform    TEXT,
                pending_ts          REAL,
                claim_attempts      INTEGER NOT NULL DEFAULT 0,
                confirmed           INTEGER NOT NULL DEFAULT 0,
                created_ts          REAL,
                confirmed_ts        REAL
            )
            """
        )
        await db.execute(
            "INSERT INTO cluster_pairings (name, signing_key, created_ts, confirmed) "
            "VALUES ('old-node', ?, ?, 1)",
            (b"\x01" * 32, 1234567890.0),
        )
        await db.commit()

    s = ClusterPairingStore(path)
    await s.init()
    try:
        import sqlite3
        conn = sqlite3.connect(str(path))
        cols = {r[1] for r in conn.execute("PRAGMA table_info(cluster_pairings)").fetchall()}
        conn.close()
        assert "blocked" in cols
        assert "revoked" in cols
        # Pre-existing row gained the columns with safe defaults.
        row = await s._fetch_row("old-node")
        assert row["blocked"] == 0
        assert row["revoked"] == 0
        # And the signing key is still retrievable from the migrated DB.
        assert await s.get_signing_key("old-node") is not None
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_block_idempotent(store):
    import hashlib
    code_hash = hashlib.sha256(b"secret").hexdigest()
    await store.announce("wnode", "http://10.0.0.1:6970", "linux", code_hash)
    await store.confirm("wnode", "secret")
    assert await store.block("wnode") is True
    # Second block is a no-op (already blocked).
    assert await store.block("wnode") is False


@pytest.mark.asyncio
async def test_unblock_idempotent(store):
    import hashlib
    code_hash = hashlib.sha256(b"secret").hexdigest()
    await store.announce("wnode", "http://10.0.0.1:6970", "linux", code_hash)
    await store.confirm("wnode", "secret")
    await store.block("wnode")
    assert await store.unblock("wnode") is True
    # Second unblock is a no-op (already unblocked).
    assert await store.unblock("wnode") is False
