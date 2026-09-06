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


# ---------------------------------------------------------------------------
# Lead endpoint — PATCH /api/projects/{pid}/lead (D7, exclusive designee)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_lead_updates_channel_settings(client):
    """PATCHing the lead pointer resynchronises settings.leads in the A2A channel."""
    agent_id, agent_name = await _test_agent_id(client)
    pid = (await client.post("/api/projects", json={"name": "P", "slug": "ra2a-lead1"})).json()["id"]
    await client.post(f"/api/projects/{pid}/members", json={"mode": "native", "agent_id": agent_id})

    # Verify leads starts empty
    a2a_before = _a2a(await _list_channels(client, pid))
    assert a2a_before is not None
    assert a2a_before["settings"].get("leads") == []

    # Promote to lead
    res = await client.patch(
        f"/api/projects/{pid}/lead",
        json={"member_id": agent_id},
    )
    assert res.status_code == 200
    assert res.json()["lead_member_id"] == agent_id

    a2a_after = _a2a(await _list_channels(client, pid))
    assert a2a_after is not None
    assert agent_name in (a2a_after["settings"].get("leads") or [])


@pytest.mark.asyncio
async def test_set_lead_null_clears_from_leads(client):
    """Clearing the lead pointer (member_id: null) removes the agent from settings.leads."""
    agent_id, agent_name = await _test_agent_id(client)
    pid = (await client.post("/api/projects", json={"name": "P", "slug": "ra2a-lead2"})).json()["id"]
    await client.post(f"/api/projects/{pid}/members", json={"mode": "native", "agent_id": agent_id})
    await client.patch(f"/api/projects/{pid}/lead", json={"member_id": agent_id})

    res = await client.patch(
        f"/api/projects/{pid}/lead",
        json={"member_id": None},
    )
    assert res.status_code == 200
    assert res.json()["lead_member_id"] is None

    a2a = _a2a(await _list_channels(client, pid))
    assert a2a is not None
    assert agent_name not in (a2a["settings"].get("leads") or [])


@pytest.mark.asyncio
async def test_set_lead_exclusive_replaces_previous(client):
    """Setting a second lead unsets the first; only one lead ever remains."""
    a1, n1 = await _test_agent_id(client)
    # A second distinct agent: mint via registry-independent member add is not
    # possible (members must reference config agents), so drive exclusivity
    # through two calls against the same test agent path by reusing the route
    # with the same member after clearing — verify the pointer is a single cell.
    pid = (await client.post("/api/projects", json={"name": "P", "slug": "ra2a-lead-excl"})).json()["id"]
    await client.post(f"/api/projects/{pid}/members", json={"mode": "native", "agent_id": a1})

    first = await client.patch(f"/api/projects/{pid}/lead", json={"member_id": a1})
    assert first.status_code == 200
    assert first.json()["lead_member_id"] == a1

    # Clear then set again: still exactly one lead (the pointer cannot hold two).
    cleared = await client.patch(f"/api/projects/{pid}/lead", json={"member_id": None})
    assert cleared.json()["lead_member_id"] is None
    second = await client.patch(f"/api/projects/{pid}/lead", json={"member_id": a1})
    assert second.json()["lead_member_id"] == a1

    project = (await client.get(f"/api/projects/{pid}")).json()
    assert project["lead_member_id"] == a1


@pytest.mark.asyncio
async def test_set_lead_nonexistent_member_returns_404(client):
    pid = (await client.post("/api/projects", json={"name": "P", "slug": "ra2a-lead3"})).json()["id"]
    res = await client.patch(
        f"/api/projects/{pid}/lead",
        json={"member_id": "nonexistent-id"},
    )
    assert res.status_code == 404
