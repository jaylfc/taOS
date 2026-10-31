import re

import pytest

from tinyagentos.app_containers import AppContainersStore


@pytest.mark.asyncio
async def test_create_and_get_roundtrip(tmp_path):
    store = AppContainersStore(tmp_path / "app_containers.db")
    await store.init()
    try:
        created = await store.create(
            name="web",
            runtime="docker",
            node="local",
            base_image="alpine:3",
            limits={"memory": 256},
            sharing="shareable",
            created_by="install",
        )
        assert re.match(r"^ac-[a-z2-7]{6}$", created["id"])
        assert created["backend_ref"] == "taos-" + created["id"]
        assert created["limits"] == {"memory": 256}
        got = await store.get(created["id"])
        assert got is not None
        assert got["name"] == "web"
        assert got["runtime"] == "docker"
        assert got["limits"] == {"memory": 256}
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_create_validation_errors(tmp_path):
    store = AppContainersStore(tmp_path / "app_containers.db")
    await store.init()
    try:
        with pytest.raises(ValueError):
            await store.create(name="x", runtime="bad", created_by="install")
        with pytest.raises(ValueError):
            await store.create(name="x", runtime="docker", sharing="bad", created_by="install")
        with pytest.raises(ValueError):
            await store.create(name="x", runtime="docker", sharing="shareable", created_by="bad")
        with pytest.raises(ValueError):
            await store.create(name="", runtime="docker", created_by="install")
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_list_filters(tmp_path):
    store = AppContainersStore(tmp_path / "app_containers.db")
    await store.init()
    try:
        await store.create(name="a", runtime="docker", node="n1", created_by="install")
        await store.create(name="b", runtime="lxc", node="n1", created_by="install")
        await store.create(name="c", runtime="docker", node="n2", created_by="install")
        assert len(await store.list(node="n1")) == 2
        assert len(await store.list(runtime="docker")) == 2
        assert len(await store.list(node="n1", runtime="lxc")) == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_set_port_and_get(tmp_path):
    store = AppContainersStore(tmp_path / "app_containers.db")
    await store.init()
    try:
        c = await store.create(name="web", runtime="docker", created_by="install")
        res = await store.set_port(c["id"], 8080, 31234)
        assert res is not None
        got = await store.get(c["id"])
        assert got["ports"] == {"8080": 31234}
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_set_node(tmp_path):
    store = AppContainersStore(tmp_path / "app_containers.db")
    await store.init()
    try:
        c = await store.create(name="web", runtime="docker", node="local", created_by="install")
        res = await store.set_node(c["id"], "edge1")
        assert res is not None
        got = await store.get(c["id"])
        assert got["node"] == "edge1"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_try_lease_release(tmp_path):
    store = AppContainersStore(tmp_path / "app_containers.db")
    await store.init()
    try:
        c = await store.create(name="web", runtime="docker", created_by="install")
        assert await store.try_lease(c["id"]) is True
        assert await store.try_lease(c["id"]) is False
        await store.release_lease(c["id"])
        assert await store.try_lease(c["id"]) is True
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_delete(tmp_path):
    store = AppContainersStore(tmp_path / "app_containers.db")
    await store.init()
    try:
        c = await store.create(name="web", runtime="docker", created_by="install")
        assert await store.delete(c["id"]) is True
        assert await store.get(c["id"]) is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_setters_on_unknown_return_none(tmp_path):
    store = AppContainersStore(tmp_path / "app_containers.db")
    await store.init()
    try:
        assert await store.set_node("ac-nope", "x") is None
        assert await store.set_port("ac-nope", 80, 81) is None
        assert await store.set_origin_port("ac-nope", 80) is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_app_state_has_store(client):
    from tinyagentos.app_containers import AppContainersStore

    assert isinstance(client._transport.app.state.app_containers, AppContainersStore)
