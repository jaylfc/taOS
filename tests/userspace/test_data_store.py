import pytest
from tinyagentos.userspace.data_store import UserspaceDataStore


@pytest.mark.asyncio
async def test_kv_namespaced_per_app(tmp_path):
    s = UserspaceDataStore(tmp_path / "d.db"); await s.init()
    await s.kv_set("appA", "k", {"v": 1})
    await s.kv_set("appB", "k", {"v": 2})
    assert await s.kv_get("appA", "k") == {"v": 1}
    assert await s.kv_get("appB", "k") == {"v": 2}
    assert await s.kv_get("appA", "missing") is None
    assert await s.kv_keys("appA") == ["k"]
    await s.kv_delete("appA", "k")
    assert await s.kv_get("appA", "k") is None
    assert await s.kv_get("appB", "k") == {"v": 2}  # appB untouched
    await s.close()


@pytest.mark.asyncio
async def test_table_insert_query_delete_scoped(tmp_path):
    s = UserspaceDataStore(tmp_path / "d.db"); await s.init()
    rid = await s.table_insert("appA", "todos", {"text": "buy milk", "done": False})
    assert isinstance(rid, int)
    await s.table_insert("appB", "todos", {"text": "other"})
    rows = await s.table_query("appA", "todos", None)
    assert len(rows) == 1 and rows[0]["text"] == "buy milk" and rows[0]["id"] == rid
    filtered = await s.table_query("appA", "todos", {"done": False})
    assert len(filtered) == 1 and filtered[0]["text"] == "buy milk"
    assert await s.table_query("appA", "todos", {"done": True}) == []
    await s.table_delete("appA", "todos", rid)
    assert await s.table_query("appA", "todos", None) == []
    assert len(await s.table_query("appB", "todos", None)) == 1  # appB untouched
    await s.close()
