"""Tests for the notify_user agent tool."""
import types

import pytest

from tinyagentos.tools.notify_tools import execute_notify_user


class FakeNotifications:
    def __init__(self):
        self.added = []

    async def add(self, *, title, message, level="info", source="system"):
        self.added.append({"title": title, "message": message, "level": level, "source": source})


def _request(notifs):
    app = types.SimpleNamespace(state=types.SimpleNamespace(notifications=notifs))
    return types.SimpleNamespace(app=app, state=types.SimpleNamespace(user_id="user-1"))


@pytest.mark.asyncio
async def test_sends_notification():
    notifs = FakeNotifications()
    res = await execute_notify_user({"message": "Build finished"}, _request(notifs))
    assert res["ok"] is True
    assert len(notifs.added) == 1
    n = notifs.added[0]
    assert n["message"] == "Build finished"
    assert n["title"] == "Agent update"
    assert n["level"] == "info"
    assert n["source"] == "agent"


@pytest.mark.asyncio
async def test_custom_title_and_level():
    notifs = FakeNotifications()
    res = await execute_notify_user(
        {"message": "Disk almost full", "title": "Warning", "level": "warning"}, _request(notifs)
    )
    assert res["title"] == "Warning" and res["level"] == "warning"
    assert notifs.added[0]["level"] == "warning"


@pytest.mark.asyncio
async def test_invalid_level_falls_back_to_info():
    notifs = FakeNotifications()
    await execute_notify_user({"message": "x", "level": "critical"}, _request(notifs))
    assert notifs.added[0]["level"] == "info"


@pytest.mark.asyncio
async def test_requires_message():
    notifs = FakeNotifications()
    res = await execute_notify_user({"title": "no body"}, _request(notifs))
    assert "error" in res
    assert notifs.added == []


@pytest.mark.asyncio
async def test_blank_title_defaults():
    notifs = FakeNotifications()
    await execute_notify_user({"message": "hi", "title": "   "}, _request(notifs))
    assert notifs.added[0]["title"] == "Agent update"


@pytest.mark.asyncio
async def test_missing_notifications_store_errors():
    req = types.SimpleNamespace(
        app=types.SimpleNamespace(state=types.SimpleNamespace(notifications=None)),
        state=types.SimpleNamespace(user_id="user-1"),
    )
    res = await execute_notify_user({"message": "x"}, req)
    assert res["error"] == "notifications are not available"
