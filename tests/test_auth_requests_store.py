import pytest
from datetime import datetime, timezone, timedelta
from itertools import count
from unittest.mock import patch, AsyncMock

import uuid

from tinyagentos.agent_registry_store import (
    AgentRegistryStore,
    load_or_create_signing_keypair,
    mint_registry_token,
)
from tinyagentos.auth_requests_store import AuthRequestsStore


async def _store(tmp_path):
    s = AuthRequestsStore(tmp_path / "auth.db")
    await s.init()
    return s


@pytest.mark.asyncio
async def test_create_returns_full_pending_record(tmp_path):
    s = await _store(tmp_path)
    rec = await s.create(
        identity_claim="agent-x",
        framework="acp",
        requested_scopes=["read", "write"],
        requested_skills=["skill.a", "skill.b"],
        reason="need access",
        duration_secs=3600,
        project_id="proj-1",
    )
    assert rec["identity_claim"] == "agent-x"
    assert rec["framework"] == "acp"
    assert rec["requested_scopes"] == ["read", "write"]
    assert rec["requested_skills"] == ["skill.a", "skill.b"]
    assert rec["reason"] == "need access"
    assert rec["duration_secs"] == 3600
    assert rec["project_id"] == "proj-1"
    assert rec["status"] == "pending"
    assert rec["canonical_id"] is None
    assert rec["token"] is None
    assert rec["granted_scopes"] is None
    assert rec["decided_ts"] is None
    assert rec["decided_by"] is None
    assert rec["id"]
    assert rec["created_ts"]
    await s.close()


@pytest.mark.asyncio
async def test_create_defaults(tmp_path):
    s = await _store(tmp_path)
    rec = await s.create(identity_claim="a", framework="b", requested_scopes=[])
    assert rec["requested_skills"] == []
    assert rec["reason"] == ""
    assert rec["duration_secs"] is None
    assert rec["project_id"] is None
    assert rec["status"] == "pending"
    await s.close()


@pytest.mark.asyncio
async def test_get_returns_none_for_unknown_id(tmp_path):
    s = await _store(tmp_path)
    assert await s.get("does-not-exist") is None
    await s.close()


@pytest.mark.asyncio
async def test_get_roundtrip(tmp_path):
    s = await _store(tmp_path)
    rec = await s.create(identity_claim="x", framework="y", requested_scopes=["s1"])
    fetched = await s.get(rec["id"])
    assert fetched == rec
    await s.close()


@pytest.mark.asyncio
async def test_list_pending_empty(tmp_path):
    s = await _store(tmp_path)
    assert await s.list_pending() == []
    await s.close()


@pytest.mark.asyncio
async def test_list_pending_returns_only_pending_ordered_by_created_ts(tmp_path):
    base = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    counter = count()

    def _now(tz=None):
        ts = base + timedelta(seconds=next(counter))
        return ts.replace(tzinfo=timezone.utc)

    with patch("tinyagentos.auth_requests_store.datetime") as mock_dt:
        mock_dt.now.side_effect = _now
        mock_dt.timezone = timezone
        mock_dt.timedelta = timedelta
        s = await _store(tmp_path)
        r1 = await s.create(identity_claim="a", framework="f", requested_scopes=[])
        r2 = await s.create(identity_claim="b", framework="f", requested_scopes=[])
        r3 = await s.create(identity_claim="c", framework="f", requested_scopes=[])
        await s.set_decision(r2["id"], "accepted", canonical_id="cid", token="t", decided_by="admin")
        pending = await s.list_pending()
        ids = [r["id"] for r in pending]
        assert ids == [r1["id"], r3["id"]]
        assert all(r["status"] == "pending" for r in pending)
        await s.close()


@pytest.mark.asyncio
async def test_count_pending_for(tmp_path):
    s = await _store(tmp_path)
    await s.create(identity_claim="a", framework="f", requested_scopes=[])
    await s.create(identity_claim="a", framework="f", requested_scopes=[])
    await s.create(identity_claim="a", framework="g", requested_scopes=[])
    await s.create(identity_claim="b", framework="f", requested_scopes=[])
    assert await s.count_pending_for("a", "f") == 2
    assert await s.count_pending_for("a", "g") == 1
    assert await s.count_pending_for("b", "f") == 1
    assert await s.count_pending_for("z", "f") == 0
    await s.close()


@pytest.mark.asyncio
async def test_count_pending_for_excludes_decided(tmp_path):
    s = await _store(tmp_path)
    r1 = await s.create(identity_claim="a", framework="f", requested_scopes=[])
    r2 = await s.create(identity_claim="a", framework="f", requested_scopes=[])
    await s.set_decision(r1["id"], "accepted", canonical_id="c", token="t", decided_by="admin")
    assert await s.count_pending_for("a", "f") == 1
    await s.close()


@pytest.mark.asyncio
async def test_set_decision_accepts_pending(tmp_path):
    s = await _store(tmp_path)
    rec = await s.create(identity_claim="a", framework="f", requested_scopes=["read"])
    decided = await s.set_decision(
        rec["id"], "accepted",
        canonical_id="canonical-1", token="jwt-token",
        granted_scopes=["read"], decided_by="admin",
    )
    assert decided is not None
    assert decided["status"] == "accepted"
    assert decided["canonical_id"] == "canonical-1"
    assert decided["token"] == "jwt-token"
    assert decided["granted_scopes"] == ["read"]
    assert decided["decided_by"] == "admin"
    assert decided["decided_ts"]
    await s.close()


@pytest.mark.asyncio
async def test_set_decision_refuses_pending(tmp_path):
    s = await _store(tmp_path)
    rec = await s.create(identity_claim="a", framework="f", requested_scopes=[])
    decided = await s.set_decision(rec["id"], "refused", decided_by="admin")
    assert decided["status"] == "refused"
    assert decided["canonical_id"] is None
    assert decided["token"] is None
    assert decided["granted_scopes"] is None
    assert decided["decided_by"] == "admin"
    assert decided["decided_ts"]
    await s.close()


@pytest.mark.asyncio
async def test_set_decision_invalid_status_raises(tmp_path):
    s = await _store(tmp_path)
    rec = await s.create(identity_claim="a", framework="f", requested_scopes=[])
    with pytest.raises(ValueError, match="accepted"):
        await s.set_decision(rec["id"], "bogus", decided_by="admin")
    await s.close()


@pytest.mark.asyncio
async def test_set_decision_already_decided_returns_none(tmp_path):
    s = await _store(tmp_path)
    rec = await s.create(identity_claim="a", framework="f", requested_scopes=[])
    first = await s.set_decision(rec["id"], "accepted", canonical_id="c", token="t", decided_by="admin")
    second = await s.set_decision(rec["id"], "refused", decided_by="admin")
    assert first is not None
    assert second is None
    await s.close()


@pytest.mark.asyncio
async def test_set_decision_unknown_id_returns_none(tmp_path):
    s = await _store(tmp_path)
    result = await s.set_decision("no-such-id", "accepted", decided_by="admin")
    assert result is None
    await s.close()


@pytest.mark.asyncio
async def test_set_decision_concurrent_winner(tmp_path):
    s = await _store(tmp_path)
    rec = await s.create(identity_claim="a", framework="f", requested_scopes=[])
    r1 = await s.set_decision(rec["id"], "accepted", canonical_id="c1", token="t1", decided_by="one")
    r2 = await s.set_decision(rec["id"], "refused", decided_by="two")
    assert r1 is not None
    assert r2 is None
    final = await s.get(rec["id"])
    assert final["status"] == "accepted"
    assert final["canonical_id"] == "c1"
    await s.close()


@pytest.mark.asyncio
async def test_set_decision_without_granted_scopes_null(tmp_path):
    s = await _store(tmp_path)
    rec = await s.create(identity_claim="a", framework="f", requested_scopes=[])
    decided = await s.set_decision(rec["id"], "accepted", decided_by="admin")
    assert decided["granted_scopes"] is None
    await s.close()


@pytest.mark.asyncio
async def test_create_then_get_then_decide_roundtrip(tmp_path):
    s = await _store(tmp_path)
    rec = await s.create(
        identity_claim="agent-z",
        framework="acp",
        requested_scopes=["a", "b"],
        requested_skills=["sk"],
        reason="r",
        duration_secs=60,
        project_id="p",
    )
    fetched = await s.get(rec["id"])
    assert fetched["status"] == "pending"
    decided = await s.set_decision(
        rec["id"], "accepted",
        canonical_id="cid", token="tok", granted_scopes=["a"], decided_by="u",
    )
    final = await s.get(rec["id"])
    assert final == decided
    assert final["status"] == "accepted"
    assert final["granted_scopes"] == ["a"]
    pending = await s.list_pending()
    assert rec["id"] not in [r["id"] for r in pending]
    await s.close()


@pytest.mark.asyncio
async def test_existing_db_gains_proven_canonical_id_column(tmp_path):
    """A DB created with the pre-change schema gains the column on init and old rows read back None."""
    # Create a fresh DB (without proven_canonical_id column)
    import aiosqlite
    
    db_path = tmp_path / "old_auth.db"
    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            """CREATE TABLE auth_requests (
                id TEXT PRIMARY KEY,
                identity_claim TEXT NOT NULL DEFAULT '',
                framework TEXT NOT NULL DEFAULT '',
                requested_scopes TEXT NOT NULL DEFAULT '[]',
                requested_skills TEXT NOT NULL DEFAULT '[]',
                reason TEXT NOT NULL DEFAULT '',
                duration_secs INTEGER,
                project_id TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                canonical_id TEXT,
                token TEXT,
                granted_scopes TEXT,
                created_ts TEXT NOT NULL,
                decided_ts TEXT,
                decided_by TEXT,
                kind TEXT NOT NULL DEFAULT 'scope_request',
                requested_project_name TEXT,
                requested_project_slug TEXT,
                purpose TEXT DEFAULT ''
            );"""
        )
        await db.commit()

    # Now open with the new store class - it should add the column
    s = AuthRequestsStore(db_path)
    await s.init()

    # Insert a row without proven_canonical_id
    record = await s.create(
        identity_claim="old-agent",
        framework="test",
        requested_scopes=["a2a_receive"],
        reason="testing",
    )
    # The new column should be None for old records
    assert record["proven_canonical_id"] is None

    # Close and reopen to verify the column persists
    await s.close()

    s2 = AuthRequestsStore(db_path)
    await s2.init()
    fetched = await s2.get(record["id"])
    assert fetched["proven_canonical_id"] is None

    await s2.close()


@pytest.mark.asyncio
async def test_uninitialised_store_raises(tmp_path):
    s = AuthRequestsStore(tmp_path / "auth.db")
    with pytest.raises(RuntimeError, match="init"):
        await s.create(identity_claim="a", framework="b", requested_scopes=[])
    with pytest.raises(RuntimeError, match="init"):
        await s.get("x")
    with pytest.raises(RuntimeError, match="init"):
        await s.list_pending()
    with pytest.raises(RuntimeError, match="init"):
        await s.count_pending_for("a", "b")
    with pytest.raises(RuntimeError, match="init"):
        await s.set_decision("x", "accepted", decided_by="a")
    await s.close()
