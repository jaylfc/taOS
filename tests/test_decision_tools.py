"""Tests for the request_decision agent tool (human-in-the-loop inbox)."""
import types

import pytest
import pytest_asyncio

from tinyagentos.decisions.decision_store import DecisionStore
from tinyagentos.tools.decision_tools import execute_request_decision, _normalize_options


@pytest_asyncio.fixture
async def app_request(tmp_path):
    store = DecisionStore(tmp_path / "decisions.db")
    await store.init()
    app = types.SimpleNamespace(state=types.SimpleNamespace(decision_store=store, notifications=None))
    req = types.SimpleNamespace(app=app, state=types.SimpleNamespace(user_id="user-1", is_admin=False))
    yield req, store
    await store.close()


@pytest.mark.asyncio
async def test_creates_pending_decision(app_request):
    req, store = app_request
    res = await execute_request_decision(
        {"question": "Pick a colour", "type": "single_select", "options": ["Red", "Blue"]}, req
    )
    assert res["ok"] is True
    did = res["decision_id"]
    assert res["status"] == "pending"
    saved = await store.get(did)
    assert saved["question"] == "Pick a colour"
    assert saved["type"] == "single_select"
    assert [o["label"] for o in saved["options"]] == ["Red", "Blue"]
    assert saved["user_id"] == "user-1"
    assert saved["from_agent"] == "@agent"


@pytest.mark.asyncio
async def test_from_agent_is_carried(app_request):
    req, store = app_request
    res = await execute_request_decision(
        {"question": "Ship it?", "type": "approve_deny", "from_agent": "@builder"}, req
    )
    saved = await store.get(res["decision_id"])
    assert saved["from_agent"] == "@builder"


@pytest.mark.asyncio
async def test_select_type_requires_options(app_request):
    req, _ = app_request
    res = await execute_request_decision({"question": "Which?", "type": "multi_select"}, req)
    assert "error" in res and "options" in res["error"]


@pytest.mark.asyncio
async def test_rejects_unknown_type(app_request):
    req, _ = app_request
    res = await execute_request_decision({"question": "Q", "type": "rank"}, req)
    assert "error" in res


@pytest.mark.asyncio
async def test_requires_question(app_request):
    req, _ = app_request
    res = await execute_request_decision({"type": "free_text"}, req)
    assert "error" in res


@pytest.mark.asyncio
async def test_free_text_needs_no_options(app_request):
    req, store = app_request
    res = await execute_request_decision({"question": "Name it", "type": "free_text"}, req)
    assert res["ok"] is True


@pytest.mark.asyncio
async def test_no_user_refuses(tmp_path):
    store = DecisionStore(tmp_path / "d.db")
    await store.init()
    app = types.SimpleNamespace(state=types.SimpleNamespace(decision_store=store, notifications=None))
    req = types.SimpleNamespace(app=app, state=types.SimpleNamespace(user_id=None))
    res = await execute_request_decision({"question": "Q", "type": "free_text"}, req)
    assert res["error"] == "no authenticated user to ask"
    await store.close()


def test_normalize_options_dedupes_colliding_labels():
    out = _normalize_options(["Other", "Other", "Other"])
    assert [o["value"] for o in out] == ["Other", "Other (2)", "Other (3)"]
    assert [o["label"] for o in out] == ["Other", "Other", "Other"]


def test_normalize_options_accepts_dicts():
    out = _normalize_options([{"label": "Yes", "value": "y"}, {"label": "No", "value": "n"}])
    assert out == [{"label": "Yes", "value": "y"}, {"label": "No", "value": "n"}]
