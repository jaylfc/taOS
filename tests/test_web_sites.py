import pytest
import pytest_asyncio

import tinyagentos.web_sites as web_sites_module
from tinyagentos.web_sites import _new_site_id, WebSiteStore


@pytest_asyncio.fixture
async def web_site_store(tmp_path):
    store = WebSiteStore(tmp_path / "web_sites.db")
    await store.init()
    yield store
    await store.close()


class TestNewSiteId:
    def test_format(self):
        site_id = _new_site_id()
        assert site_id.startswith("site-")
        suffix = site_id[5:]
        assert len(suffix) == 8
        alphabet = "abcdefghijklmnopqrstuvwxyz234567"
        for ch in suffix:
            assert ch in alphabet

    def test_uniqueness(self):
        ids = {_new_site_id() for _ in range(100)}
        assert len(ids) == 100


@pytest.mark.asyncio
async def test_create_happy_path(web_site_store):
    row = await web_site_store.create(title="My Site", content='{"sections": []}')
    assert row["title"] == "My Site"
    assert row["content"] == '{"sections": []}'
    assert row["id"].startswith("site-")
    assert row["created_at"] == row["updated_at"]


@pytest.mark.asyncio
async def test_create_defaults_index_html_to_empty(web_site_store):
    row = await web_site_store.create(title="My Site", content="{}")
    assert row["index_html"] == ""


@pytest.mark.asyncio
async def test_create_stores_index_html(web_site_store):
    html = "<!doctype html><html><body>Hi</body></html>"
    row = await web_site_store.create(title="My Site", content="{}", index_html=html)
    assert row["index_html"] == html
    fetched = await web_site_store.get(row["id"])
    assert fetched is not None
    assert fetched["index_html"] == html


@pytest.mark.asyncio
async def test_list_excludes_content(web_site_store):
    await web_site_store.create(title="T", content='{"secret": true}')
    rows = await web_site_store.list()
    assert len(rows) == 1
    assert "content" not in rows[0]
    assert rows[0]["title"] == "T"


@pytest.mark.asyncio
async def test_get_returns_content(web_site_store):
    created = await web_site_store.create(title="T", content='{"a": 1}')
    fetched = await web_site_store.get(created["id"])
    assert fetched is not None
    assert fetched["content"] == '{"a": 1}'


@pytest.mark.asyncio
async def test_get_missing_returns_none(web_site_store):
    assert await web_site_store.get("site-noexist") is None


@pytest.mark.asyncio
async def test_update_changes_content_and_timestamp(web_site_store):
    created = await web_site_store.create(title="T", content="{}")
    updated = await web_site_store.update(
        created["id"], title="T2", content='{"sections": [1]}'
    )
    assert updated is not None
    assert updated["title"] == "T2"
    assert updated["content"] == '{"sections": [1]}'
    assert updated["updated_at"] >= created["updated_at"]


@pytest.mark.asyncio
async def test_update_changes_index_html(web_site_store):
    created = await web_site_store.create(title="T", content="{}", index_html="<p>v1</p>")
    updated = await web_site_store.update(created["id"], title="T", content="{}", index_html="<p>v2</p>")
    assert updated is not None
    assert updated["index_html"] == "<p>v2</p>"


@pytest.mark.asyncio
async def test_delete_existing(web_site_store):
    created = await web_site_store.create(title="T", content="{}")
    assert await web_site_store.delete(created["id"]) is True
    assert await web_site_store.get(created["id"]) is None


@pytest.mark.asyncio
async def test_delete_nonexistent(web_site_store):
    assert await web_site_store.delete("site-noexist") is False


@pytest.mark.asyncio
async def test_create_retries_on_id_collision(web_site_store, monkeypatch):
    """A colliding id (e.g. two concurrent creates racing on the same
    generated suffix) must not surface as a 500 -- the INSERT/PRIMARY KEY
    is the source of truth and a fresh id is retried."""
    ids = iter(["site-dupeid1", "site-dupeid1", "site-freshid"])
    monkeypatch.setattr(web_sites_module, "_new_site_id", lambda: next(ids))

    first = await web_site_store.create(title="First", content="{}")
    assert first["id"] == "site-dupeid1"

    second = await web_site_store.create(title="Second", content="{}")
    assert second["id"] == "site-freshid"
    assert second["title"] == "Second"


@pytest.mark.asyncio
async def test_create_gives_up_after_bounded_retries(web_site_store, monkeypatch):
    monkeypatch.setattr(web_sites_module, "_new_site_id", lambda: "site-alwayssame")
    await web_site_store.create(title="First", content="{}")

    with pytest.raises(RuntimeError):
        await web_site_store.create(title="Second", content="{}")
