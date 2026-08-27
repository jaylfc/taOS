import pytest
import yaml
from httpx import AsyncClient, ASGITransport
from taos_test_csrf import csrf_event_hooks
from tinyagentos.chat.help import handle_help, KNOWN_TOPICS


def _make_help_app(tmp_path):
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


async def _authed_help_client(tmp_path):
    app = _make_help_app(tmp_path)
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


def test_overview_on_empty_args():
    out = handle_help("")
    assert "chat-guide" in out.lower()
    # lists known topics
    for t in ["threads", "attachments", "mentions"]:
        assert t in out


def test_specific_topic_returns_section():
    out = handle_help("threads")
    assert "thread" in out.lower()
    assert "chat-guide" in out.lower()  # link to full guide


def test_unknown_topic_returns_generic_message():
    out = handle_help("unknownthing")
    assert "unknown" in out.lower() or "try /help" in out.lower()


def test_all_documented_topics_have_handlers():
    for t in KNOWN_TOPICS:
        out = handle_help(t)
        assert len(out) > 0
        assert "error" not in out.lower()


@pytest.mark.asyncio
async def test_help_message_intercepted_posts_system_reply(tmp_path):
    app, client = await _authed_help_client(tmp_path)
    async with client:
        ch_r = await client.post(
            "/api/chat/channels",
            json={"name": "g", "type": "group", "description": "", "topic": "",
                  "members": ["user", "tom"], "created_by": "user"},
        )
        assert ch_r.status_code in (200, 201), ch_r.json()
        ch_id = ch_r.json()["id"]
        r = await client.post(
            "/api/chat/messages",
            json={"channel_id": ch_id, "author_id": "user",
                  "author_type": "user", "content": "/help",
                  "content_type": "text"},
        )
        assert r.status_code in (200, 201), r.json()
        body = r.json()
        assert body.get("handled") == "help"
        msgs = await app.state.chat_messages.get_messages(channel_id=ch_id, limit=5)
        sys_msgs = [m for m in msgs if m.get("author_type") == "system"]
        assert len(sys_msgs) == 1
        assert "chat-guide" in sys_msgs[0]["content"].lower()


@pytest.mark.asyncio
async def test_help_bypasses_bare_slash_guardrail(tmp_path):
    app, client = await _authed_help_client(tmp_path)
    async with client:
        ch_r = await client.post(
            "/api/chat/channels",
            json={"name": "g", "type": "group", "description": "", "topic": "",
                  "members": ["user", "tom", "don"], "created_by": "user"},
        )
        assert ch_r.status_code in (200, 201), ch_r.json()
        ch_id = ch_r.json()["id"]
        r = await client.post(
            "/api/chat/messages",
            json={"channel_id": ch_id, "author_id": "user",
                  "author_type": "user", "content": "/help threads",
                  "content_type": "text"},
        )
        assert r.status_code in (200, 201), r.json()
        assert r.json().get("handled") == "help"
