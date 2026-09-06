import pytest
import pytest_asyncio

from tinyagentos.office_docs import VALID_KINDS, _new_doc_id, OfficeDocStore


@pytest_asyncio.fixture
async def office_doc_store(tmp_path):
    store = OfficeDocStore(tmp_path / "office_docs.db")
    await store.init()
    yield store
    await store.close()


class TestNewDocId:
    def test_format(self):
        doc_id = _new_doc_id()
        assert doc_id.startswith("doc-")
        suffix = doc_id[4:]
        assert len(suffix) == 8
        alphabet = "abcdefghijklmnopqrstuvwxyz234567"
        for ch in suffix:
            assert ch in alphabet

    def test_uniqueness(self):
        ids = {_new_doc_id() for _ in range(100)}
        assert len(ids) == 100


class TestValidKinds:
    def test_expected_kinds(self):
        assert VALID_KINDS == {"write", "calc", "db", "slides"}


@pytest.mark.asyncio
async def test_create_happy_path(office_doc_store):
    row = await office_doc_store.create(kind="write", title="My Doc", content="Hello")
    assert row["kind"] == "write"
    assert row["title"] == "My Doc"
    assert row["content"] == "Hello"
    assert row["id"].startswith("doc-")
    assert row["created_at"] == row["updated_at"]
    assert isinstance(row["created_at"], int)


@pytest.mark.asyncio
async def test_create_all_kinds(office_doc_store):
    for kind in VALID_KINDS:
        row = await office_doc_store.create(kind=kind, title=kind, content="x")
        assert row["kind"] == kind


@pytest.mark.asyncio
async def test_create_invalid_kind(office_doc_store):
    with pytest.raises(ValueError, match="kind must be one of"):
        await office_doc_store.create(kind="invalid", title="T", content="C")


@pytest.mark.asyncio
async def test_get_existing(office_doc_store):
    created = await office_doc_store.create(kind="write", title="T", content="C")
    fetched = await office_doc_store.get(created["id"])
    assert fetched is not None
    assert fetched["id"] == created["id"]
    assert fetched["kind"] == "write"
    assert fetched["title"] == "T"
    assert fetched["content"] == "C"
    assert "created_at" in fetched
    assert "updated_at" in fetched


@pytest.mark.asyncio
async def test_get_nonexistent(office_doc_store):
    result = await office_doc_store.get("doc-nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_list_empty(office_doc_store):
    rows = await office_doc_store.list()
    assert rows == []


@pytest.mark.asyncio
async def test_list_returns_all(office_doc_store):
    await office_doc_store.create(kind="write", title="A", content="a")
    await office_doc_store.create(kind="calc", title="B", content="b")
    rows = await office_doc_store.list()
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_list_order_desc_updated_at(office_doc_store):
    import asyncio
    r1 = await office_doc_store.create(kind="write", title="First", content="1")
    await asyncio.sleep(1.1)
    r2 = await office_doc_store.create(kind="write", title="Second", content="2")
    rows = await office_doc_store.list()
    assert rows[0]["id"] == r2["id"]
    assert rows[1]["id"] == r1["id"]


@pytest.mark.asyncio
async def test_list_excludes_content(office_doc_store):
    await office_doc_store.create(kind="write", title="T", content="secret")
    rows = await office_doc_store.list()
    assert len(rows) == 1
    assert "content" not in rows[0]


@pytest.mark.asyncio
async def test_update_title_and_content(office_doc_store):
    created = await office_doc_store.create(kind="write", title="Old", content="old")
    updated = await office_doc_store.update(created["id"], title="New", content="new")
    assert updated is not None
    assert updated["title"] == "New"
    assert updated["content"] == "new"
    assert updated["kind"] == "write"
    assert updated["updated_at"] >= created["updated_at"]


@pytest.mark.asyncio
async def test_update_with_kind_change(office_doc_store):
    created = await office_doc_store.create(kind="write", title="T", content="C")
    updated = await office_doc_store.update(created["id"], title="T", content="C", kind="calc")
    assert updated["kind"] == "calc"


@pytest.mark.asyncio
async def test_update_invalid_kind(office_doc_store):
    created = await office_doc_store.create(kind="write", title="T", content="C")
    with pytest.raises(ValueError, match="kind must be one of"):
        await office_doc_store.update(created["id"], title="T", content="C", kind="bogus")


@pytest.mark.asyncio
async def test_update_nonexistent(office_doc_store):
    result = await office_doc_store.update("doc-noexist", title="X", content="Y")
    assert result is None


@pytest.mark.asyncio
async def test_delete_existing(office_doc_store):
    created = await office_doc_store.create(kind="write", title="T", content="C")
    deleted = await office_doc_store.delete(created["id"])
    assert deleted is True
    assert await office_doc_store.get(created["id"]) is None


@pytest.mark.asyncio
async def test_delete_nonexistent(office_doc_store):
    result = await office_doc_store.delete("doc-noexist")
    assert result is False


@pytest.mark.asyncio
async def test_create_get_update_delete_roundtrip(office_doc_store):
    created = await office_doc_store.create(kind="write", title="Start", content="orig")
    doc_id = created["id"]

    fetched = await office_doc_store.get(doc_id)
    assert fetched["title"] == "Start"

    updated = await office_doc_store.update(doc_id, title="Edited", content="changed", kind="calc")
    assert updated["title"] == "Edited"
    assert updated["kind"] == "calc"

    rows = await office_doc_store.list()
    assert len(rows) == 1

    assert await office_doc_store.delete(doc_id) is True
    assert await office_doc_store.get(doc_id) is None
    assert await office_doc_store.list() == []
