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
    # After redeem the invite is claimed (not yet redeemed — the route flips
    # claimed→redeemed via mark_redeemed after approve succeeds, #1993).
    assert record["status"] == "claimed"
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


@pytest.mark.asyncio
async def test_revoke_expired_invite(store):
    """An expired invite can still be revoked (cleanup from the pending list)."""
    result = await store.mint(
        project_id="prj-1",
        scopes=[],
        approval_mode="auto",
        check_interval_secs=1800,
        created_by="u",
    )
    iid = result["record"]["invite_id"]
    await store._db.execute(
        "UPDATE project_invites SET expires_ts = 1 WHERE invite_id = ?", (iid,)
    )
    await store._db.commit()
    row = await store.get(iid)
    assert row["status"] == "expired"
    ok = await store.revoke(iid)
    assert ok is True
    row = await store.get(iid)
    assert row["status"] == "revoked"


@pytest.mark.asyncio
async def test_revoke_redeemed_invite_returns_false(store):
    result = await store.mint(
        project_id="prj-1",
        scopes=[],
        approval_mode="auto",
        check_interval_secs=1800,
        created_by="u",
    )
    iid = result["record"]["invite_id"]
    pin = result["pin"]
    await store.redeem(iid, pin)
    assert await store.revoke(iid) is False


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
    # After redeem the invite is claimed (not yet redeemed — the route flips
    # claimed→redeemed via mark_redeemed after approve succeeds, #1993).
    assert record["status"] == "claimed"
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


# ---------------------------------------------------------------------------
# mark_redeemed + rollback_to_pending (issue #1993)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_redeemed_flips_claimed_to_redeemed(store):
    result = await store.mint(
        project_id="prj-1",
        scopes=["a2a_send"],
        approval_mode="auto",
        check_interval_secs=1800,
        created_by="u",
    )
    iid = result["record"]["invite_id"]
    # Claim it
    record = await store.redeem(iid, result["pin"])
    assert record["status"] == "claimed"

    # mark_redeemed should flip claimed → redeemed
    await store.mark_redeemed(iid, "agent-x", "req-1")
    row = await store.get(iid)
    assert row["status"] == "redeemed"
    assert row["redeemed_by"] == "agent-x"
    assert row["redeemed_request_id"] == "req-1"


@pytest.mark.asyncio
async def test_rollback_to_pending_after_claim(store):
    result = await store.mint(
        project_id="prj-1",
        scopes=["a2a_send"],
        approval_mode="auto",
        check_interval_secs=1800,
        created_by="u",
    )
    iid = result["record"]["invite_id"]
    # Claim it
    record = await store.redeem(iid, result["pin"])
    assert record["status"] == "claimed"

    # Roll back — simulate approve failure
    await store.rollback_to_pending(iid)
    row = await store.get(iid)
    assert row["status"] == "pending"

    # Can redeem again (re-claim)
    record2 = await store.redeem(iid, result["pin"])
    assert record2["status"] == "claimed"


@pytest.mark.asyncio
async def test_rollback_only_touches_claimed(store):
    result = await store.mint(
        project_id="prj-1",
        scopes=["a2a_send"],
        approval_mode="auto",
        check_interval_secs=1800,
        created_by="u",
    )
    iid = result["record"]["invite_id"]
    # Still pending — rollback should be a no-op, not revert anything else
    await store.rollback_to_pending(iid)
    row = await store.get(iid)
    assert row["status"] == "pending"

    # Claim and then mark as redeemed
    await store.redeem(iid, result["pin"])
    await store.mark_redeemed(iid, "agent-x", "req-1")
    row = await store.get(iid)
    assert row["status"] == "redeemed"

    # Rollback on a redeemed invite is a no-op
    await store.rollback_to_pending(iid)
    row = await store.get(iid)
    assert row["status"] == "redeemed"  # stays redeemed


# ---------------------------------------------------------------------------
# B1: collab invite kind + pin_required + contact_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mint_collab_invite(store):
    result = await store.mint(
        project_id="prj-1",
        scopes=[],
        approval_mode="manual",
        check_interval_secs=1800,
        created_by="u",
        kind="collab",
        pin_required=True,
        contact_id="hub:hogne",
    )
    assert result["record"]["kind"] == "collab"
    assert result["record"]["pin_required"] == 1
    assert result["record"]["contact_id"] == "hub:hogne"
    assert result["record"]["status"] == "pending"
    assert len(result["pin"]) == 4


@pytest.mark.asyncio
async def test_mint_collab_invite_no_pin_required(store):
    result = await store.mint(
        project_id="prj-1",
        scopes=[],
        approval_mode="manual",
        check_interval_secs=1800,
        created_by="u",
        kind="collab",
        pin_required=False,
        contact_id="hub:hogne",
    )
    assert result["record"]["pin_required"] == 0


@pytest.mark.asyncio
async def test_mint_rejects_invalid_kind(store):
    with pytest.raises(ValueError, match="invalid invite kind"):
        await store.mint(
            project_id="prj-1",
            scopes=[],
            approval_mode="auto",
            check_interval_secs=1800,
            created_by="u",
            kind="invalid",
        )


@pytest.mark.asyncio
async def test_default_kind_is_agent(store):
    result = await store.mint(
        project_id="prj-1",
        scopes=["a2a_send"],
        approval_mode="auto",
        check_interval_secs=1800,
        created_by="u",
    )
    assert result["record"]["kind"] == "agent"
    assert result["record"]["pin_required"] == 1
    assert result["record"]["contact_id"] is None


@pytest.mark.asyncio
async def test_mint_collab_rejects_scopes(store):
    with pytest.raises(ValueError, match="collab invites must carry no scopes"):
        await store.mint(
            project_id="prj-1",
            scopes=["a2a_send"],
            approval_mode="manual",
            check_interval_secs=1800,
            created_by="u",
            kind="collab",
        )


@pytest.mark.asyncio
async def test_list_pending_collab_for_contact(store):
    await store.mint(
        project_id="prj-a",
        scopes=[],
        approval_mode="manual",
        check_interval_secs=1800,
        created_by="u",
        kind="collab",
        contact_id="hub:hogne",
    )
    await store.mint(
        project_id="prj-b",
        scopes=[],
        approval_mode="manual",
        check_interval_secs=1800,
        created_by="u",
        kind="collab",
        contact_id="hub:hogne",
    )
    # Agent-kind invite for the same contact — should NOT appear in collab list
    await store.mint(
        project_id="prj-c",
        scopes=["a2a_send"],
        approval_mode="auto",
        check_interval_secs=1800,
        created_by="u",
        kind="agent",
        contact_id="hub:hogne",
    )
    items = await store.list_pending_collab_for_contact("hub:hogne")
    assert len(items) == 2
    for item in items:
        assert item["kind"] == "collab"
        assert item["contact_id"] == "hub:hogne"
        assert "pin_hash" not in item


@pytest.mark.asyncio
async def test_list_pending_collab_for_unknown_contact(store):
    items = await store.list_pending_collab_for_contact("hub:nobody")
    assert items == []


@pytest.mark.asyncio
async def test_mark_accepted_flips_to_redeemed(store):
    result = await store.mint(
        project_id="prj-1",
        scopes=[],
        approval_mode="manual",
        check_interval_secs=1800,
        created_by="u",
        kind="collab",
        contact_id="hub:hogne",
    )
    iid = result["record"]["invite_id"]
    # mark_accepted works directly from pending for collab invites
    await store.mark_accepted(iid, "hub:hogne")
    row = await store.get(iid)
    assert row["status"] == "redeemed"
    assert row["redeemed_by"] == "hub:hogne"


@pytest.mark.asyncio
async def test_mark_accepted_from_claimed(store):
    """mark_accepted also works on claimed invites (agent flow that was claimed first)."""
    result = await store.mint(
        project_id="prj-1",
        scopes=["a2a_send"],
        approval_mode="auto",
        check_interval_secs=1800,
        created_by="u",
        kind="agent",
    )
    iid = result["record"]["invite_id"]
    # Claim first (agent redeem path)
    await store.redeem(iid, result["pin"])
    row = await store.get(iid)
    assert row["status"] == "claimed"
    # mark_accepted should still work from claimed
    await store.mark_accepted(iid, "hub:hogne")
    row = await store.get(iid)
    assert row["status"] == "redeemed"
    assert row["redeemed_by"] == "hub:hogne"


@pytest.mark.asyncio
async def test_mark_expired_pending_invite(store):
    result = await store.mint(
        project_id="prj-1",
        scopes=["a2a_send"],
        approval_mode="auto",
        check_interval_secs=1800,
        created_by="u",
    )
    iid = result["record"]["invite_id"]
    ok = await store.mark_expired(iid)
    assert ok is True
    row = await store.get(iid)
    assert row["status"] == "expired"


@pytest.mark.asyncio
async def test_mark_expired_nonexistent(store):
    ok = await store.mark_expired("000000")
    assert ok is False


@pytest.mark.asyncio
async def test_boot_migration_adds_kind_pin_required_contact_id(tmp_path):
    """A legacy DB without kind/pin_required/contact_id columns must survive init
    with defaults applied."""
    db_path = tmp_path / "legacy_invites_b1.db"
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
            redeemed_by    TEXT,
            redeemed_request_id TEXT,
            display_name   TEXT
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
    # Defaults applied by migration
    assert row.get("kind") == "agent"
    assert row.get("pin_required") == 1
    assert row.get("contact_id") is None
    await store.close()
