import pytest
import pytest_asyncio

from tinyagentos.decisions.decision_store import DecisionStore


@pytest_asyncio.fixture
async def store(tmp_path):
    s = DecisionStore(tmp_path / "decisions.db")
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_create_and_get(store):
    d = await store.create(
        "@taOS-dev", "Build X first?", "single_select",
        options=[{"label": "A", "value": "a", "recommended": True, "rationale": "best"},
                 {"label": "B", "value": "b"}],
        context="why", project_id="prj-1", user_id="u1",
    )
    assert d["id"].startswith("dec-")
    assert d["status"] == "pending"
    assert d["options"][0]["recommended"] is True
    got = await store.get(d["id"])
    assert got["question"] == "Build X first?"


@pytest.mark.asyncio
async def test_invalid_type_rejected(store):
    with pytest.raises(ValueError):
        await store.create("@a", "q", "bogus_type")


@pytest.mark.asyncio
async def test_metadata_round_trips(store):
    meta = {"kind": "app_grant", "app_id": "stream-chat", "capabilities": ["app.net"]}
    d = await store.create("@a", "grant?", "multi_select",
                           options=[{"label": "Net", "value": "app.net"}], metadata=meta)
    assert d["metadata"] == meta
    got = await store.get(d["id"])
    assert got["metadata"] == meta
    # Omitted metadata defaults to an empty dict, not None.
    d2 = await store.create("@a", "q", "free_text")
    assert d2["metadata"] == {}


@pytest.mark.asyncio
async def test_list_filters(store):
    await store.create("@a", "q1", "approve_deny", project_id="p1", user_id="u1")
    b = await store.create("@a", "q2", "free_text", project_id="p2", user_id="u1")
    await store.answer(b["id"], "done", "u1")
    assert len(await store.list()) == 2
    assert len(await store.list(status="pending")) == 1
    assert len(await store.list(status="answered")) == 1
    assert len(await store.list(project_id="p1")) == 1
    assert len(await store.list(user_id="u1")) == 2


@pytest.mark.asyncio
async def test_answer_then_cannot_reanswer(store):
    d = await store.create("@a", "q", "approve_deny", user_id="u1")
    upd = await store.answer(d["id"], "approve", "u1")
    assert upd["status"] == "answered"
    assert upd["answer"]["value"] == "approve"
    assert upd["answer"]["answered_by"] == "u1"
    # second answer on a non-pending decision is rejected
    assert await store.answer(d["id"], "deny", "u1") is None


@pytest.mark.asyncio
async def test_answer_unknown_returns_none(store):
    assert await store.answer("dec-missing", "x", "u1") is None


@pytest.mark.asyncio
async def test_supersede(store):
    d = await store.create("@a", "q", "single_select",
                           options=[{"label": "x", "value": "x"}], user_id="u1")
    assert await store.supersede(d["id"]) is True
    assert (await store.get(d["id"]))["status"] == "superseded"


@pytest.mark.asyncio
async def test_branching_fields_reserved(store):
    d = await store.create("@a", "q", "free_text", user_id="u1",
                           parent_decision_id="dec-parent", checkpoint_ref="abc123",
                           timeline_id="t1")
    got = await store.get(d["id"])
    assert got["parent_decision_id"] == "dec-parent"
    assert got["checkpoint_ref"] == "abc123"
    assert got["timeline_id"] == "t1"


@pytest.mark.asyncio
async def test_list_filter_from_agent(store):
    """from_agent filter returns only decisions raised by that agent."""
    await store.create("@agent-x", "q1", "free_text", user_id="u1")
    await store.create("@agent-y", "q2", "free_text", user_id="u1")
    x_only = await store.list(from_agent="@agent-x")
    assert len(x_only) == 1
    assert x_only[0]["from_agent"] == "@agent-x"


@pytest.mark.asyncio
async def test_list_project_id_none_is_null(store):
    """An explicit project_id=None must match only NULL-project decisions
    (IS NULL), not all decisions.  A global grant filters to null-project
    decisions only, per _resolve_decision_actor's posting rule."""
    null_d = await store.create("@a", "q1", "approve_deny", user_id="u1")
    proj_d = await store.create("@a", "q2", "approve_deny", project_id="p1", user_id="u1")
    items = await store.list(project_id=None)
    assert len(items) == 1
    assert items[0]["id"] == null_d["id"]
    assert items[0]["project_id"] is None


@pytest.mark.asyncio
async def test_answer_source_persistence(store):
    """Answer source field is persisted and round-trips correctly."""
    d = await store.create("@a", "q", "approve_deny", user_id="u1")
    upd = await store.answer(d["id"], "approve", "u1", source="in_app")
    assert upd["answer"]["source"] == "in_app"

    d2 = await store.create("@a", "q2", "approve_deny", user_id="u1")
    upd2 = await store.answer(d2["id"], "deny", "u1", source="mirrored_from_chat")
    assert upd2["answer"]["source"] == "mirrored_from_chat"

    # Default source
    d3 = await store.create("@a", "q3", "approve_deny", user_id="u1")
    upd3 = await store.answer(d3["id"], "approve", "u1")
    assert upd3["answer"]["source"] == "in_app"


@pytest.mark.asyncio
async def test_create_retries_on_id_collision(store, monkeypatch):
    """new_id collision in a plain BaseStore (non-projects) must be retried."""
    import tinyagentos.decisions.decision_store as ds_mod

    call_count = 0
    duplicate_id = "dec-collision"
    fresh_id = "dec-fresh"

    def fake_new_id(prefix):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            return duplicate_id
        return fresh_id

    monkeypatch.setattr(ds_mod, "new_id", fake_new_id)

    d1 = await store.create(
        "@a", "q1", "single_select",
        options=[{"label": "A", "value": "a"}],
        user_id="u1",
    )
    assert d1["id"] == duplicate_id

    d2 = await store.create(
        "@a", "q2", "single_select",
        options=[{"label": "B", "value": "b"}],
        user_id="u1",
    )
    assert d2["id"] == fresh_id
    assert call_count == 3


@pytest.mark.asyncio
async def test_notes_table_created_on_existing_db(tmp_path):
    """The decision_notes table is added to an existing database on init,
    not only on fresh install."""
    import aiosqlite

    db_path = tmp_path / "existing.db"
    conn = await aiosqlite.connect(str(db_path))
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS decisions (
            id TEXT PRIMARY KEY, from_agent TEXT NOT NULL, project_id TEXT,
            user_id TEXT NOT NULL DEFAULT '', question TEXT NOT NULL,
            type TEXT NOT NULL, options TEXT NOT NULL DEFAULT '[]',
            context TEXT NOT NULL DEFAULT '', priority TEXT NOT NULL DEFAULT 'normal',
            status TEXT NOT NULL DEFAULT 'pending', answer TEXT,
            created_at REAL NOT NULL, answered_at REAL, deadline REAL,
            checkpoint_ref TEXT, parent_decision_id TEXT, timeline_id TEXT,
            metadata TEXT NOT NULL DEFAULT '{}'
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_decisions_status ON decisions(status, created_at DESC)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_decisions_project ON decisions(project_id, status)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_decisions_user ON decisions(user_id, status)"
    )
    await conn.commit()
    await conn.close()

    store = DecisionStore(db_path)
    await store.init()
    # list_notes on a missing decision must not crash -- the table must exist.
    notes = await store.list_notes("dec-nonexistent")
    assert notes == []
    # And add_note must work on that upgraded database.
    d = await store.create("@a", "q", "free_text", user_id="u1")
    updated = await store.add_note(d["id"], "hello", "u1")
    assert updated is not None
    assert len(updated["notes"]) == 1
    assert updated["notes"][0]["text"] == "hello"


# ── tsk-5dulr5: asker-side withdraw ──────────────────────────────


@pytest.mark.asyncio
async def test_withdraw_pending_records_reason(store):
    d = await store.create("@a", "still needed?", "approve_deny", user_id="u1")
    w = await store.withdraw(d["id"], "moot: satisfied elsewhere", "@a")
    assert w["status"] == "withdrawn"
    assert w["withdraw_reason"] == "moot: satisfied elsewhere"
    assert w["withdrawn_by"] == "@a"
    assert w["withdrawn_at"] is not None
    # Leaves the pending inbox, stays in history.
    assert all(x["id"] != d["id"] for x in await store.list(status="pending"))
    assert [x["id"] for x in await store.list(status="withdrawn")] == [d["id"]]


@pytest.mark.asyncio
async def test_withdraw_is_terminal(store):
    d = await store.create("@a", "q", "approve_deny", user_id="u1")
    await store.answer(d["id"], "approve", "u1")
    # answered -> withdraw refused, record untouched
    assert await store.withdraw(d["id"], "late", "@a") is None
    assert (await store.get(d["id"]))["status"] == "answered"

    d2 = await store.create("@a", "q2", "approve_deny", user_id="u1")
    assert (await store.withdraw(d2["id"], "moot", "@a"))["status"] == "withdrawn"
    # withdrawn is terminal: no re-withdraw, no answer, no supersede
    assert await store.withdraw(d2["id"], "again", "@a") is None
    assert await store.answer(d2["id"], "approve", "u1") is None
    assert await store.supersede(d2["id"]) is False
    got = await store.get(d2["id"])
    assert got["status"] == "withdrawn" and got["withdraw_reason"] == "moot"


@pytest.mark.asyncio
async def test_withdraw_columns_added_to_existing_db(tmp_path):
    """Existing-DB upgrade: a decisions table created before the withdraw
    columns existed gains them on init, and withdraw works on the old row."""
    import aiosqlite

    db_path = tmp_path / "legacy.db"
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """CREATE TABLE decisions (
                id TEXT PRIMARY KEY, from_agent TEXT NOT NULL, project_id TEXT,
                user_id TEXT NOT NULL DEFAULT '', question TEXT NOT NULL,
                type TEXT NOT NULL, options TEXT NOT NULL DEFAULT '[]',
                context TEXT NOT NULL DEFAULT '', priority TEXT NOT NULL DEFAULT 'normal',
                status TEXT NOT NULL DEFAULT 'pending', answer TEXT,
                created_at REAL NOT NULL, answered_at REAL, deadline REAL,
                checkpoint_ref TEXT, parent_decision_id TEXT, timeline_id TEXT,
                metadata TEXT NOT NULL DEFAULT '{}')"""
        )
        await db.execute(
            "INSERT INTO decisions (id, from_agent, user_id, question, type, created_at) "
            "VALUES ('dec-old', '@a', 'u1', 'old q', 'approve_deny', 1.0)"
        )
        await db.commit()

    s = DecisionStore(db_path)
    await s.init()
    try:
        cols = {
            r[1] for r in await (await s._db.execute("PRAGMA table_info(decisions)")).fetchall()
        }
        assert {"withdraw_reason", "withdrawn_at", "withdrawn_by"} <= cols
        w = await s.withdraw("dec-old", "moot", "@a")
        assert w["status"] == "withdrawn" and w["withdraw_reason"] == "moot"
    finally:
        await s.close()
