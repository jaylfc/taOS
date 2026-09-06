"""Tests for the todo agent tools (todo_list_lists, todo_add_item, todo_set_done)."""

from __future__ import annotations

import types
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from tinyagentos.todo.todo_store import TodoStore
from tinyagentos.tools.todo_tools import (
    execute_todo_add_item,
    execute_todo_list_lists,
    execute_todo_set_done,
)


# --------------------------------------------------------------------- helpers

def _make_request(store, config=None, msg_store=None, agent_registry=None, user_id=None):
    state = types.SimpleNamespace(
        todo_store=store,
        config=config,
        chat_messages=msg_store,
        agent_registry=agent_registry,
        user_id=user_id,
    )
    app = types.SimpleNamespace(state=state)
    req = types.SimpleNamespace(app=app, state=state)
    return req


@pytest_asyncio.fixture
async def store(tmp_path):
    s = TodoStore(tmp_path / "test_todo_tools.db")
    await s.init()
    yield s
    await s.close()


# ------------------------------------------------------------------ list tests

@pytest.mark.asyncio
async def test_list_returns_owned_lists(store):
    doc = await store.create_list("user-1", "Shopping")
    await store.create_list("user-2", "Other List")

    req = _make_request(store)
    res = await execute_todo_list_lists(
        {"agent_name": "atlas", "owner_user_id": "user-1"}, req
    )
    assert "lists" in res
    assert any(d["id"] == doc["id"] for d in res["lists"])
    assert len(res["lists"]) == 1
    # Internal fields must be stripped.
    for d in res["lists"]:
        assert "owner_user_id" not in d
        assert "archived_at" not in d
        assert "created_at" not in d


@pytest.mark.asyncio
async def test_list_excludes_other_users_lists(store):
    await store.create_list("user-2", "Private")

    req = _make_request(store)
    res = await execute_todo_list_lists(
        {"agent_name": "atlas", "owner_user_id": "user-1"}, req
    )
    assert res["lists"] == []


@pytest.mark.asyncio
async def test_list_excludes_archived_lists(store):
    doc = await store.create_list("user-1", "Old List")
    await store.archive_list(doc["id"])

    req = _make_request(store)
    res = await execute_todo_list_lists(
        {"agent_name": "atlas", "owner_user_id": "user-1"}, req
    )
    assert res["lists"] == []


@pytest.mark.asyncio
async def test_list_missing_agent_name_returns_error(store):
    req = _make_request(store)
    res = await execute_todo_list_lists({}, req)
    assert "error" in res
    assert "agent_name" in res["error"]


# ------------------------------------------------------------------- add tests

@pytest.mark.asyncio
async def test_owner_can_add_item(store):
    doc = await store.create_list("user-1", "Shopping")

    req = _make_request(store)
    res = await execute_todo_add_item(
        {"agent_name": "atlas", "list_id": doc["id"], "text": "Buy milk",
         "owner_user_id": "user-1"},
        req,
    )
    assert res.get("ok") is True
    assert "item_id" in res

    items = await store.list_items(doc["id"])
    assert any(i["text"] == "Buy milk" for i in items)


@pytest.mark.asyncio
async def test_non_owner_rejected(store):
    doc = await store.create_list("user-1", "Private")

    req = _make_request(store)
    res = await execute_todo_add_item(
        {"agent_name": "atlas", "list_id": doc["id"], "text": "Hacked",
         "owner_user_id": "user-2"},
        req,
    )
    assert "error" in res
    assert "access" in res["error"]

    items = await store.list_items(doc["id"])
    assert items == []


@pytest.mark.asyncio
async def test_add_item_attributed_to_owner(store):
    doc = await store.create_list("user-1", "Tasks")

    req = _make_request(store)
    res = await execute_todo_add_item(
        {"agent_name": "atlas", "list_id": doc["id"], "text": "Do the thing",
         "owner_user_id": "user-1"},
        req,
    )
    item = await store.get_item(res["item_id"])
    assert item["author"] == "user-1"


@pytest.mark.asyncio
async def test_add_item_notification_noop(store):
    """Notification module is a no-op for now; just verify the add succeeds."""
    doc = await store.create_list("user-1", "Ideas")

    req = _make_request(store)
    res = await execute_todo_add_item(
        {"agent_name": "atlas", "list_id": doc["id"], "text": "Interesting",
         "owner_user_id": "user-1"},
        req,
    )
    assert res.get("ok") is True


@pytest.mark.asyncio
async def test_add_item_missing_fields_returns_error(store):
    req = _make_request(store)

    res = await execute_todo_add_item(
        {"agent_name": "atlas", "list_id": "list-x", "owner_user_id": "user-1"}, req
    )
    assert "error" in res

    res = await execute_todo_add_item(
        {"agent_name": "atlas", "text": "hi", "owner_user_id": "user-1"}, req
    )
    assert "error" in res

    res = await execute_todo_add_item(
        {"list_id": "list-x", "text": "hi", "owner_user_id": "user-1"}, req
    )
    assert "error" in res

    res = await execute_todo_add_item(
        {"agent_name": "atlas", "list_id": "list-x", "text": "hi"}, req
    )
    assert "error" in res


@pytest.mark.asyncio
async def test_add_item_archived_list_rejected(store):
    doc = await store.create_list("user-1", "Old List")
    await store.archive_list(doc["id"])

    req = _make_request(store)
    res = await execute_todo_add_item(
        {"agent_name": "atlas", "list_id": doc["id"], "text": "late entry",
         "owner_user_id": "user-1"},
        req,
    )
    assert "error" in res
    assert "archived" in res["error"]


@pytest.mark.asyncio
async def test_add_item_nonexistent_list_returns_error(store):
    req = _make_request(store)
    res = await execute_todo_add_item(
        {"agent_name": "atlas", "list_id": "nonexistent", "text": "hi",
         "owner_user_id": "user-1"},
        req,
    )
    assert "error" in res
    assert "not found" in res["error"]


# ------------------------------------------------------------- set_done tests

@pytest.mark.asyncio
async def test_owner_can_mark_item_done(store):
    doc = await store.create_list("user-1", "Build List")
    item = await store.add_item(doc["id"], "Ship feature", author="user-1")

    req = _make_request(store)
    res = await execute_todo_set_done(
        {"agent_name": "atlas", "list_id": doc["id"], "item_id": item["id"],
         "done": True, "owner_user_id": "user-1"},
        req,
    )
    assert res.get("ok") is True
    assert res["done"] is True

    updated = await store.get_item(item["id"])
    assert updated["done"] is True

    # And it can be reopened.
    res = await execute_todo_set_done(
        {"agent_name": "atlas", "list_id": doc["id"], "item_id": item["id"],
         "done": False, "owner_user_id": "user-1"},
        req,
    )
    assert res.get("ok") is True
    updated = await store.get_item(item["id"])
    assert updated["done"] is False


@pytest.mark.asyncio
async def test_non_owner_cannot_mark_done(store):
    doc = await store.create_list("user-1", "Read Only")
    item = await store.add_item(doc["id"], "A task", author="user-1")

    req = _make_request(store)
    res = await execute_todo_set_done(
        {"agent_name": "atlas", "list_id": doc["id"], "item_id": item["id"],
         "done": True, "owner_user_id": "user-2"},
        req,
    )
    assert "error" in res
    assert "access" in res["error"]

    updated = await store.get_item(item["id"])
    assert updated["done"] is False


@pytest.mark.asyncio
async def test_set_done_rejects_item_from_another_list(store):
    doc_a = await store.create_list("user-1", "List A")
    doc_b = await store.create_list("user-1", "List B")
    foreign = await store.add_item(doc_b["id"], "Not yours", author="user-1")

    req = _make_request(store)
    res = await execute_todo_set_done(
        {"agent_name": "atlas", "list_id": doc_a["id"], "item_id": foreign["id"],
         "done": True, "owner_user_id": "user-1"},
        req,
    )
    assert "error" in res
    assert "not found" in res["error"]

    updated = await store.get_item(foreign["id"])
    assert updated["done"] is False


@pytest.mark.asyncio
async def test_set_done_archived_list_rejected(store):
    doc = await store.create_list("user-1", "Old List")
    item = await store.add_item(doc["id"], "A task", author="user-1")
    await store.archive_list(doc["id"])

    req = _make_request(store)
    res = await execute_todo_set_done(
        {"agent_name": "atlas", "list_id": doc["id"], "item_id": item["id"],
         "done": True, "owner_user_id": "user-1"},
        req,
    )
    assert "error" in res
    assert "archived" in res["error"]


@pytest.mark.asyncio
async def test_set_done_missing_or_bad_fields_returns_error(store):
    req = _make_request(store)

    # missing done
    res = await execute_todo_set_done(
        {"agent_name": "atlas", "list_id": "d", "item_id": "i",
         "owner_user_id": "user-1"}, req
    )
    assert "error" in res
    # non-boolean done
    res = await execute_todo_set_done(
        {"agent_name": "atlas", "list_id": "d", "item_id": "i",
         "done": "yes", "owner_user_id": "user-1"}, req
    )
    assert "error" in res
    # missing item_id
    res = await execute_todo_set_done(
        {"agent_name": "atlas", "list_id": "d", "done": True,
         "owner_user_id": "user-1"}, req
    )
    assert "error" in res
    # missing agent_name
    res = await execute_todo_set_done(
        {"list_id": "d", "item_id": "i", "done": True,
         "owner_user_id": "user-1"}, req
    )
    assert "error" in res
    assert "agent_name" in res["error"]


# ---------------------------------------------------- registry-enforced tests

@pytest.mark.asyncio
async def test_list_lists_binds_agent_to_owner_via_registry(store):
    """When agent_registry is present, owner_user_id is derived from the agent."""
    doc = await store.create_list("user-1", "Shopping")

    # Mock registry: atlas → user-1
    registry = MagicMock()
    registry.get_by_handle = AsyncMock(
        return_value={"user_id": "user-1", "handle": "atlas"}
    )

    req = _make_request(store, agent_registry=registry)
    res = await execute_todo_list_lists(
        {"agent_name": "atlas", "owner_user_id": "user-1"}, req
    )
    assert "lists" in res
    assert any(d["id"] == doc["id"] for d in res["lists"])
    registry.get_by_handle.assert_called_once_with("atlas")


@pytest.mark.asyncio
async def test_list_lists_rejects_agent_not_in_registry_no_user_id(store):
    """Agent not in registry and no user_id → error."""
    registry = MagicMock()
    registry.get_by_handle = AsyncMock(return_value=None)

    # No user_id on state
    req = _make_request(store, agent_registry=registry)
    res = await execute_todo_list_lists(
        {"agent_name": "unknown"}, req
    )
    assert "error" in res
    assert "not found" in res["error"]


@pytest.mark.asyncio
async def test_list_lists_deployed_agent_fallback(store):
    """Agent not in registry but authenticated → uses request.state.user_id."""
    doc = await store.create_list("user-1", "Deployed Agent's List")

    registry = MagicMock()
    registry.get_by_handle = AsyncMock(return_value=None)
    req = _make_request(store, agent_registry=registry, user_id="user-1")

    res = await execute_todo_list_lists(
        {"agent_name": "deployed-agent"}, req
    )
    assert "lists" in res
    assert any(d["id"] == doc["id"] for d in res["lists"])


@pytest.mark.asyncio
async def test_list_lists_rejects_deployed_agent_no_user_id(store):
    """Agent not in registry and no user_id → error."""
    registry = MagicMock()
    registry.get_by_handle = AsyncMock(return_value=None)

    # No user_id on state
    req = _make_request(store, agent_registry=registry)
    res = await execute_todo_list_lists(
        {"agent_name": "deployed-agent"}, req
    )
    assert "error" in res
    assert "not found" in res["error"]


@pytest.mark.asyncio
async def test_add_item_deployed_agent_fallback(store):
    """Agent not in registry but authenticated → uses request.state.user_id."""
    doc = await store.create_list("user-1", "Deployed Agent's List")

    registry = MagicMock()
    registry.get_by_handle = AsyncMock(return_value=None)

    req = _make_request(store, agent_registry=registry, user_id="user-1")

    res = await execute_todo_add_item(
        {"agent_name": "deployed-agent", "list_id": doc["id"], "text": "test item"},
        req,
    )
    assert res.get("ok") is True


@pytest.mark.asyncio
async def test_add_item_rejects_deployed_agent_no_user_id(store):
    """Agent not in registry and no user_id → error."""
    doc = await store.create_list("user-1", "Deployed Agent's List")

    registry = MagicMock()
    registry.get_by_handle = AsyncMock(return_value=None)

    # No user_id on state
    req = _make_request(store, agent_registry=registry)
    res = await execute_todo_add_item(
        {"agent_name": "deployed-agent", "list_id": doc["id"], "text": "test item"},
        req,
    )
    assert "error" in res
    assert "not found" in res["error"]


@pytest.mark.asyncio
async def test_set_done_deployed_agent_fallback(store):
    """Agent not in registry but authenticated → uses request.state.user_id."""
    doc = await store.create_list("user-1", "Tasks")
    item = await store.add_item(doc["id"], "A task", author="user-1")

    registry = MagicMock()
    registry.get_by_handle = AsyncMock(return_value=None)

    req = _make_request(store, agent_registry=registry, user_id="user-1")

    res = await execute_todo_set_done(
        {"agent_name": "deployed-agent", "list_id": doc["id"],
         "item_id": item["id"], "done": True},
        req,
    )
    assert res.get("ok") is True


@pytest.mark.asyncio
async def test_set_done_rejects_deployed_agent_no_user_id(store):
    """Agent not in registry and no user_id → error."""
    doc = await store.create_list("user-1", "Tasks")
    item = await store.add_item(doc["id"], "A task", author="user-1")

    registry = MagicMock()
    registry.get_by_handle = AsyncMock(return_value=None)

    # No user_id on state
    req = _make_request(store, agent_registry=registry)
    res = await execute_todo_set_done(
        {"agent_name": "deployed-agent", "list_id": doc["id"],
         "item_id": item["id"], "done": True},
        req,
    )
    assert "error" in res
    assert "not found" in res["error"]


@pytest.mark.asyncio
async def test_add_item_binds_agent_to_owner_via_registry(store):
    """Registry-resolved user_id is used for the access check, overriding args."""
    doc = await store.create_list("user-1", "Private")

    # Mock registry: atlas → user-1 (even though args claim user-2)
    registry = MagicMock()
    registry.get_by_handle = AsyncMock(
        return_value={"user_id": "user-1", "handle": "atlas"}
    )

    req = _make_request(store, agent_registry=registry)
    # Agent tries to claim user-2 but registry says they're user-1
    res = await execute_todo_add_item(
        {"agent_name": "atlas", "list_id": doc["id"], "text": "Should work",
         "owner_user_id": "user-2"},
        req,
    )
    # The registry overrides owner_user_id to user-1 → access granted
    assert res.get("ok") is True


@pytest.mark.asyncio
async def test_set_done_binds_agent_to_owner_via_registry(store):
    """Registry-resolved user_id gates set_done access."""
    doc = await store.create_list("user-1", "Tasks")
    item = await store.add_item(doc["id"], "A task", author="user-1")

    # Mock registry: atlas → user-2 (different owner → denied)
    registry = MagicMock()
    registry.get_by_handle = AsyncMock(
        return_value={"user_id": "user-2", "handle": "atlas"}
    )

    req = _make_request(store, agent_registry=registry)
    res = await execute_todo_set_done(
        {"agent_name": "atlas", "list_id": doc["id"], "item_id": item["id"],
         "done": True, "owner_user_id": "user-1"},
        req,
    )
    # Registry says atlas → user-2, but list owner is user-1 → denied
    assert "error" in res
    assert "access" in res["error"]


@pytest.mark.asyncio
async def test_resolve_falls_back_without_registry(store):
    """No agent_registry on state → falls back to args-supplied owner_user_id."""
    doc = await store.create_list("user-1", "Fallback")

    req = _make_request(store)  # no agent_registry
    res = await execute_todo_list_lists(
        {"agent_name": "atlas", "owner_user_id": "user-1"}, req
    )
    assert "lists" in res
    assert any(d["id"] == doc["id"] for d in res["lists"])


# ----------------------------------------- real AgentRegistryStore tests (F2)
# Pattern: test_registry_governance_lifecycle.py:35 — instantiate a real
# AgentRegistryStore, not a MagicMock, so the get_by_handle return-None
# production path is actually exercised.

@pytest.mark.asyncio
async def test_registry_hit_uses_store_user_id(store, tmp_path):
    """Real AgentRegistryStore: registered handle → use registry's user_id."""
    from tinyagentos.agent_registry_store import AgentRegistryStore

    doc = await store.create_list("user-1", "Registered Agent's List")

    reg = AgentRegistryStore(tmp_path / "reg.db")
    await reg.init()
    try:
        await reg.register(framework="test", handle="atlas", user_id="user-1")
        rec = await reg.get_by_handle("atlas")
        assert rec is not None
        assert rec["user_id"] == "user-1"

        req = _make_request(store, agent_registry=reg)
        res = await execute_todo_list_lists(
            {"agent_name": "atlas", "owner_user_id": "user-99"}, req
        )
        # Registry overrides caller-supplied owner_user_id → user-1 owns the list
        assert "lists" in res
        assert any(d["id"] == doc["id"] for d in res["lists"])
        # Internal fields must be stripped from the real-store path too.
        for d in res["lists"]:
            assert "owner_user_id" not in d
            assert "archived_at" not in d
            assert "created_at" not in d
    finally:
        await reg.close()


@pytest.mark.asyncio
async def test_registry_miss_falls_back_to_config(store, tmp_path):
    """Real AgentRegistryStore: handle not found → config fallback (deployed agent)."""
    from tinyagentos.agent_registry_store import AgentRegistryStore

    doc = await store.create_list("user-1", "Deployed Agent's List")

    reg = AgentRegistryStore(tmp_path / "reg2.db")
    await reg.init()
    try:
        # Store is empty — no rows at all, so get_by_handle returns None.
        rec = await reg.get_by_handle("deployed-agent")
        assert rec is None

        mock_config = MagicMock()
        mock_config.agents = [{"name": "deployed-agent"}]
        req = _make_request(
            store, config=mock_config, agent_registry=reg, user_id="user-1"
        )

        res = await execute_todo_list_lists(
            {"agent_name": "deployed-agent"}, req
        )
        assert "lists" in res
        assert any(d["id"] == doc["id"] for d in res["lists"])
    finally:
        await reg.close()


@pytest.mark.asyncio
async def test_registry_miss_no_config_errors(store, tmp_path):
    """Real AgentRegistryStore: handle not found, no config → error."""
    from tinyagentos.agent_registry_store import AgentRegistryStore

    reg = AgentRegistryStore(tmp_path / "reg3.db")
    await reg.init()
    try:
        rec = await reg.get_by_handle("unknown")
        assert rec is None

        # No config on state → fallback cannot fire
        req = _make_request(store, agent_registry=reg)
        res = await execute_todo_list_lists(
            {"agent_name": "unknown", "owner_user_id": "user-1"}, req
        )
        assert "error" in res
        assert "not found" in res["error"]
    finally:
        await reg.close()
