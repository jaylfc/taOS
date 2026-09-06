import pytest
import pytest_asyncio

import tinyagentos.design_docs as design_docs_module
from tinyagentos.design_docs import _new_design_id, DesignStore


@pytest_asyncio.fixture
async def design_store(tmp_path):
    store = DesignStore(tmp_path / "design_docs.db")
    await store.init()
    yield store
    await store.close()


class TestNewDesignId:
    def test_format(self):
        design_id = _new_design_id()
        assert design_id.startswith("design-")
        suffix = design_id[7:]
        assert len(suffix) == 8
        alphabet = "abcdefghijklmnopqrstuvwxyz234567"
        for ch in suffix:
            assert ch in alphabet

    def test_uniqueness(self):
        ids = {_new_design_id() for _ in range(100)}
        assert len(ids) == 100


@pytest.mark.asyncio
async def test_create_happy_path(design_store):
    row = await design_store.create(name="My Design", content='{"artboard": {}, "elements": []}')
    assert row["name"] == "My Design"
    assert row["content"] == '{"artboard": {}, "elements": []}'
    assert row["id"].startswith("design-")
    assert row["created_at"] == row["updated_at"]


@pytest.mark.asyncio
async def test_list_excludes_content(design_store):
    await design_store.create(name="T", content='{"secret": true}')
    rows = await design_store.list()
    assert len(rows) == 1
    assert "content" not in rows[0]
    assert rows[0]["name"] == "T"


@pytest.mark.asyncio
async def test_get_returns_content(design_store):
    created = await design_store.create(name="T", content='{"a": 1}')
    fetched = await design_store.get(created["id"])
    assert fetched is not None
    assert fetched["content"] == '{"a": 1}'


@pytest.mark.asyncio
async def test_get_missing_returns_none(design_store):
    assert await design_store.get("design-noexist") is None


@pytest.mark.asyncio
async def test_update_changes_content_and_timestamp(design_store):
    created = await design_store.create(name="T", content="{}")
    updated = await design_store.update(
        created["id"], name="T2", content='{"elements": [1]}'
    )
    assert updated is not None
    assert updated["name"] == "T2"
    assert updated["content"] == '{"elements": [1]}'
    assert updated["updated_at"] >= created["updated_at"]


@pytest.mark.asyncio
async def test_delete_existing(design_store):
    created = await design_store.create(name="T", content="{}")
    assert await design_store.delete(created["id"]) is True
    assert await design_store.get(created["id"]) is None


@pytest.mark.asyncio
async def test_delete_nonexistent(design_store):
    assert await design_store.delete("design-noexist") is False


@pytest.mark.asyncio
async def test_create_retries_on_id_collision(design_store, monkeypatch):
    """A colliding id (e.g. two concurrent creates racing on the same
    generated suffix) must not surface as a 500 -- the INSERT/PRIMARY KEY
    is the source of truth and a fresh id is retried."""
    ids = iter(["design-dupeid1", "design-dupeid1", "design-freshid"])
    monkeypatch.setattr(design_docs_module, "_new_design_id", lambda: next(ids))

    first = await design_store.create(name="First", content="{}")
    assert first["id"] == "design-dupeid1"

    second = await design_store.create(name="Second", content="{}")
    assert second["id"] == "design-freshid"
    assert second["name"] == "Second"


@pytest.mark.asyncio
async def test_create_gives_up_after_bounded_retries(design_store, monkeypatch):
    monkeypatch.setattr(design_docs_module, "_new_design_id", lambda: "design-alwayssame")
    await design_store.create(name="First", content="{}")

    with pytest.raises(RuntimeError):
        await design_store.create(name="Second", content="{}")


@pytest.mark.asyncio
async def test_create_get_update_delete_roundtrip(design_store):
    created = await design_store.create(name="Start", content="orig")
    design_id = created["id"]

    fetched = await design_store.get(design_id)
    assert fetched["name"] == "Start"

    updated = await design_store.update(design_id, name="Edited", content="changed")
    assert updated["name"] == "Edited"
    assert updated["content"] == "changed"

    rows = await design_store.list()
    assert len(rows) == 1

    assert await design_store.delete(design_id) is True
    assert await design_store.get(design_id) is None
    assert await design_store.list() == []
