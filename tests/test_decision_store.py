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
