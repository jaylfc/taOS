import pytest
import yaml
from httpx import AsyncClient, ASGITransport


def _make_phase_app(tmp_path):
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


async def _client_with_bearer(tmp_path):
    from tinyagentos.chat.typing_registry import TypingRegistry
    app = _make_phase_app(tmp_path)
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
    return app, client


@pytest.mark.asyncio
async def test_thinking_with_valid_phase_200(tmp_path):
    app, client = await _client_with_bearer(tmp_path)
    async with client:
        r = await client.post(
            "/api/chat/channels/c1/thinking",
            json={"slug": "tom", "state": "start", "phase": "tool", "detail": "web_search"},
        )
        assert r.status_code == 200, r.json()
        listing = app.state.typing.list("c1")
        assert listing["agent"][0]["phase"] == "tool"
        assert listing["agent"][0]["detail"] == "web_search"


@pytest.mark.asyncio
async def test_thinking_with_invalid_phase_400(tmp_path):
    app, client = await _client_with_bearer(tmp_path)
    async with client:
        r = await client.post(
            "/api/chat/channels/c1/thinking",
            json={"slug": "tom", "state": "start", "phase": "not-a-phase"},
        )
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_thinking_without_phase_defaults_thinking(tmp_path):
    app, client = await _client_with_bearer(tmp_path)
    async with client:
        r = await client.post(
            "/api/chat/channels/c1/thinking",
            json={"slug": "tom", "state": "start"},
        )
        assert r.status_code == 200
        listing = app.state.typing.list("c1")
        assert listing["agent"][0]["phase"] == "thinking"
