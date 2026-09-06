"""Unit tests for UserspaceAppStore CRUD and permission helpers."""
from __future__ import annotations

import pytest

from tinyagentos.userspace.store import UserspaceAppStore


async def _store(tmp_path) -> UserspaceAppStore:
    s = UserspaceAppStore(tmp_path / "userspace_apps.db")
    await s.init()
    return s


@pytest.mark.asyncio
async def test_get_returns_none_for_missing_app(tmp_path):
    store = await _store(tmp_path)
    try:
        assert await store.get("missing") is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_install_get_list_and_uninstall(tmp_path):
    store = await _store(tmp_path)
    try:
        await store.install(
            app_id="notes",
            name="Notes",
            version="2.0.0",
            app_type="web",
            entry="app.html",
            icon="notes.png",
            permissions_requested=["app.kv", "app.files"],
            trust="first-party",
        )
        row = await store.get("notes")
        assert row is not None
        assert row["app_id"] == "notes"
        assert row["name"] == "Notes"
        assert row["version"] == "2.0.0"
        assert row["app_type"] == "web"
        assert row["entry"] == "app.html"
        assert row["icon"] == "notes.png"
        assert row["permissions_requested"] == ["app.kv", "app.files"]
        assert row["permissions_granted"] == []
        assert row["enabled"] == 1
        assert row["trust"] == "first-party"
        assert isinstance(row["installed_at"], int)

        listed = await store.list_installed()
        assert len(listed) == 1
        assert listed[0]["app_id"] == "notes"

        assert await store.uninstall("notes") is True
        assert await store.get("notes") is None
        assert await store.list_installed() == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_uninstall_missing_returns_false(tmp_path):
    store = await _store(tmp_path)
    try:
        assert await store.uninstall("ghost") is False
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_install_upsert_updates_metadata(tmp_path):
    store = await _store(tmp_path)
    try:
        await store.install(
            app_id="calc",
            name="Calc",
            version="1.0.0",
            app_type="web",
            entry="index.html",
            icon="calc.png",
            permissions_requested=["app.net"],
        )
        await store.set_permissions_granted("calc", ["app.net"])
        await store.set_enabled("calc", False)

        await store.install(
            app_id="calc",
            name="Calculator",
            version="1.1.0",
            app_type="web",
            entry="main.html",
            icon="calc2.png",
            permissions_requested=["app.kv"],
            trust="community",
        )
        row = await store.get("calc")
        assert row["name"] == "Calculator"
        assert row["version"] == "1.1.0"
        assert row["entry"] == "main.html"
        assert row["icon"] == "calc2.png"
        assert row["permissions_requested"] == ["app.kv"]
        assert row["trust"] == "community"
        # upsert does not reset granted perms or enabled flag
        assert row["permissions_granted"] == ["app.net"]
        assert row["enabled"] == 0
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_set_permissions_granted_and_enabled_toggle(tmp_path):
    store = await _store(tmp_path)
    try:
        await store.install(
            app_id="mail",
            name="Mail",
            version="1",
            app_type="container",
            entry="index.html",
            icon="",
            permissions_requested=["app.net", "app.memory"],
        )
        await store.set_permissions_granted("mail", ["app.net"])
        await store.set_enabled("mail", False)
        disabled = await store.get("mail")
        assert disabled["permissions_granted"] == ["app.net"]
        assert disabled["enabled"] == 0

        await store.set_enabled("mail", True)
        enabled = await store.get("mail")
        assert enabled["enabled"] == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_list_installed_orders_by_installed_at(tmp_path):
    store = await _store(tmp_path)
    try:
        await store.install(
            app_id="first",
            name="First",
            version="1",
            app_type="web",
            entry="index.html",
            icon="",
            permissions_requested=[],
        )
        await store.install(
            app_id="second",
            name="Second",
            version="1",
            app_type="web",
            entry="index.html",
            icon="",
            permissions_requested=[],
        )
        ids = [row["app_id"] for row in await store.list_installed()]
        assert ids == ["first", "second"]
        assert (
            (await store.get("first"))["installed_at"]
            <= (await store.get("second"))["installed_at"]
        )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_set_runtime_location(tmp_path):
    store = await _store(tmp_path)
    try:
        await store.install(
            app_id="svc",
            name="Svc",
            version="1",
            app_type="container",
            entry="index.html",
            icon="",
            permissions_requested=[],
        )
        await store.set_runtime_location("svc", "10.0.0.5", 8080)
        row = await store.get("svc")
        assert row["container_host"] == "10.0.0.5"
        assert row["container_port"] == 8080
    finally:
        await store.close()