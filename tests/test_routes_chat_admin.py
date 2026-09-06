"""Tests for chat admin REST endpoints (PATCH channel, POST members, POST muted)."""
import pytest
import yaml

from httpx import AsyncClient, ASGITransport
from tinyagentos.app import create_app
from taos_test_csrf import csrf_event_hooks


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
        event_hooks=csrf_event_hooks(),
    )
    return app, client


async def _make_channel(app, **kwargs):
    defaults = dict(
        name="test", type="group", description="", topic="",
        members=["user", "tom"], settings={}, created_by="user",
    )
    defaults.update(kwargs)
    ch = await app.state.chat_channels.create_channel(**defaults)
    return ch["id"] if isinstance(ch, dict) else ch


# ── PATCH /api/chat/channels/{id} ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_patch_sets_response_mode(tmp_path):
    app, client = await _setup_client(tmp_path)
    async with client:
        ch_id = await _make_channel(app)
        r = await client.patch(
            f"/api/chat/channels/{ch_id}",
            json={"response_mode": "lively"},
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True
        ch = await app.state.chat_channels.get_channel(ch_id)
        assert ch["settings"]["response_mode"] == "lively"


@pytest.mark.asyncio
async def test_patch_invalid_response_mode_returns_400(tmp_path):
    app, client = await _setup_client(tmp_path)
    async with client:
        ch_id = await _make_channel(app)
        r = await client.patch(
            f"/api/chat/channels/{ch_id}",
            json={"response_mode": "blasting"},
        )
        assert r.status_code == 400
        assert "invalid response_mode" in r.json()["error"]


@pytest.mark.asyncio
async def test_patch_max_hops_out_of_range_returns_400(tmp_path):
    app, client = await _setup_client(tmp_path)
    async with client:
        ch_id = await _make_channel(app)
        r = await client.patch(
            f"/api/chat/channels/{ch_id}",
            json={"max_hops": 99},
        )
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_patch_topic_too_long_returns_400(tmp_path):
    app, client = await _setup_client(tmp_path)
    async with client:
        ch_id = await _make_channel(app)
        r = await client.patch(
            f"/api/chat/channels/{ch_id}",
            json={"topic": "x" * 501},
        )
        assert r.status_code == 400
        assert "topic" in r.json()["error"]


@pytest.mark.asyncio
async def test_patch_unknown_channel_returns_404(tmp_path):
    app, client = await _setup_client(tmp_path)
    async with client:
        r = await client.patch(
            "/api/chat/channels/doesnotexist",
            json={"response_mode": "quiet"},
        )
        assert r.status_code == 404


# ── POST /api/chat/channels/{id}/members ─────────────────────────────────────

@pytest.mark.asyncio
async def test_members_add_known_agent(tmp_path):
    app, client = await _setup_client(tmp_path)
    async with client:
        ch_id = await _make_channel(app, members=["user"])
        r = await client.post(
            f"/api/chat/channels/{ch_id}/members",
            json={"action": "add", "slug": "tom"},
        )
        assert r.status_code == 200
        ch = await app.state.chat_channels.get_channel(ch_id)
        assert "tom" in ch["members"]


@pytest.mark.asyncio
async def test_members_remove(tmp_path):
    app, client = await _setup_client(tmp_path)
    async with client:
        ch_id = await _make_channel(app, members=["user", "tom"])
        r = await client.post(
            f"/api/chat/channels/{ch_id}/members",
            json={"action": "remove", "slug": "tom"},
        )
        assert r.status_code == 200
        ch = await app.state.chat_channels.get_channel(ch_id)
        assert "tom" not in ch["members"]


@pytest.mark.asyncio
async def test_members_unknown_slug_returns_400(tmp_path):
    app, client = await _setup_client(tmp_path)
    async with client:
        ch_id = await _make_channel(app)
        r = await client.post(
            f"/api/chat/channels/{ch_id}/members",
            json={"action": "add", "slug": "nobody"},
        )
        assert r.status_code == 400
        assert "unknown agent" in r.json()["error"]


# ── POST /api/chat/channels/{id}/muted ───────────────────────────────────────

@pytest.mark.asyncio
async def test_muted_add_agent(tmp_path):
    app, client = await _setup_client(tmp_path)
    async with client:
        ch_id = await _make_channel(app)
        r = await client.post(
            f"/api/chat/channels/{ch_id}/muted",
            json={"action": "add", "slug": "tom"},
        )
        assert r.status_code == 200
        ch = await app.state.chat_channels.get_channel(ch_id)
        assert "tom" in ch["settings"]["muted"]
