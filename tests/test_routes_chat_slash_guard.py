"""Tests for the bare-slash guardrail in non-DM channels.

Covers both transports (HTTP and WS) — the safety must not be transport
dependent. See #268.
"""
import asyncio
import json

import pytest
import yaml

from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport
from tinyagentos.app import create_app
from tinyagentos.routes.chat import _validate_slash_target


def _make_app(tmp_path):
    cfg = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    (tmp_path / "config.yaml").write_text(yaml.dump(cfg))
    (tmp_path / ".setup_complete").touch()
    return create_app(data_dir=tmp_path)


async def _setup_client(tmp_path):
    app = _make_app(tmp_path)
    await app.state.chat_channels.init()
    await app.state.chat_messages.init()
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    rec = app.state.auth.find_user("admin")
    token = app.state.auth.create_session(user_id=rec["id"], long_lived=True)
    client = AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": token},
    )
    return app, client


@pytest.mark.asyncio
async def test_bare_slash_in_group_returns_400(tmp_path):
    # `/help` is intercepted in-app (taOS control command) so use a generic
    # slash command to exercise the guardrail path.
    app, client = await _setup_client(tmp_path)
    async with client:
        ch = await app.state.chat_channels.create_channel(
            name="g", type="group", description="", topic="",
            members=["user", "tom", "don"], settings={}, created_by="user",
        )
        ch_id = ch["id"] if isinstance(ch, dict) else ch
        r = await client.post(
            "/api/chat/messages",
            json={"channel_id": ch_id, "author_id": "user",
                  "author_type": "user", "content": "/clear",
                  "content_type": "text"},
        )
        assert r.status_code == 400
        assert "address an agent" in r.json()["error"]


@pytest.mark.asyncio
async def test_slash_with_mention_allowed(tmp_path):
    app, client = await _setup_client(tmp_path)
    async with client:
        ch = await app.state.chat_channels.create_channel(
            name="g", type="group", description="", topic="",
            members=["user", "tom"], settings={}, created_by="user",
        )
        ch_id = ch["id"] if isinstance(ch, dict) else ch
        r = await client.post(
            "/api/chat/messages",
            json={"channel_id": ch_id, "author_id": "user",
                  "author_type": "user", "content": "@tom /help",
                  "content_type": "text"},
        )
        assert r.status_code in (200, 201, 202)


@pytest.mark.asyncio
async def test_slash_with_at_all_allowed(tmp_path):
    app, client = await _setup_client(tmp_path)
    async with client:
        ch = await app.state.chat_channels.create_channel(
            name="g", type="group", description="", topic="",
            members=["user", "tom", "don"], settings={}, created_by="user",
        )
        ch_id = ch["id"] if isinstance(ch, dict) else ch
        r = await client.post(
            "/api/chat/messages",
            json={"channel_id": ch_id, "author_id": "user",
                  "author_type": "user", "content": "@all /help",
                  "content_type": "text"},
        )
        assert r.status_code in (200, 201, 202)


@pytest.mark.asyncio
async def test_slash_in_dm_allowed(tmp_path):
    app, client = await _setup_client(tmp_path)
    async with client:
        ch = await app.state.chat_channels.create_channel(
            name="dm", type="dm", description="", topic="",
            members=["user", "tom"], settings={}, created_by="user",
        )
        ch_id = ch["id"] if isinstance(ch, dict) else ch
        r = await client.post(
            "/api/chat/messages",
            json={"channel_id": ch_id, "author_id": "user",
                  "author_type": "user", "content": "/help",
                  "content_type": "text"},
        )
        assert r.status_code in (200, 201, 202)


@pytest.mark.asyncio
async def test_non_slash_in_group_allowed(tmp_path):
    app, client = await _setup_client(tmp_path)
    async with client:
        ch = await app.state.chat_channels.create_channel(
            name="g", type="group", description="", topic="",
            members=["user", "tom", "don"], settings={}, created_by="user",
        )
        ch_id = ch["id"] if isinstance(ch, dict) else ch
        r = await client.post(
            "/api/chat/messages",
            json={"channel_id": ch_id, "author_id": "user",
                  "author_type": "user", "content": "hello folks",
                  "content_type": "text"},
        )
        assert r.status_code in (200, 201, 202)


# ---------------------------------------------------------------------------
# Pure-helper unit tests — guards the behavior independent of routing wiring.
# ---------------------------------------------------------------------------


class TestValidateSlashTargetHelper:
    def test_non_slash_returns_none(self):
        assert _validate_slash_target("hello", {"type": "group"}) is None

    def test_no_channel_returns_none(self):
        # Defensive: missing channel record cannot block delivery.
        assert _validate_slash_target("/clear", None) is None

    def test_dm_channel_allows_slash(self):
        assert _validate_slash_target("/clear", {"type": "dm", "members": ["u", "v"]}) is None

    def test_a2a_group_allows_slash(self):
        ch = {"type": "group", "members": ["a"], "settings": {"kind": "a2a"}}
        assert _validate_slash_target("/clear", ch) is None

    def test_unaddressed_slash_in_group_returns_error(self):
        ch = {"type": "group", "members": ["user", "tom"], "settings": {}}
        msg = _validate_slash_target("/clear", ch)
        assert msg is not None
        assert "address an agent" in msg

    def test_at_mention_allows_slash(self):
        ch = {"type": "group", "members": ["user", "tom"], "settings": {}}
        assert _validate_slash_target("@tom /help", ch) is None

    def test_at_all_allows_slash(self):
        ch = {"type": "group", "members": ["user", "tom"], "settings": {}}
        assert _validate_slash_target("@all /help", ch) is None


# ---------------------------------------------------------------------------
# WS-path regression tests for #268 — guard must apply over the WebSocket too.
# ---------------------------------------------------------------------------


def _make_ws_app(tmp_path):
    cfg = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    (tmp_path / "config.yaml").write_text(yaml.dump(cfg))
    (tmp_path / ".setup_complete").touch()
    app = create_app(data_dir=tmp_path)
    asyncio.run(app.state.chat_channels.init())
    asyncio.run(app.state.chat_messages.init())
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    return app


def test_ws_unaddressed_slash_in_group_returns_error_frame(tmp_path):
    app = _make_ws_app(tmp_path)
    ch = asyncio.run(app.state.chat_channels.create_channel(
        name="g", type="group", description="", topic="",
        members=["user", "tom", "don"], settings={}, created_by="user",
    ))
    ch_id = ch["id"] if isinstance(ch, dict) else ch

    record = app.state.auth.find_user("admin")
    token = app.state.auth.create_session(user_id=record["id"], long_lived=True)

    with TestClient(app, raise_server_exceptions=False) as client:
        client.cookies.set("taos_session", token)
        with client.websocket_connect("/ws/chat") as ws:
            ws.send_text(json.dumps({
                "type": "message",
                "channel_id": ch_id,
                "content": "/clear",
            }))
            frame = json.loads(ws.receive_text())
            assert frame["type"] == "error"
            assert "address an agent" in frame["error"]


def test_ws_addressed_slash_in_group_is_delivered(tmp_path):
    """Sanity-check the happy WS path so the guard isn't blocking valid messages."""
    app = _make_ws_app(tmp_path)
    ch = asyncio.run(app.state.chat_channels.create_channel(
        name="g", type="group", description="", topic="",
        members=["user", "tom"], settings={}, created_by="user",
    ))
    ch_id = ch["id"] if isinstance(ch, dict) else ch

    record = app.state.auth.find_user("admin")
    token = app.state.auth.create_session(user_id=record["id"], long_lived=True)

    with TestClient(app, raise_server_exceptions=False) as client:
        client.cookies.set("taos_session", token)
        with client.websocket_connect("/ws/chat") as ws:
            ws.send_text(json.dumps({
                "type": "join", "channel_id": ch_id,
            }))
            ws.send_text(json.dumps({
                "type": "message",
                "channel_id": ch_id,
                "content": "@tom /help",
            }))
            # Expect a "message" broadcast frame, not an "error" one.
            frame = json.loads(ws.receive_text())
            assert frame["type"] != "error", f"Expected message frame, got {frame}"
