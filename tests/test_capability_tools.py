"""Tests for the get_capabilities agent tool."""
import types

import pytest

from tinyagentos.tools.capability_tools import execute_get_capabilities


class _FakeChecker:
    def get_all_capabilities(self):
        return {
            "chat-small": {"available": True, "hint": None},
            "chat-large": {"available": False, "hint": "Add a GPU worker with 8GB+ VRAM"},
            "embedding": {"available": True, "hint": None},
        }


def _req(checker):
    return types.SimpleNamespace(
        app=types.SimpleNamespace(state=types.SimpleNamespace(capabilities=checker)),
        state=types.SimpleNamespace(user_id="user-1"),
    )


@pytest.mark.asyncio
async def test_lists_all_with_hints():
    res = await execute_get_capabilities({}, _req(_FakeChecker()))
    assert res["ok"] is True
    assert res["count"] == 3 and res["available_count"] == 2
    by_name = {c["capability"]: c for c in res["capabilities"]}
    assert by_name["chat-small"]["available"] is True
    assert "unlock_hint" not in by_name["chat-small"]
    assert by_name["chat-large"]["available"] is False
    assert by_name["chat-large"]["unlock_hint"] == "Add a GPU worker with 8GB+ VRAM"


@pytest.mark.asyncio
async def test_available_only_filters():
    res = await execute_get_capabilities({"available_only": True}, _req(_FakeChecker()))
    assert res["count"] == 2
    assert all(c["available"] for c in res["capabilities"])
    assert {c["capability"] for c in res["capabilities"]} == {"chat-small", "embedding"}


@pytest.mark.asyncio
async def test_no_checker_errors():
    req = types.SimpleNamespace(
        app=types.SimpleNamespace(state=types.SimpleNamespace(capabilities=None)),
        state=types.SimpleNamespace(user_id="user-1"),
    )
    res = await execute_get_capabilities({}, req)
    assert res["error"] == "capability checker not available"


@pytest.mark.asyncio
async def test_results_sorted():
    res = await execute_get_capabilities({}, _req(_FakeChecker()))
    names = [c["capability"] for c in res["capabilities"]]
    assert names == sorted(names)
