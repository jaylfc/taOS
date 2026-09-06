"""Route-level tests for chat channel project_id filtering."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_get_channels_filter_by_project_id(client):
    """GET /api/chat/channels?project_id=... returns only matching channels."""
    # Create three channels with different project scopes
    await client.post("/api/chat/channels", json={"name": "root-ch", "type": "topic"})
    await client.post("/api/chat/channels", json={"name": "prj-x-ch", "type": "topic", "project_id": "prj-X"})
    await client.post("/api/chat/channels", json={"name": "prj-y-ch", "type": "topic", "project_id": "prj-Y"})

    resp = await client.get("/api/chat/channels?project_id=prj-X")
    assert resp.status_code == 200
    channels = resp.json()["channels"]
    names = {c["name"] for c in channels}
    assert "prj-x-ch" in names
    assert "prj-y-ch" not in names
    assert "root-ch" not in names


@pytest.mark.asyncio
async def test_post_channel_with_project_id_then_filter(client):
    """POST with project_id body field; GET filter returns it."""
    resp = await client.post(
        "/api/chat/channels",
        json={"name": "zeta-room", "type": "group", "project_id": "prj-Z"},
    )
    assert resp.status_code == 200
    created = resp.json()
    assert created["project_id"] == "prj-Z"

    resp = await client.get("/api/chat/channels?project_id=prj-Z")
    assert resp.status_code == 200
    channels = resp.json()["channels"]
    ids = {c["id"] for c in channels}
    assert created["id"] in ids
