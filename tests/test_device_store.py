import pytest
import pytest_asyncio
from tinyagentos.device_store import DeviceStore, DEVICE_TOKEN_PREFIX


@pytest_asyncio.fixture
async def store(tmp_path):
    s = DeviceStore(tmp_path / "devices.db")
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_register_mints_scoped_token_and_persists(store):
    dev = await store.register(user_id="u1", platform="ios", display_name="iPhone")
    assert dev["device_id"]
    assert dev["user_id"] == "u1"
    assert dev["platform"] == "ios"
    assert dev["display_name"] == "iPhone"
    assert dev["scoped_token"].startswith(DEVICE_TOKEN_PREFIX)
    assert dev["revoked"] == 0
    assert int(dev["registered_at"]) > 0

    fetched = await store.get(dev["device_id"])
    assert fetched["scoped_token"] == dev["scoped_token"]


@pytest.mark.asyncio
async def test_get_by_token_resolves_then_stops_after_revoke(store):
    dev = await store.register(user_id="u1", platform="ios")
    assert (await store.get_by_token(dev["scoped_token"]))["device_id"] == dev["device_id"]
    assert await store.get_by_token("taosdev_nope") is None

    assert await store.revoke(dev["device_id"]) is True
    assert await store.get_by_token(dev["scoped_token"]) is None


@pytest.mark.asyncio
async def test_list_for_user_scopes_and_hides_token(store):
    a = await store.register(user_id="u1", platform="ios")
    await store.register(user_id="u2", platform="ios")
    revoked = await store.register(user_id="u1", platform="watchos")
    await store.revoke(revoked["device_id"])

    rows = await store.list_for_user("u1")
    assert [r["device_id"] for r in rows] == [a["device_id"]]
    assert "scoped_token" not in rows[0]


@pytest.mark.asyncio
async def test_update_push_token(store):
    dev = await store.register(user_id="u1", platform="ios", push_token="old")
    updated = await store.update_push_token(dev["device_id"], "new")
    assert updated["push_token"] == "new"
    assert (await store.get(dev["device_id"]))["push_token"] == "new"


@pytest.mark.asyncio
async def test_block_kills_token_but_stays_listed(store):
    dev = await store.register(user_id="u1", platform="ios", push_token="pt1")

    assert await store.block(dev["device_id"]) is True
    # Token is dead (blocked implies revoked).
    assert await store.get_by_token(dev["scoped_token"]) is None
    row = await store.get(dev["device_id"])
    assert row["revoked"] == 1 and row["blocked"] == 1
    # Unlike a plain revoke, the row stays visible so the owner can see the
    # safety valve is engaged.
    listed = await store.list_for_user("u1")
    assert [r["device_id"] for r in listed] == [dev["device_id"]]
    # Second block is a no-op.
    assert await store.block(dev["device_id"]) is False


@pytest.mark.asyncio
async def test_unblock_leaves_token_dead_and_hides_row(store):
    dev = await store.register(user_id="u1", platform="ios")
    await store.block(dev["device_id"])

    assert await store.unblock(dev["device_id"]) is True
    # The old token stays dead: unblock does not resurrect it.
    assert await store.get_by_token(dev["scoped_token"]) is None
    # Now a plain revoked row, hidden from the list.
    assert await store.list_for_user("u1") == []
    row = await store.get(dev["device_id"])
    assert row["revoked"] == 1 and row["blocked"] == 0
    # Second unblock is a no-op.
    assert await store.unblock(dev["device_id"]) is False


@pytest.mark.asyncio
async def test_find_blocked_by_push_token(store):
    dev = await store.register(user_id="u1", platform="ios", push_token="pt1")
    # Not blocked yet: no match.
    assert await store.find_blocked_by_push_token("u1", "pt1") is None

    await store.block(dev["device_id"])
    assert await store.find_blocked_by_push_token("u1", "pt1") == dev["device_id"]
    # Scoped to the owning user and the exact push token.
    assert await store.find_blocked_by_push_token("u2", "pt1") is None
    assert await store.find_blocked_by_push_token("u1", "other") is None

    await store.unblock(dev["device_id"])
    assert await store.find_blocked_by_push_token("u1", "pt1") is None


@pytest.mark.asyncio
async def test_blocked_token_rejected_by_get_by_token(store):
    """A blocked device's scoped token must not resolve -- get_by_token is the
    store-level gate used by require_device (the real device-bearer auth path)."""
    dev = await store.register(user_id="u1", platform="ios", push_token="pt1")
    await store.block(dev["device_id"])
    assert await store.get_by_token(dev["scoped_token"]) is None
    # Revoke-only also kills the token.
    dev2 = await store.register(user_id="u1", platform="ios")
    await store.revoke(dev2["device_id"])
    assert await store.get_by_token(dev2["scoped_token"]) is None


@pytest.mark.asyncio
async def test_blocked_device_consumes_slot(store):
    """Pinned behaviour: a blocked device remains in list_for_user (and thus
    counts against _MAX_DEVICES_PER_USER) until unblocked. Revoked-only
    devices do NOT count."""
    a = await store.register(user_id="u1", platform="ios")
    b = await store.register(user_id="u1", platform="ios")
    # Block device b -- it stays listed.
    await store.block(b["device_id"])
    listed = [r["device_id"] for r in await store.list_for_user("u1")]
    assert set(listed) == {a["device_id"], b["device_id"]}
    # Unblock b -- it now falls out of list_for_user (revoked-only, hidden).
    await store.unblock(b["device_id"])
    listed = [r["device_id"] for r in await store.list_for_user("u1")]
    assert listed == [a["device_id"]]


@pytest.mark.asyncio
async def test_revoke_isolates_devices(store):
    """Revoking device A must not affect device B on the same account."""
    a = await store.register(user_id="u1", platform="ios", display_name="A")
    b = await store.register(user_id="u1", platform="ios", display_name="B")
    assert await store.revoke(a["device_id"]) is True
    # A's token is dead...
    assert await store.get_by_token(a["scoped_token"]) is None
    # ...but B is untouched.
    row_b = await store.get(b["device_id"])
    assert row_b["revoked"] == 0
    b_token = await store.get_by_token(b["scoped_token"])
    assert b_token is not None
    assert b_token["device_id"] == b["device_id"]
    # A is revoked-only (not blocked), so it is hidden from list_for_user;
    # B remains listed. Revoking A did not affect B's slot or token.
    listed = [r["device_id"] for r in await store.list_for_user("u1")]
    assert listed == [b["device_id"]]


@pytest.mark.asyncio
async def test_blocked_column_migration_over_existing_db(tmp_path):
    """The `blocked` column must be retrofitted onto a database created BEFORE
    this change. A fresh-schema test passes vacuously; this one builds the old
    table by hand and fails if the guarded ALTER in _post_init is removed."""
    import aiosqlite

    path = tmp_path / "devices.db"
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """
            CREATE TABLE devices (
                device_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                platform TEXT NOT NULL,
                push_token TEXT NOT NULL DEFAULT '',
                scoped_token TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL DEFAULT '',
                registered_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                last_seen INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                revoked INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        await db.execute(
            "INSERT INTO devices (device_id, user_id, platform, scoped_token) "
            "VALUES ('d-old', 'u1', 'ios', 'taosdev_pre_block')"
        )
        await db.commit()

    s = DeviceStore(path)
    await s.init()
    try:
        # The pre-existing row gained the column with the safe default...
        row = await s.get("d-old")
        assert row is not None and row["blocked"] == 0
        # ...and every blocked-aware query path works against the migrated DB.
        assert (await s.get_by_token("taosdev_pre_block"))["device_id"] == "d-old"
        assert await s.block("d-old") is True
        assert await s.get_by_token("taosdev_pre_block") is None
    finally:
        await s.close()
