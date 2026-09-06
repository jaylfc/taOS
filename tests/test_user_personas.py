import pytest
from tinyagentos.user_personas import UserPersonaStore

@pytest.fixture
def store(tmp_path):
    return UserPersonaStore(tmp_path / "personas.db")

def test_create_and_get(store):
    pid = store.create(name="My Persona", soul_md="SOUL", agent_md="AGENT", description="desc")
    row = store.get(pid)
    assert row["name"] == "My Persona"
    assert row["soul_md"] == "SOUL"
    assert row["agent_md"] == "AGENT"
    assert row["description"] == "desc"

def test_list_newest_first(store):
    a = store.create(name="A", soul_md="")
    b = store.create(name="B", soul_md="")
    rows = store.list()
    assert [r["id"] for r in rows] == [b, a]

def test_update(store):
    pid = store.create(name="X", soul_md="old")
    store.update(pid, soul_md="new")
    assert store.get(pid)["soul_md"] == "new"

def test_delete(store):
    pid = store.create(name="X", soul_md="")
    store.delete(pid)
    assert store.get(pid) is None

def test_created_at_is_utc_seconds(store):
    pid = store.create(name="X", soul_md="")
    ts = store.get(pid)["created_at"]
    assert isinstance(ts, int)
    import time; now = int(time.time())
    assert now - 5 < ts <= now
