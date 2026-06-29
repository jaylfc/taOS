import pytest
import pytest_asyncio

from tinyagentos.receipt_store import ReceiptStore


@pytest_asyncio.fixture
async def store(tmp_path):
    s = ReceiptStore(tmp_path / "receipts.db")
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_record_and_get_round_trips_json(store):
    rid = await store.record(
        "taos-dev-20260629-090000",
        handle="@taOS-dev",
        project_id="prj-1",
        workspace_hash="abc123",
        capability="files.write",
        capability_granted_at="2026-06-29T09:00:00+00:00",
        tool_name="file_write",
        tool_args={"path": "a.py", "bytes": 12},
        input_refs=[{"name": "spec", "hash": "deadbeef"}],
        output_ref="sha256:cafe",
        result_summary="wrote a.py",
        files_changed=[{"path": "a.py", "hash_before": "", "hash_after": "x", "bytes_delta": 12}],
        stop_reason="completed",
        redactions=[{"field": "tool_args.secret", "reason": "credential"}],
        human_approval={"decision_id": "dec-abc123", "answered_by": "u1", "value": "approve"},
        trace_id="trace-1",
        board_audit_event_id="ba-aaaa1111",
        decision_id="dec-abc123",
        created_by_user_id="u1",
        metadata={"note": "first"},
    )
    assert rid.startswith("rct-")
    got = await store.get(rid)
    # JSON fields come back as parsed Python objects, not strings.
    assert got["tool_args"] == {"path": "a.py", "bytes": 12}
    assert got["files_changed"][0]["bytes_delta"] == 12
    assert got["human_approval"]["answered_by"] == "u1"
    assert got["redactions"][0]["reason"] == "credential"
    assert got["agent_canonical_id"] == "taos-dev-20260629-090000"
    assert got["trace_id"] == "trace-1"
    assert got["decision_id"] == "dec-abc123"


@pytest.mark.asyncio
async def test_agent_canonical_id_is_required(store):
    with pytest.raises(ValueError):
        await store.record("")


@pytest.mark.asyncio
async def test_minimal_receipt_defaults(store):
    rid = await store.record("agent-x")
    got = await store.get(rid)
    assert got["tool_args"] == {}
    assert got["files_changed"] == []
    assert got["redactions"] == []
    assert got["human_approval"] is None  # absent side-effect approval stays null
    assert got["capability"] == ""


@pytest.mark.asyncio
async def test_get_missing_returns_none(store):
    assert await store.get("rct-nope") is None


@pytest.mark.asyncio
async def test_list_newest_first_and_filters(store):
    a = await store.record("agent-a", project_id="prj-1", trace_id="t1")
    b = await store.record("agent-b", project_id="prj-2", trace_id="t2")
    c = await store.record("agent-a", project_id="prj-1", trace_id="t1")

    everything = await store.list()
    # Newest first by insertion order.
    assert [r["id"] for r in everything] == [c, b, a]

    by_agent = await store.list(agent_canonical_id="agent-a")
    assert {r["id"] for r in by_agent} == {a, c}

    by_project = await store.list(project_id="prj-2")
    assert [r["id"] for r in by_project] == [b]

    by_trace = await store.list(trace_id="t1")
    assert {r["id"] for r in by_trace} == {a, c}


@pytest.mark.asyncio
async def test_list_limit_is_clamped(store):
    for _ in range(5):
        await store.record("agent-a")
    assert len(await store.list(limit=2)) == 2
    # Over-large + non-positive limits are clamped, never error.
    assert len(await store.list(limit=10_000)) == 5
    assert len(await store.list(limit=0)) >= 1


@pytest.mark.asyncio
async def test_corrupt_json_field_is_flagged_not_silent(store):
    # An audit ledger must surface on-disk corruption, not pass a malformed JSON
    # column off as valid data. Simulate corruption and read it back.
    rid = await store.record("agent-x", tool_args={"ok": 1})
    await store._db.execute("UPDATE receipts SET tool_args = ? WHERE id = ?", ("{not json", rid))
    await store._db.commit()
    got = await store.get(rid)
    assert got["tool_args"] == {"_unparsed": "{not json"}  # flagged, lossless


@pytest.mark.asyncio
async def test_append_only_no_mutation_surface(store):
    # The store is append-only: it must not expose update/delete (the Time
    # Machine guarantee). Guards against a future regression adding one.
    assert not hasattr(store, "update")
    assert not hasattr(store, "delete")
    assert not hasattr(store, "set_status")
