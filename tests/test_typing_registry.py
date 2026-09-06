import pytest
from tinyagentos.chat.typing_registry import TypingRegistry


def test_empty_registry_returns_empty_lists():
    r = TypingRegistry()
    assert r.list("c1") == {"human": [], "agent": []}


def test_mark_human_appears_in_list():
    r = TypingRegistry()
    r.mark("c1", "jay", "human")
    assert [e["slug"] for e in r.list("c1")["human"]] == ["jay"]
    assert r.list("c1")["agent"] == []


def test_mark_agent_appears_in_list():
    r = TypingRegistry()
    r.mark("c1", "tom", "agent")
    assert [e["slug"] for e in r.list("c1")["agent"]] == ["tom"]


def test_clear_removes_entry():
    r = TypingRegistry()
    r.mark("c1", "tom", "agent")
    r.clear("c1", "tom")
    assert r.list("c1")["agent"] == []


def test_clear_idempotent():
    r = TypingRegistry()
    r.clear("c1", "nobody")  # must not raise


def test_different_channels_independent():
    r = TypingRegistry()
    r.mark("c1", "jay", "human")
    assert r.list("c2") == {"human": [], "agent": []}


def test_human_ttl_expires(monkeypatch):
    r = TypingRegistry(human_ttl=3, agent_ttl=45)
    t = [1000.0]
    monkeypatch.setattr("tinyagentos.chat.typing_registry._now", lambda: t[0])
    r.mark("c1", "jay", "human")
    assert [e["slug"] for e in r.list("c1")["human"]] == ["jay"]
    t[0] = 1003.1
    assert r.list("c1")["human"] == []


def test_agent_ttl_expires(monkeypatch):
    r = TypingRegistry(human_ttl=3, agent_ttl=45)
    t = [1000.0]
    monkeypatch.setattr("tinyagentos.chat.typing_registry._now", lambda: t[0])
    r.mark("c1", "tom", "agent")
    assert [e["slug"] for e in r.list("c1")["agent"]] == ["tom"]
    t[0] = 1045.1
    assert r.list("c1")["agent"] == []


def test_mark_refreshes_ttl(monkeypatch):
    r = TypingRegistry(human_ttl=3, agent_ttl=45)
    t = [1000.0]
    monkeypatch.setattr("tinyagentos.chat.typing_registry._now", lambda: t[0])
    r.mark("c1", "jay", "human")
    t[0] = 1002.0
    r.mark("c1", "jay", "human")  # refresh
    t[0] = 1004.0
    assert [e["slug"] for e in r.list("c1")["human"]] == ["jay"]  # still alive (refreshed at 1002)


# ── New phase/detail tests ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mark_with_phase_and_detail():
    reg = TypingRegistry()
    reg.mark("c1", "tom", "agent", phase="tool", detail="web_search")
    result = reg.list("c1")
    agents = result["agent"]
    assert len(agents) == 1
    entry = agents[0]
    assert entry["slug"] == "tom"
    assert entry["phase"] == "tool"
    assert entry["detail"] == "web_search"


@pytest.mark.asyncio
async def test_mark_without_phase_defaults_to_thinking():
    reg = TypingRegistry()
    reg.mark("c1", "tom", "agent")
    result = reg.list("c1")
    entry = result["agent"][0]
    assert entry["phase"] == "thinking"
    assert entry["detail"] is None


@pytest.mark.asyncio
async def test_mark_overwrites_phase_last_writer_wins():
    reg = TypingRegistry()
    reg.mark("c1", "tom", "agent", phase="thinking")
    reg.mark("c1", "tom", "agent", phase="tool", detail="search")
    entry = reg.list("c1")["agent"][0]
    assert entry["phase"] == "tool"
    assert entry["detail"] == "search"


@pytest.mark.asyncio
async def test_human_entry_shape_matches_agent():
    reg = TypingRegistry()
    reg.mark("c1", "jay", "human")
    entry = reg.list("c1")["human"][0]
    assert entry["slug"] == "jay"
    assert entry.get("phase") is None


# ── HTTP endpoint tests ────────────────────────────────────────────────────────

import pytest
import yaml
from httpx import AsyncClient, ASGITransport
from tinyagentos.app import create_app


def _make_app(tmp_path):
    cfg = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [{"name": "tom", "host": "localhost", "color": "#fff"}],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    (tmp_path / "config.yaml").write_text(yaml.dump(cfg))
    (tmp_path / ".setup_complete").touch()
    return create_app(data_dir=tmp_path)


async def _setup_client(tmp_path):
    from tinyagentos.chat.typing_registry import TypingRegistry
    app = _make_app(tmp_path)
    await app.state.chat_channels.init()
    await app.state.chat_messages.init()
    # typing is lifespan-owned; tests that don't run the lifespan must init it.
    app.state.typing = TypingRegistry()
    token = app.state.auth.get_local_token()
    client = AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )
    return app, client, token


@pytest.mark.asyncio
async def test_post_typing_marks_registry(tmp_path):
    app, client, _token = await _setup_client(tmp_path)
    async with client:
        store = app.state.chat_channels
        ch = await store.create_channel(
            name="g", type="group", description="", topic="",
            members=["user", "tom"], settings={}, created_by="user",
        )
        ch_id = ch["id"] if isinstance(ch, dict) else ch

        r = await client.post(
            f"/api/chat/channels/{ch_id}/typing",
            json={"author_id": "user"},
        )
        assert r.status_code == 200
        listing = app.state.typing.list(ch_id)
        assert "user" in [e["slug"] for e in listing["human"]]


@pytest.mark.asyncio
async def test_post_thinking_start_marks_registry(tmp_path):
    app, client, token = await _setup_client(tmp_path)
    async with client:
        store = app.state.chat_channels
        ch = await store.create_channel(
            name="g", type="group", description="", topic="",
            members=["user", "tom"], settings={}, created_by="user",
        )
        ch_id = ch["id"] if isinstance(ch, dict) else ch

        r = await client.post(
            f"/api/chat/channels/{ch_id}/thinking",
            json={"slug": "tom", "state": "start"},
        )
        assert r.status_code == 200
        listing = app.state.typing.list(ch_id)
        assert "tom" in [e["slug"] for e in listing["agent"]]

        # end clears
        r = await client.post(
            f"/api/chat/channels/{ch_id}/thinking",
            json={"slug": "tom", "state": "end"},
        )
        assert r.status_code == 200
        listing = app.state.typing.list(ch_id)
        assert "tom" not in [e["slug"] for e in listing["agent"]]


@pytest.mark.asyncio
async def test_post_thinking_requires_bearer(tmp_path):
    app = _make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/api/chat/channels/x/thinking",
            json={"slug": "tom", "state": "start"},
        )
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_get_typing_returns_current_state(tmp_path):
    app, client, _token = await _setup_client(tmp_path)
    async with client:
        store = app.state.chat_channels
        ch = await store.create_channel(
            name="g", type="group", description="", topic="",
            members=["user", "tom", "don"], settings={}, created_by="user",
        )
        ch_id = ch["id"] if isinstance(ch, dict) else ch
        app.state.typing.mark(ch_id, "user", "human")
        app.state.typing.mark(ch_id, "tom", "agent")

        r = await client.get(f"/api/chat/channels/{ch_id}/typing")
        assert r.status_code == 200
        body = r.json()
        assert [e["slug"] for e in body["human"]] == ["user"]
        assert [e["slug"] for e in body["agent"]] == ["tom"]
