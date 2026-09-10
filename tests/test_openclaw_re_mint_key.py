"""Red-first test: deployed agent with llm_key=None must get a key on bootstrap."""
from __future__ import annotations

import asyncio
import yaml
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.app import create_app
from tinyagentos.bridge_session import BridgeSessionRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def re_mint_data_dir(tmp_path):
    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [
            {"name": "test-backend", "type": "rkllama", "url": "http://localhost:8080", "priority": 1}
        ],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [
            {
                "name": "mybot",
                "host": "192.168.1.10",
                "color": "#aabbcc",
                "model": "qwen2.5:7b",
                "fallback_models": ["kilo-auto/free", "gpt-4o"],
                "chat_channel_id": "ch_mybot",
            }
        ],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    (tmp_path / "config.yaml").write_text(yaml.dump(config))
    (tmp_path / ".setup_complete").touch()
    return tmp_path


@pytest.fixture
def re_mint_app(re_mint_data_dir):
    return create_app(data_dir=re_mint_data_dir)


@pytest_asyncio.fixture
async def re_mint_client(re_mint_app):
    app = re_mint_app
    for attr in ("metrics", "notifications", "secrets", "scheduler", "channels",
                 "relationships", "conversion", "training", "agent_messages",
                 "shared_folders", "streaming_sessions", "expert_agents",
                 "chat_messages", "chat_channels", "canvas_store"):
        store = getattr(app.state, attr)
        if getattr(store, "_db", None) is not None:
            await store.close()
        await store.init()
    await app.state.qmd_client.init()
    app.state.bridge_sessions = BridgeSessionRegistry(
        chat_messages=app.state.chat_messages,
        chat_channels=app.state.chat_channels,
        chat_hub=app.state.chat_hub,
    )
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    token = app.state.auth.get_local_token()
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as c:
        yield c, app
    for attr in ("canvas_store", "chat_channels", "chat_messages", "expert_agents",
                 "streaming_sessions", "shared_folders", "agent_messages",
                 "conversion", "training", "relationships", "channels",
                 "scheduler", "secrets", "notifications", "metrics"):
        store = getattr(app.state, attr)
        try:
            await store.close()
        except Exception:
            pass
    try:
        await app.state.qmd_client.close()
    except Exception:
        pass
    try:
        await app.state.http_client.aclose()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bootstrap_re_mints_missing_llm_key(re_mint_client):
    """An agent with llm_key=None must get a key minted on bootstrap."""
    client, app = re_mint_client
    for a in app.state.config.agents:
        if a.get("name") == "mybot":
            a.pop("llm_key", None)

    mock_proxy = MagicMock()
    mock_proxy.is_running.return_value = True
    mock_key = "sk-taos-re-minted-test"
    mock_proxy.create_agent_key = AsyncMock(return_value=mock_key)
    app.state.llm_proxy = mock_proxy

    resp1 = await client.get("/api/openclaw/bootstrap?agent=mybot")
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["agent_name"] == "mybot"
    assert app.state.config.agents[0].get("llm_key") == mock_key

    resp2 = await client.get("/api/openclaw/bootstrap?agent=mybot")
    assert resp2.status_code == 200
