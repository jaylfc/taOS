"""Tests for the list_store_apps agent tool."""
import types

import pytest

from tinyagentos.tools.store_tools import execute_list_store_apps


class _App:
    def __init__(self, id, name, type="app", category="util", description=""):
        self.id = id
        self.name = name
        self.type = type
        self.category = category
        self.description = description


class _FakeRegistry:
    def __init__(self, apps, installed=()):
        self._apps = apps
        self._installed = set(installed)

    def list_available(self, type_filter=None):
        if type_filter:
            return [a for a in self._apps if a.type == type_filter]
        return list(self._apps)

    def is_installed(self, app_id):
        return app_id in self._installed


def _req(registry, installation=None):
    state = types.SimpleNamespace(registry=registry, installation_state=installation)
    return types.SimpleNamespace(app=types.SimpleNamespace(state=state), state=types.SimpleNamespace(user_id="user-1"))


@pytest.mark.asyncio
async def test_lists_all_with_installed_flag():
    reg = _FakeRegistry(
        [_App("comfyui", "ComfyUI", type="service", description="image backend"),
         _App("notes", "Notes", type="app")],
        installed=["comfyui"],
    )
    res = await execute_list_store_apps({}, _req(reg))
    assert res["ok"] is True and res["count"] == 2
    by_id = {a["id"]: a for a in res["apps"]}
    assert by_id["comfyui"]["installed"] is True
    assert by_id["notes"]["installed"] is False
    assert by_id["comfyui"]["type"] == "service"


@pytest.mark.asyncio
async def test_type_filter():
    reg = _FakeRegistry([_App("comfyui", "ComfyUI", type="service"), _App("notes", "Notes", type="app")])
    res = await execute_list_store_apps({"type": "service"}, _req(reg))
    assert res["count"] == 1 and res["apps"][0]["id"] == "comfyui"


@pytest.mark.asyncio
async def test_query_search():
    reg = _FakeRegistry([_App("comfyui", "ComfyUI", description="image backend"), _App("notes", "Notes", description="text")])
    res = await execute_list_store_apps({"query": "image"}, _req(reg))
    assert res["count"] == 1 and res["apps"][0]["id"] == "comfyui"


@pytest.mark.asyncio
async def test_installation_state_overrides_registry():
    class _Inst:
        def state(self, app_id):
            return "running" if app_id == "comfyui" else "not_installed"
    reg = _FakeRegistry([_App("comfyui", "ComfyUI"), _App("notes", "Notes")], installed=["notes"])
    res = await execute_list_store_apps({}, _req(reg, installation=_Inst()))
    by_id = {a["id"]: a for a in res["apps"]}
    # installation_state wins: comfyui running -> installed, notes not_installed -> not installed
    assert by_id["comfyui"]["installed"] is True
    assert by_id["notes"]["installed"] is False


@pytest.mark.asyncio
async def test_no_registry_errors():
    req = types.SimpleNamespace(
        app=types.SimpleNamespace(state=types.SimpleNamespace(registry=None)),
        state=types.SimpleNamespace(user_id="user-1"),
    )
    res = await execute_list_store_apps({}, req)
    assert res["error"] == "store registry not available"
