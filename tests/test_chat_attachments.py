import json
import os
import pytest
from pathlib import Path
from httpx import AsyncClient, ASGITransport
from taos_test_csrf import csrf_event_hooks
from tinyagentos.chat.message_store import ChatMessageStore


@pytest.mark.asyncio
async def test_send_message_persists_attachments(tmp_path):
    store = ChatMessageStore(tmp_path / "msgs.db")
    await store.init()
    atts = [
        {"filename": "screenshot.png", "mime_type": "image/png",
         "size": 312456, "url": "/api/chat/files/abc-screenshot.png",
         "source": "disk"},
    ]
    msg = await store.send_message(
        channel_id="c1", author_id="user", author_type="user",
        content="look", content_type="text", state="complete",
        metadata=None, attachments=atts,
    )
    assert msg["attachments"] == atts


@pytest.mark.asyncio
async def test_send_message_defaults_attachments_to_empty_list(tmp_path):
    store = ChatMessageStore(tmp_path / "msgs.db")
    await store.init()
    msg = await store.send_message(
        channel_id="c1", author_id="user", author_type="user",
        content="plain", content_type="text", state="complete",
        metadata=None,
    )
    assert msg["attachments"] == []


@pytest.mark.asyncio
async def test_get_message_round_trips_attachments(tmp_path):
    store = ChatMessageStore(tmp_path / "msgs.db")
    await store.init()
    atts = [{"filename": "r.pdf", "mime_type": "application/pdf",
             "size": 500, "url": "/api/chat/files/r.pdf", "source": "workspace"}]
    msg = await store.send_message(
        channel_id="c1", author_id="user", author_type="user",
        content="see", content_type="text", state="complete",
        metadata=None, attachments=atts,
    )
    roundtripped = await store.get_message(msg["id"])
    assert roundtripped["attachments"] == atts


import yaml


def _make_from_path_app(tmp_path):
    cfg = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    (tmp_path / "config.yaml").write_text(yaml.dump(cfg))
    (tmp_path / ".setup_complete").touch()
    from tinyagentos.app import create_app
    return create_app(data_dir=tmp_path)


async def _authed_client(tmp_path):
    app = _make_from_path_app(tmp_path)
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    rec = app.state.auth.find_user("admin")
    token = app.state.auth.create_session(user_id=rec["id"], long_lived=True)
    client = AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": token},
        event_hooks=csrf_event_hooks(),
    )
    return app, client


@pytest.mark.asyncio
async def test_from_path_copies_workspace_file_and_returns_record(tmp_path):
    # seed a file in the user workspace
    ws = tmp_path / "agent-workspaces" / "user"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "report.md").write_text("# hi")

    app, client = await _authed_client(tmp_path)
    async with client:
        r = await client.post(
            "/api/chat/attachments/from-path",
            json={"path": "/workspaces/user/report.md", "source": "workspace"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["filename"] == "report.md"
        assert body["mime_type"] == "text/markdown"
        assert body["source"] == "workspace"
        assert body["url"].startswith("/api/chat/files/")
        # physical file exists
        stored_name = body["url"].rsplit("/", 1)[-1]
        assert (tmp_path / "chat-files" / stored_name).exists()


@pytest.mark.asyncio
async def test_from_path_rejects_traversal(tmp_path):
    app, client = await _authed_client(tmp_path)
    async with client:
        r = await client.post(
            "/api/chat/attachments/from-path",
            json={"path": "/workspaces/user/../../../etc/passwd", "source": "workspace"},
        )
        assert r.status_code in (400, 403)


from tinyagentos.app import create_app


async def _authed_msg_client(tmp_path):
    """Create an app + authenticated client with DB stores pre-initialized.
    ASGITransport in httpx 0.28 does not fire lifespan events, so we init
    the chat stores manually before returning."""
    app = _make_from_path_app(tmp_path)
    await app.state.chat_channels.init()
    await app.state.chat_messages.init()
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    rec = app.state.auth.find_user("admin")
    token = app.state.auth.create_session(user_id=rec["id"], long_lived=True)
    client = AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": token},
        event_hooks=csrf_event_hooks(),
    )
    return app, client


@pytest.mark.asyncio
async def test_send_message_with_attachments_persists(tmp_path, monkeypatch):
    monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
    # seed a file that /api/chat/files/ would serve
    (tmp_path / "chat-files").mkdir(parents=True, exist_ok=True)
    (tmp_path / "chat-files" / "abc-file.png").write_bytes(b"x")

    app, client = await _authed_msg_client(tmp_path)
    async with client:
        ch_r = await client.post(
            "/api/chat/channels",
            json={"name": "g", "type": "group", "members": ["user", "tom"],
                  "created_by": "user"},
        )
        assert ch_r.status_code in (200, 201), ch_r.json()
        ch_id = ch_r.json()["id"]
        r = await client.post(
            "/api/chat/messages",
            json={
                "channel_id": ch_id, "author_id": "user",
                "author_type": "user", "content": "here",
                "content_type": "text",
                "attachments": [
                    {"filename": "file.png", "mime_type": "image/png",
                     "size": 1, "url": "/api/chat/files/abc-file.png",
                     "source": "disk"},
                ],
            },
        )
        assert r.status_code in (200, 201)
        body = r.json()
        assert body["attachments"][0]["filename"] == "file.png"


@pytest.mark.asyncio
async def test_send_message_rejects_more_than_10_attachments(tmp_path, monkeypatch):
    monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
    (tmp_path / "chat-files").mkdir(parents=True, exist_ok=True)
    (tmp_path / "chat-files" / "f.png").write_bytes(b"x")
    app, client = await _authed_msg_client(tmp_path)
    async with client:
        ch_r = await client.post(
            "/api/chat/channels",
            json={"name": "g", "type": "group", "members": ["user"],
                  "created_by": "user"},
        )
        assert ch_r.status_code in (200, 201), ch_r.json()
        ch_id = ch_r.json()["id"]
        atts = [{"filename": "f.png", "mime_type": "image/png", "size": 1,
                 "url": "/api/chat/files/f.png", "source": "disk"}] * 11
        r = await client.post(
            "/api/chat/messages",
            json={"channel_id": ch_id, "author_id": "user",
                  "author_type": "user", "content": "overflow",
                  "content_type": "text", "attachments": atts},
        )
        assert r.status_code == 400
        assert "10" in r.json().get("error", "")


@pytest.mark.asyncio
async def test_send_message_rejects_bad_url_prefix(tmp_path, monkeypatch):
    monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
    app, client = await _authed_msg_client(tmp_path)
    async with client:
        ch_r = await client.post(
            "/api/chat/channels",
            json={"name": "g", "type": "group", "members": ["user"],
                  "created_by": "user"},
        )
        assert ch_r.status_code in (200, 201), ch_r.json()
        ch_id = ch_r.json()["id"]
        r = await client.post(
            "/api/chat/messages",
            json={"channel_id": ch_id, "author_id": "user",
                  "author_type": "user", "content": "bad",
                  "content_type": "text",
                  "attachments": [
                      {"filename": "f", "mime_type": "x", "size": 1,
                       "url": "https://evil.example/f", "source": "disk"}
                  ]},
        )
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_from_path_rejects_oversize(tmp_path):
    ws = tmp_path / "agent-workspaces" / "user"
    ws.mkdir(parents=True, exist_ok=True)
    big = ws / "big.bin"
    big.write_bytes(b"0" * (101 * 1024 * 1024))  # 101 MB

    app, client = await _authed_client(tmp_path)
    async with client:
        r = await client.post(
            "/api/chat/attachments/from-path",
            json={"path": "/workspaces/user/big.bin", "source": "workspace"},
        )
        assert r.status_code in (413, 400)
        assert "too large" in r.json().get("error", "").lower()
