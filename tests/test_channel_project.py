"""Tests for channel project tagging: set_project and project_id filter."""
from __future__ import annotations

import pytest
import pytest_asyncio

from tinyagentos.chat.channel_store import ChatChannelStore


@pytest_asyncio.fixture
async def store(tmp_path):
    s = ChatChannelStore(tmp_path / "chat.db")
    await s.init()
    yield s
    await s.close()


async def _create(store, name="general", project_id="", **kw):
    return await store.create_channel(
        name=name, type="text", created_by="user1", project_id=project_id, **kw
    )


@pytest.mark.asyncio
async def test_set_project_updates_row(store):
    ch = await _create(store, name="ch1", project_id="")
    assert ch["project_id"] == ""

    await store.set_project(ch["id"], "prj-alpha")

    updated = await store.get_channel(ch["id"])
    assert updated["project_id"] == "prj-alpha"


@pytest.mark.asyncio
async def test_set_project_can_clear(store):
    ch = await _create(store, name="ch1", project_id="prj-beta")
    assert ch["project_id"] == "prj-beta"

    await store.set_project(ch["id"], "")

    updated = await store.get_channel(ch["id"])
    assert updated["project_id"] == ""


@pytest.mark.asyncio
async def test_list_channels_filtered_by_project(store):
    a = await _create(store, name="a", project_id="prj-1")
    b = await _create(store, name="b", project_id="prj-2")
    c = await _create(store, name="c", project_id="prj-1")

    result = await store.list_channels(project_id="prj-1")
    ids = [ch["id"] for ch in result]
    assert a["id"] in ids
    assert c["id"] in ids
    assert b["id"] not in ids


@pytest.mark.asyncio
async def test_list_channels_no_filter_returns_all(store):
    await _create(store, name="a", project_id="prj-1")
    await _create(store, name="b", project_id="prj-2")
    await _create(store, name="c", project_id="")

    result = await store.list_channels()
    assert len(result) == 3


@pytest.mark.asyncio
async def test_list_channels_empty_project_id_returns_all(store):
    await _create(store, name="a", project_id="prj-1")
    await _create(store, name="b", project_id="")

    result = await store.list_channels(project_id="")
    assert len(result) == 2
