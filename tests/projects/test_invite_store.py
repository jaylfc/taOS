import json
import sqlite3
import time

import pytest
import pytest_asyncio

from tinyagentos.projects.invite_store import (
    InviteAlreadyRedeemedError,
    InviteExpiredError,
    InvitePendingCapError,
    InvitePinError,
    InviteRevokedError,
    ProjectInviteStore,
)


@pytest_asyncio.fixture
async def store(tmp_path):
    s = ProjectInviteStore(tmp_path / "project_invites.db")
    await s.init()
    yield s
    await s.close()


def _make_scopes(*extra):
    base = ["project_tasks"]
    return list(dict.fromkeys(list(base) + list(extra)))


# ---------------------------------------------------------------------------
# mint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mint_returns_6_digit_id_and_4_digit_pin(store):
    result = await store.mint(
        project_id="prj-1",
        scopes=["a2a_send"],
        approval_mode="auto",
        check_interval_secs=1800,
        created_by="user-admin",
    )
    assert len(result["record"]["invite_id"]) == 6
    assert result["record"]["invite_id"].isdigit()
    assert len(result["pin"]) == 4
    assert result["pin"].isdigit()


@pytest.mark.asyncio
async def test_mint_forces_project_tasks(store):
    result = await store.mint(
        project_id="prj-1",
        scopes=["a2a_send"],
        approval_mode="auto",
        check_interval_secs=1800,
        created_by="user-admin",
    )
    assert "project_tasks" in result["record"]["scopes"]


@pytest.mark.asyncio
async def test_mint_does_not_duplicate_project_tasks(store):
    result = await store.mint(
        project_id="prj-1",
        scopes=["project_tasks", "a2a_send"],
        approval_mode="auto",
        check_interval_secs=1800,
        created_by="user-admin",
    )
    assert result["record"]["scopes"].count("project_tasks") == 1


@pytest.mark.asyncio
async def test_mint_enforces_pending_cap(store):
    for i in range(10):
        await store.mint(
            project_id="prj-cap",
            scopes=[],
            approval_mode="auto",
            check_interval_secs=1800,
            created_by="u",
        )
    with pytest.raises(InvitePendingCapError):
        await store.mint(
            project_id="prj-cap",
            scopes=[],
            approval_mode="auto",
            check_interval_secs=1800,
            created_by="u",
        )


# ---------------------------------------------------------------------------
# OS-level (project-less) mint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_os_level_mint_no_project_tasks(store):
    """An OS-level invite (project_id=None) keeps exactly the requested scopes;
    project_tasks is NOT forced because the identity is not project-bound."""
    result = await store.mint(
        project_id=None,
        scopes=["a2a_send"],
        approval_mode="auto",
        check_interval_secs=1800,
        created_by="user-admin",
    )
    scopes = result["record"]["scopes"]
    assert scopes == ["a2a_send"]
    assert "project_tasks" not in scopes
    assert result["record"]["project_id"] is None


@pytest.mark.asyncio
async def test_os_level_mint_stores_display_name(store):
    result = await store.mint(
        project_id=None,
        scopes=["a2a_send"],
        approval_mode="auto",
        check_interval_secs=1800,
        created_by="u",
        display_name="Scout",
    )
    iid = result["record"]["invite_id"]
    assert result["record"]["display_name"] == "Scout"
    row = await store.get(iid)
    assert row["display_name"] == "Scout"


@pytest.mark.asyncio
async def test_os_level_pending_cap_uses_is_null(store):
    """The pending cap stays live for OS-level invites (project_id IS NULL);
    SQL ``= NULL`` would silently bypass it."""
    for _ in range(10):
        await store.mint(
            project_id=None,
            scopes=[],
            approval_mode="auto",
            check_interval_secs=1800,
            created_by="u",
        )
    with pytest.raises(InvitePendingCapError):
        await store.mint(
            project_id=None,
            scopes=[],
            approval_mode="auto",
            check_interval_secs=1800,
            created_by="u",
        )


@pytest.mark.asyncio
async def test_os_level_cap_independent_of_project_cap(store):
    """OS-level pending invites do not count against a project's cap and vice
    versa: 10 OS-level + a project mint still succeeds."""
    for _ in range(10):
        await store.mint(
            project_id=None,
            scopes=[],
            approval_mode="auto",
            check_interval_secs=1800,
            created_by="u",
        )
    # A project mint is unaffected by the OS-level pending group.
    result = await store.mint(
        project_id="prj-x",
        scopes=[],
        approval_mode="auto",
        check_interval_secs=1800,
        created_by="u",
    )
    assert result["record"]["project_id"] == "prj-x"


@pytest.mark.asyncio
async def test_list_os_level_omits_pin_hash(store):
    await store.mint(
        project_id=None,
        scopes=["a2a_send"],
        approval_mode="auto",
        check_interval_secs=1800,
        created_by="u",
        display_name="Scout",
    )
    # A project invite must NOT appear in the OS-level listing.
    await store.mint(
        project_id="prj-1",
        scopes=[],
        approval_mode="auto",
        check_interval_secs=1800,
        created_by="u",
    )
    items = await store.list_os_level()
    assert len(items) == 1
    assert items[0]["project_id"] is None
    assert items[0]["display_name"] == "Scout"
    assert "pin_hash" not in items[0]


@pytest.mark.asyncio
async def test_os_level_redeem_single_use(store):
    result = await store.mint(
        project_id=None,
        scopes=["a2a_send"],
        approval_mode="auto",
        check_interval_secs=1800,
        created_by="u",
    )
    iid = result["record"]["invite_id"]
    record = await store.redeem(iid, result["pin"])
    assert record["status"] == "redeemed"
    assert record["project_id"] is None
    with pytest.raises(InviteAlreadyRedeemedError):
        await store.redeem(iid, result["pin"])


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_existing(store):
    result = await store.mint(
        project_id="prj-1",
        scopes=[],
        approval_mode="auto",
        check_interval_secs=1800,
        created_by="u",
    )
    iid = result["record"]["invite_id"]
    row = await store.get(iid)
    assert row is not None
    assert row["invite_id"] == iid
    assert "pin_hash" in row


@pytest.mark.asyncio
async def test_get_missing_returns_none(store):
    assert await store.get("000000") is None


@pytest.mark.asyncio
async def test_get_sweeps_expired(store):
    result = await store.mint(
        project_id="prj-1",
        scopes=[],
        approval_mode="auto",
        check_interval_secs=1800,
        created_by="u",
    )
    iid = result["record"]["invite_id"]
    # Manually push expiry into the past
    await store._db.execute(
        "UPDATE project_invites SET expires_ts = ? WHERE invite_id = ?",
        (time.time() - 1, iid),
    )
    await store._db.commit()
    row = await store.get(iid)
    assert row["status"] == "expired"


# ---------------------------------------------------------------------------
# list_for_project
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_for_project_omits_pin_hash(store):
    result = await store.mint(
        project_id="prj-list",
        scopes=[],
        approval_mode="auto",
        check_interval_secs=1800,
        created_by="u",
    )
    items = await store.list_for_project("prj-list")
    assert len(items) == 1
    assert "pin_hash" not in items[0]


@pytest.mark.asyncio
async def test_list_for_project_empty(store):
    assert await store.list_for_project("no-such") == []


# ---------------------------------------------------------------------------
# revoke
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revoke_pending_invite(store):
    result = await store.mint(
        project_id="prj-1",
        scopes=[],
        approval_mode="auto",
        check_interval_secs=1800,
        created_by="u",
    )
    iid = result["record"]["invite_id"]
    ok = await store.revoke(iid)
    assert ok is True
    row = await store.get(iid)
    assert row["status"] == "revoked"


@pytest.mark.asyncio
async def test_revoke_nonexistent_returns_false(store):
    assert await store.revoke("000000") is False


# ---------------------------------------------------------------------------
# redeem
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redeem_wrong_pin_increments_attempts(store):
    result = await store.mint(
        project_id="prj-1",
        scopes=[],
        approval_mode="auto",
        check_interval_secs=1800,
        created_by="u",
    )
    iid = result["record"]["invite_id"]
    correct_pin = result["pin"]
    for i in range(4):
        with pytest.raises(InvitePinError):
            await store.redeem(iid, "0000")
        row = await store.get(iid)
        assert row["redeem_attempts"] == i + 1


@pytest.mark.asyncio
async def test_redeem_5_wrong_pins_invalidates(store):
    result = await store.mint(
        project_id="prj-1",
        scopes=[],
        approval_mode="auto",
        check_interval_secs=1800,
        created_by="u",
    )
    iid = result["record"]["invite_id"]
    for _ in range(5):
        with pytest.raises(InvitePinError):
            await store.redeem(iid, "0000")
    with pytest.raises(InvitePinError):
        await store.redeem(iid, "0000")
    row = await store.get(iid)
    assert row["status"] == "expired"


@pytest.mark.asyncio
async def test_redeem_correct_pin_single_use(store):
    result = await store.mint(
        project_id="prj-1",
        scopes=[],
        approval_mode="auto",
        check_interval_secs=1800,
        created_by="u",
    )
    iid = result["record"]["invite_id"]
    pin = result["pin"]
    record = await store.redeem(iid, pin)
    assert record["status"] == "redeemed"
    with pytest.raises(InviteAlreadyRedeemedError):
        await store.redeem(iid, pin)


@pytest.mark.asyncio
async def test_redeem_expired_invite(store):
    result = await store.mint(
        project_id="prj-1",
        scopes=[],
        approval_mode="auto",
        check_interval_secs=1800,
        created_by="u",
    )
    iid = result["record"]["invite_id"]
    # Expire it manually
    await store._db.execute(
        "UPDATE project_invites SET expires_ts = ? WHERE invite_id = ?",
        (time.time() - 1, iid),
    )
    await store._db.commit()
    with pytest.raises(InviteExpiredError):
        await store.redeem(iid, result["pin"])


@pytest.mark.asyncio
async def test_redeem_revoked_invite(store):
    result = await store.mint(
        project_id="prj-1",
        scopes=[],
        approval_mode="auto",
        check_interval_secs=1800,
        created_by="u",
    )
    iid = result["record"]["invite_id"]
    await store.revoke(iid)
    with pytest.raises(InviteRevokedError):
        await store.redeem(iid, result["pin"])


# ---------------------------------------------------------------------------
# boot-migration test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_boot_migration_adds_missing_column(tmp_path):
    db_path = tmp_path / "legacy_invites.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE project_invites (
            invite_id      TEXT PRIMARY KEY,
            project_id     TEXT NOT NULL,
            pin_hash       TEXT NOT NULL,
            scopes         TEXT NOT NULL,
            approval_mode  TEXT NOT NULL,
            check_interval_secs INTEGER,
            created_by     TEXT NOT NULL,
            created_ts     REAL NOT NULL,
            expires_ts     REAL NOT NULL,
            redeem_attempts INTEGER DEFAULT 0,
            status         TEXT NOT NULL,
            redeemed_by    TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO project_invites
            (invite_id, project_id, pin_hash, scopes, approval_mode,
             check_interval_secs, created_by, created_ts, expires_ts,
             redeem_attempts, status, redeemed_by)
        VALUES ('000001', 'prj-1', 'abc', '[]', 'auto', 1800, 'u', 1.0, ?, 0, 'pending', NULL)
        """,
        (time.time() + 900,),
    )
    conn.commit()
    conn.close()

    store = ProjectInviteStore(db_path)
    await store.init()
    row = await store.get("000001")
    assert row is not None
    assert row["status"] == "pending"
    assert row.get("redeemed_request_id") is None
    # The display_name column is added by the boot migration for legacy DBs.
    assert row.get("display_name") is None
    await store.close()
