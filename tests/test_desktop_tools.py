import asyncio
import types

import pytest

from tinyagentos.desktop_control.broker import DesktopCommandBroker
from tinyagentos.tools.desktop_tools import execute_open_app, execute_arrange_windows


def _fake_request(broker, user_id="user-1"):
    """Minimal stand-in for a FastAPI Request: .app.state + .state.user_id."""
    state = types.SimpleNamespace(desktop_command_broker=broker)
    app = types.SimpleNamespace(state=state)
    return types.SimpleNamespace(app=app, state=types.SimpleNamespace(user_id=user_id))


@pytest.mark.asyncio
async def test_open_app_emits_open_command():
    broker = DesktopCommandBroker()
    q = await broker.subscribe("user-1")
    req = _fake_request(broker)
    res = await execute_open_app({"app": "projects"}, req)
    assert res["ok"] and res["delivered"] == 1
    cmd = await asyncio.wait_for(q.get(), timeout=0.5)
    assert cmd.kind == "open-app" and cmd.payload["app"] == "projects"


@pytest.mark.asyncio
async def test_open_app_passes_props():
    broker = DesktopCommandBroker()
    q = await broker.subscribe("user-1")
    req = _fake_request(broker)
    await execute_open_app({"app": "messages", "props": {"channel": "general"}}, req)
    cmd = await asyncio.wait_for(q.get(), timeout=0.5)
    assert cmd.payload["props"] == {"channel": "general"}


@pytest.mark.asyncio
async def test_open_app_requires_app():
    broker = DesktopCommandBroker()
    res = await execute_open_app({}, _fake_request(broker))
    assert "error" in res


@pytest.mark.asyncio
async def test_arrange_windows_emits_window_command():
    broker = DesktopCommandBroker()
    q = await broker.subscribe("user-1")
    req = _fake_request(broker)
    res = await execute_arrange_windows({"preset": "tile-3"}, req)
    assert res["ok"]
    cmd = await asyncio.wait_for(q.get(), timeout=0.5)
    assert cmd.kind == "window" and cmd.payload == {"action": "arrange", "preset": "tile-3"}


@pytest.mark.asyncio
async def test_arrange_windows_rejects_bad_preset():
    broker = DesktopCommandBroker()
    res = await execute_arrange_windows({"preset": "spiral"}, _fake_request(broker))
    assert "error" in res


@pytest.mark.asyncio
async def test_scoped_to_caller_user():
    """A command only reaches the calling user's desktop, never another user's."""
    broker = DesktopCommandBroker()
    other = await broker.subscribe("user-2")
    await execute_open_app({"app": "store"}, _fake_request(broker, user_id="user-1"))
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(other.get(), timeout=0.1)
