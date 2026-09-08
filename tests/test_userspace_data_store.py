"""Unit tests for tinyagentos/userspace/data_store.py.

Covers the KV and table-store operations that UserspaceDataStore exposes,
plus the edge cases (missing keys/ids, upserts, empty lists, per-app
namespacing) all backed by a real aiosqlite database in tmp_path.
"""
import pytest
import pytest_asyncio

from tinyagentos.userspace.data_store import UserspaceDataStore


@pytest_asyncio.fixture
async def store(tmp_path):
    s = UserspaceDataStore(tmp_path / "data.db")
    await s.init()
    yield s
    await s.close()


# ---------------------------------------------------------------------------
# KV store: create / read / update / delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kv_set_then_get_round_trip(store):
    await store.kv_set("appA", "color", "blue")
    assert await store.kv_get("appA", "color") == "blue"


@pytest.mark.asyncio
async def test_kv_set_stores_nested_json(store):
    value = {"name": "todo", "items": [1, 2, {"nested": True}], "count": 3}
    await store.kv_set("appA", "config", value)
    assert await store.kv_get("appA", "config") == value


@pytest.mark.asyncio
async def test_kv_round_trips_various_json_types(store):
    cases = [
        ("s", "hello"),
        ("i", 42),
        ("f", 3.14),
        ("bt", True),
        ("bf", False),
        ("n", None),
        ("lst", [1, 2, 3]),
        ("nested", {"a": [1, {"b": 2}]}),
        ("empty_dict", {}),
        ("empty_list", []),
    ]
    for key, value in cases:
        await store.kv_set("appA", key, value)
        assert await store.kv_get("appA", key) == value
    assert set(await store.kv_keys("appA")) == {k for k, _ in cases}


@pytest.mark.asyncio
async def test_kv_set_overwrites_existing_value(store):
    await store.kv_set("appA", "k", {"v": 1})
    await store.kv_set("appA", "k", {"v": 2})
    assert await store.kv_get("appA", "k") == {"v": 2}
    assert await store.kv_keys("appA") == ["k"]


@pytest.mark.asyncio
async def test_kv_set_stores_none_as_distinct_from_unset(store):
    await store.kv_set("appA", "maybe", None)
    # None round-trips, and the row exists (key is present) even though the
    # decoded value is indistinguishable from an unset key.
    assert await store.kv_get("appA", "maybe") is None
    assert await store.kv_keys("appA") == ["maybe"]


@pytest.mark.asyncio
async def test_kv_get_missing_key_returns_none(store):
    assert await store.kv_get("appA", "missing") is None


@pytest.mark.asyncio
async def test_kv_delete_removes_value(store):
    await store.kv_set("appA", "k", "v")
    await store.kv_delete("appA", "k")
    assert await store.kv_get("appA", "k") is None
    assert await store.kv_keys("appA") == []


@pytest.mark.asyncio
async def test_kv_delete_missing_key_is_noop(store):
    await store.kv_set("appA", "keep", 1)
    await store.kv_delete("appA", "gone")
    assert await store.kv_get("appA", "keep") == 1
    assert await store.kv_keys("appA") == ["keep"]


@pytest.mark.asyncio
async def test_kv_keys_empty_for_new_app(store):
    assert await store.kv_keys("appA") == []


@pytest.mark.asyncio
async def test_kv_keys_returns_sorted(store):
    await store.kv_set("appA", "zebra", 1)
    await store.kv_set("appA", "apple", 2)
    await store.kv_set("appA", "mango", 3)
    assert await store.kv_keys("appA") == ["apple", "mango", "zebra"]


@pytest.mark.asyncio
async def test_kv_keys_reflects_deletes(store):
    await store.kv_set("appA", "a", 1)
    await store.kv_set("appA", "b", 2)
    await store.kv_set("appA", "c", 3)
    await store.kv_delete("appA", "b")
    assert await store.kv_keys("appA") == ["a", "c"]


@pytest.mark.asyncio
async def test_kv_scoping_isolation(store):
    await store.kv_set("appA", "shared", 1)
    await store.kv_set("appB", "shared", 2)
    assert await store.kv_get("appA", "shared") == 1
    assert await store.kv_get("appB", "shared") == 2
    assert await store.kv_keys("appA") == ["shared"]
    assert await store.kv_keys("appB") == ["shared"]
    # Deleting from appA must not touch appB's data.
    await store.kv_delete("appA", "shared")
    assert await store.kv_get("appA", "shared") is None
    assert await store.kv_get("appB", "shared") == 2


@pytest.mark.asyncio
async def test_kv_set_after_delete_recreates_value(store):
    await store.kv_set("appA", "k", "first")
    await store.kv_delete("appA", "k")
    await store.kv_set("appA", "k", "second")
    assert await store.kv_get("appA", "k") == "second"


# ---------------------------------------------------------------------------
# Table store: create / read / delete (no update method exists)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_table_insert_returns_positive_int_id(store):
    rid = await store.table_insert("appA", "todos", {"text": "buy milk"})
    assert isinstance(rid, int)
    assert rid > 0


@pytest.mark.asyncio
async def test_table_insert_and_query_round_trip(store):
    row = {"text": "buy milk", "done": False}
    rid = await store.table_insert("appA", "todos", row)
    results = await store.table_query("appA", "todos", None)
    assert len(results) == 1
    assert results[0]["id"] == rid
    assert results[0]["text"] == "buy milk"
    assert results[0]["done"] is False


@pytest.mark.asyncio
async def test_table_query_empty_table_returns_empty_list(store):
    assert await store.table_query("appA", "todos", None) == []


@pytest.mark.asyncio
async def test_table_query_nonexistent_table_returns_empty_list(store):
    assert await store.table_query("appA", "never_used", None) == []


@pytest.mark.asyncio
async def test_table_query_no_filter_returns_all_rows_in_order(store):
    r1 = await store.table_insert("appA", "todos", {"n": 1})
    r2 = await store.table_insert("appA", "todos", {"n": 2})
    r3 = await store.table_insert("appA", "todos", {"n": 3})
    rows = await store.table_query("appA", "todos", None)
    assert [r["id"] for r in rows] == [r1, r2, r3]
    assert [r["n"] for r in rows] == [1, 2, 3]


@pytest.mark.asyncio
async def test_table_query_empty_where_returns_all(store):
    await store.table_insert("appA", "t", {"x": 1})
    await store.table_insert("appA", "t", {"x": 2})
    assert len(await store.table_query("appA", "t", {})) == 2


@pytest.mark.asyncio
async def test_table_query_where_matches_subset(store):
    await store.table_insert("appA", "todos", {"text": "a", "done": False})
    await store.table_insert("appA", "todos", {"text": "b", "done": True})
    await store.table_insert("appA", "todos", {"text": "c", "done": False})
    done = await store.table_query("appA", "todos", {"done": True})
    assert len(done) == 1
    assert done[0]["text"] == "b"


@pytest.mark.asyncio
async def test_table_query_where_no_match_returns_empty(store):
    await store.table_insert("appA", "todos", {"text": "a", "done": False})
    assert await store.table_query("appA", "todos", {"done": True}) == []


@pytest.mark.asyncio
async def test_table_query_where_multiple_conditions(store):
    await store.table_insert("appA", "todos", {"a": 1, "b": 2, "c": 3})
    await store.table_insert("appA", "todos", {"a": 1, "b": 5, "c": 9})
    await store.table_insert("appA", "todos", {"a": 7, "b": 2, "c": 3})
    rows = await store.table_query("appA", "todos", {"a": 1, "b": 2})
    assert len(rows) == 1
    assert rows[0]["c"] == 3


@pytest.mark.asyncio
async def test_table_query_where_missing_key_excludes_row(store):
    await store.table_insert("appA", "t", {"x": 1})
    assert await store.table_query("appA", "t", {"y": 1}) == []


@pytest.mark.asyncio
async def test_table_delete_removes_row(store):
    rid = await store.table_insert("appA", "todos", {"text": "x"})
    await store.table_delete("appA", "todos", rid)
    assert await store.table_query("appA", "todos", None) == []


@pytest.mark.asyncio
async def test_table_delete_nonexistent_id_is_noop(store):
    rid = await store.table_insert("appA", "todos", {"text": "keep"})
    await store.table_delete("appA", "todos", 99999)
    rows = await store.table_query("appA", "todos", None)
    assert len(rows) == 1
    assert rows[0]["id"] == rid


@pytest.mark.asyncio
async def test_table_delete_only_affects_targeted_row(store):
    r1 = await store.table_insert("appA", "t", {"v": 1})
    r2 = await store.table_insert("appA", "t", {"v": 2})
    await store.table_delete("appA", "t", r1)
    rows = await store.table_query("appA", "t", None)
    assert len(rows) == 1
    assert rows[0]["id"] == r2
    assert rows[0]["v"] == 2


@pytest.mark.asyncio
async def test_table_insert_incrementing_ids(store):
    id1 = await store.table_insert("appA", "t", {"v": 1})
    id2 = await store.table_insert("appA", "t", {"v": 2})
    assert id2 == id1 + 1


@pytest.mark.asyncio
async def test_table_scoping_isolation(store):
    rid_a = await store.table_insert("appA", "todos", {"text": "from-a"})
    rid_b = await store.table_insert("appB", "todos", {"text": "from-b"})
    assert rid_a != rid_b
    a_rows = await store.table_query("appA", "todos", None)
    b_rows = await store.table_query("appB", "todos", None)
    assert len(a_rows) == 1 and a_rows[0]["text"] == "from-a"
    assert len(b_rows) == 1 and b_rows[0]["text"] == "from-b"
    # Deleting appA's row must not affect appB.
    await store.table_delete("appA", "todos", rid_a)
    assert await store.table_query("appA", "todos", None) == []
    assert len(await store.table_query("appB", "todos", None)) == 1


@pytest.mark.asyncio
async def test_table_scoping_by_table_name(store):
    await store.table_insert("appA", "todos", {"text": "task"})
    await store.table_insert("appA", "notes", {"text": "task"})
    todos = await store.table_query("appA", "todos", None)
    notes = await store.table_query("appA", "notes", None)
    assert len(todos) == 1 and todos[0]["text"] == "task"
    assert len(notes) == 1 and notes[0]["text"] == "task"


@pytest.mark.asyncio
async def test_table_insert_duplicate_rows_allowed(store):
    r1 = await store.table_insert("appA", "t", {"x": 1})
    r2 = await store.table_insert("appA", "t", {"x": 1})
    assert r1 != r2
    rows = await store.table_query("appA", "t", None)
    assert len(rows) == 2
    assert {r["x"] for r in rows} == {1}


@pytest.mark.asyncio
async def test_table_query_where_returns_merged_id_and_data(store):
    rid = await store.table_insert("appA", "todos", {"text": "a", "done": False})
    rows = await store.table_query("appA", "todos", {"done": False})
    assert rows == [{"id": rid, "text": "a", "done": False}]


@pytest.mark.asyncio
async def test_table_delete_does_not_reset_autoincrement(store):
    r1 = await store.table_insert("appA", "t", {"v": 1})
    await store.table_delete("appA", "t", r1)
    r2 = await store.table_insert("appA", "t", {"v": 2})
    assert r2 == r1 + 1


# ---------------------------------------------------------------------------
# Cross-cutting: KV and table stores live in separate tables
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kv_and_table_stores_are_independent(store):
    await store.kv_set("appA", "k", "v")
    await store.table_insert("appA", "t", {"x": 1})
    assert await store.kv_get("appA", "k") == "v"
    assert await store.kv_keys("appA") == ["k"]
    rows = await store.table_query("appA", "t", None)
    assert len(rows) == 1 and rows[0]["x"] == 1
