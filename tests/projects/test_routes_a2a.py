from __future__ import annotations

import pytest


async def _list_channels(client, project_id: str) -> list[dict]:
    res = await client.get(f"/api/chat/channels?project_id={project_id}")
    assert res.status_code == 200
    return res.json().get("channels", [])


def _a2a(channels: list[dict]) -> dict | None:
    for c in channels:
        if (c.get("settings") or {}).get("kind") == "a2a":
            return c
    return None


async def _test_agent_id(client) -> tuple[str, str]:
    """Return (agent_id, agent_name) for the test-agent in config."""
    res = await client.get("/api/agents")
    assert res.status_code == 200
    data = res.json()
    agents = data if isinstance(data, list) else data.get("agents", [])
    ta = next(a for a in agents if a["name"] == "test-agent")
    return ta["id"], ta["name"]


@pytest.mark.asyncio
async def test_create_project_creates_a2a_channel(client):
    res = await client.post("/api/projects", json={"name": "P", "slug": "ra2a-1"})
    assert res.status_code == 200
    pid = res.json()["id"]

    channels = await _list_channels(client, pid)
    a2a = _a2a(channels)
    assert a2a is not None
    assert a2a["name"] == "a2a"
    assert a2a["type"] == "group"
    assert a2a["members"] == []


@pytest.mark.asyncio
async def test_add_member_adds_to_a2a_channel(client):
    agent_id, agent_name = await _test_agent_id(client)
    pid = (await client.post("/api/projects", json={"name": "P", "slug": "ra2a-2"})).json()["id"]

    res = await client.post(
        f"/api/projects/{pid}/members",
        json={"mode": "native", "agent_id": agent_id},
    )
    assert res.status_code == 200

    channels = await _list_channels(client, pid)
    a2a = _a2a(channels)
    assert a2a is not None
    # Channel members are agent names (not hex IDs)
    assert agent_name in a2a["members"]


@pytest.mark.asyncio
async def test_remove_member_removes_from_a2a_channel(client):
    agent_id, agent_name = await _test_agent_id(client)
    pid = (await client.post("/api/projects", json={"name": "P", "slug": "ra2a-3"})).json()["id"]
    await client.post(
        f"/api/projects/{pid}/members",
        json={"mode": "native", "agent_id": agent_id},
    )

    res = await client.delete(f"/api/projects/{pid}/members/{agent_id}")
    assert res.status_code == 200

    channels = await _list_channels(client, pid)
    a2a = _a2a(channels)
    assert a2a is not None
    assert agent_name not in a2a["members"]


@pytest.mark.asyncio
async def test_a2a_failure_does_not_break_project_create(client, monkeypatch, caplog):
    import tinyagentos.projects.a2a as a2a_mod

    async def boom(*args, **kwargs):
        raise RuntimeError("simulated a2a failure")

    monkeypatch.setattr(a2a_mod, "ensure_a2a_channel", boom)

    with caplog.at_level("WARNING"):
        res = await client.post("/api/projects", json={"name": "P", "slug": "ra2a-fail"})
    assert res.status_code == 200
    assert res.json()["slug"] == "ra2a-fail"
    assert any("a2a ensure failed" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_project_delete_archives_a2a_channel(client):
    pid = (await client.post("/api/projects", json={"name": "P", "slug": "ra2a-del"})).json()["id"]

    pre = _a2a(await _list_channels(client, pid))
    assert pre is not None and pre["settings"].get("archived") is not True

    res = await client.delete(f"/api/projects/{pid}")
    assert res.status_code == 200

    archived_res = await client.get(
        f"/api/chat/channels?archived=true&project_id={pid}"
    )
    archived = archived_res.json().get("channels", [])
    assert _a2a(archived) is not None
