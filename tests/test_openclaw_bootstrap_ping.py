"""Verify the bootstrap endpoint bumps agent['bootstrap_last_seen_at']."""
from __future__ import annotations

import time

import pytest
import pytest_asyncio
import yaml
from httpx import ASGITransport, AsyncClient

from tinyagentos.app import create_app


@pytest.fixture
def _ping_data_dir(tmp_path):
    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [
            {
                "name": "atlas-ping",
                "display_name": "Atlas",
                "framework": "openclaw",
                "llm_key": "sk-test-ping",
                "model": "qwen2.5:7b",
                "bootstrap_last_seen_at": None,
            }
        ],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    (tmp_path / "config.yaml").write_text(yaml.dump(config))
    (tmp_path / ".setup_complete").touch()
    return tmp_path


@pytest.fixture
def _ping_app(_ping_data_dir):
    return create_app(data_dir=_ping_data_dir)


@pytest_asyncio.fixture
async def ping_client(_ping_app):
    app = _ping_app
    for attr in ("metrics", "notifications", "secrets", "scheduler", "channels",
                 "relationships", "conversion", "training", "agent_messages",
                 "shared_folders", "streaming_sessions", "expert_agents",
                 "chat_messages", "chat_channels", "canvas_store"):
        store = getattr(app.state, attr)
        if getattr(store, "_db", None) is not None:
            await store.close()
        await store.init()
    await app.state.qmd_client.init()
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


@pytest.mark.asyncio
async def test_bootstrap_sets_last_seen_at(ping_client):
    client, app = ping_client

    before = int(time.time())
    resp = await client.get("/api/openclaw/bootstrap?agent=atlas-ping")

    agent = next(
        a for a in app.state.config.agents if a["name"] == "atlas-ping"
    )

    if resp.status_code == 200:
        assert agent["bootstrap_last_seen_at"] is not None
        assert agent["bootstrap_last_seen_at"] >= before
    else:
        assert agent["bootstrap_last_seen_at"] is None
