import pytest
import pytest_asyncio

from tinyagentos.music_songs import _new_song_id, SongStore


@pytest_asyncio.fixture
async def song_store(tmp_path):
    store = SongStore(tmp_path / "songs.db")
    await store.init()
    yield store
    await store.close()


class TestNewSongId:
    def test_format(self):
        song_id = _new_song_id()
        assert song_id.startswith("song-")
        suffix = song_id[5:]
        assert len(suffix) == 8
        alphabet = "abcdefghijklmnopqrstuvwxyz234567"
        for ch in suffix:
            assert ch in alphabet

    def test_uniqueness(self):
        ids = {_new_song_id() for _ in range(100)}
        assert len(ids) == 100


@pytest.mark.asyncio
async def test_create_happy_path(song_store):
    row = await song_store.create(name="My Song", content='{"tempo":92}')
    assert row["name"] == "My Song"
    assert row["content"] == '{"tempo":92}'
    assert row["id"].startswith("song-")
    assert row["created_at"] == row["updated_at"]
    assert isinstance(row["created_at"], int)


@pytest.mark.asyncio
async def test_get_existing(song_store):
    created = await song_store.create(name="T", content="C")
    fetched = await song_store.get(created["id"])
    assert fetched is not None
    assert fetched["id"] == created["id"]
    assert fetched["name"] == "T"
    assert fetched["content"] == "C"
    assert "created_at" in fetched
    assert "updated_at" in fetched


@pytest.mark.asyncio
async def test_get_nonexistent(song_store):
    result = await song_store.get("song-nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_list_empty(song_store):
    rows = await song_store.list()
    assert rows == []


@pytest.mark.asyncio
async def test_list_returns_all(song_store):
    await song_store.create(name="A", content="a")
    await song_store.create(name="B", content="b")
    rows = await song_store.list()
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_list_order_desc_updated_at(song_store):
    import asyncio

    r1 = await song_store.create(name="First", content="1")
    await asyncio.sleep(1.1)
    r2 = await song_store.create(name="Second", content="2")
    rows = await song_store.list()
    assert rows[0]["id"] == r2["id"]
    assert rows[1]["id"] == r1["id"]


@pytest.mark.asyncio
async def test_list_excludes_content(song_store):
    await song_store.create(name="T", content="secret")
    rows = await song_store.list()
    assert len(rows) == 1
    assert "content" not in rows[0]


@pytest.mark.asyncio
async def test_update_name_and_content(song_store):
    created = await song_store.create(name="Old", content="old")
    updated = await song_store.update(created["id"], name="New", content="new")
    assert updated is not None
    assert updated["name"] == "New"
    assert updated["content"] == "new"
    assert updated["updated_at"] >= created["updated_at"]


@pytest.mark.asyncio
async def test_update_nonexistent(song_store):
    result = await song_store.update("song-noexist", name="X", content="Y")
    assert result is None


@pytest.mark.asyncio
async def test_delete_existing(song_store):
    created = await song_store.create(name="T", content="C")
    deleted = await song_store.delete(created["id"])
    assert deleted is True
    assert await song_store.get(created["id"]) is None


@pytest.mark.asyncio
async def test_delete_nonexistent(song_store):
    result = await song_store.delete("song-noexist")
    assert result is False


@pytest.mark.asyncio
async def test_create_get_update_delete_roundtrip(song_store):
    created = await song_store.create(name="Start", content="orig")
    song_id = created["id"]

    fetched = await song_store.get(song_id)
    assert fetched["name"] == "Start"

    updated = await song_store.update(song_id, name="Edited", content="changed")
    assert updated["name"] == "Edited"

    rows = await song_store.list()
    assert len(rows) == 1

    assert await song_store.delete(song_id) is True
    assert await song_store.get(song_id) is None
    assert await song_store.list() == []
