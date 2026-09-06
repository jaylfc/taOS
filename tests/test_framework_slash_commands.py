import pytest


@pytest.mark.asyncio
async def test_slash_commands_endpoint_returns_per_slug_manifest(client, app):
    app.state.config.agents.append({
        "name": "tom", "framework": "hermes", "host": "localhost", "color": "#fff",
    })
    r = await client.get("/api/frameworks/slash-commands")
    assert r.status_code == 200
    body = r.json()
    # Shape: {slug: [{name, description}, ...]}
    assert "tom" in body
    assert isinstance(body["tom"], list)
    assert body["tom"][0]["name"] in ("help", "clear", "model")


@pytest.mark.asyncio
async def test_slash_commands_endpoint_handles_unknown_framework(client, app):
    app.state.config.agents.append({
        "name": "mystery", "framework": "nonexistent-fw", "host": "localhost", "color": "#fff",
    })
    r = await client.get("/api/frameworks/slash-commands")
    assert r.status_code == 200
    body = r.json()
    assert body.get("mystery") == []


@pytest.mark.asyncio
async def test_agent_slash_commands_returns_commands_for_valid_agent(client, app):
    """New per-agent endpoint: known framework → returns its slash commands."""
    app.state.config.agents.append({
        "name": "tom", "framework": "hermes", "host": "localhost", "color": "#fff",
    })
    r = await client.get("/api/agents/tom/slash-commands")
    assert r.status_code == 200
    body = r.json()
    assert "tom" in body
    assert isinstance(body["tom"], list)
    assert len(body["tom"]) > 0
    assert body["tom"][0]["name"] in ("help", "clear", "model")


@pytest.mark.asyncio
async def test_agent_slash_commands_handles_unknown_framework(client, app):
    """Per-agent endpoint: unknown framework → empty command list."""
    app.state.config.agents.append({
        "name": "mystery", "framework": "nonexistent-fw", "host": "localhost", "color": "#fff",
    })
    r = await client.get("/api/agents/mystery/slash-commands")
    assert r.status_code == 200
    body = r.json()
    assert body.get("mystery") == []


@pytest.mark.asyncio
async def test_agent_slash_commands_404_for_unknown_agent(client, app):
    """Per-agent endpoint: non-existent agent → 404."""
    r = await client.get("/api/agents/no-such-agent/slash-commands")
    assert r.status_code == 404
    body = r.json()
    assert "error" in body
