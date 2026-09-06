"""Tests for streaming app routes."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_list_streaming_apps(client):
    r = await client.get("/api/streaming-apps")
    assert r.status_code == 200
    data = r.json()
    assert "apps" in data
    assert isinstance(data["apps"], list)


@pytest.mark.asyncio
async def test_list_sessions_empty(client):
    r = await client.get("/api/streaming-apps/sessions")
    assert r.status_code == 200
    data = r.json()
    assert "sessions" in data
    assert data["sessions"] == []


@pytest.mark.asyncio
async def test_get_session_not_found(client):
    r = await client.get("/api/streaming-apps/sessions/nonexistent")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_stop_session_not_found(client):
    r = await client.post("/api/streaming-apps/sessions/nonexistent/stop")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_swap_agent_not_found(client):
    r = await client.post(
        "/api/streaming-apps/sessions/nonexistent/swap-agent",
        json={"agent_name": "test-agent", "agent_type": "app-expert"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_launch_and_get_session(client):
    r = await client.post("/api/streaming-apps/launch", json={"app_id": "test-app"})
    assert r.status_code == 200
    data = r.json()
    assert "session_id" in data
    assert data["status"] == "running"

    session_id = data["session_id"]
    r2 = await client.get(f"/api/streaming-apps/sessions/{session_id}")
    assert r2.status_code == 200
    session = r2.json()
    assert session["session_id"] == session_id
    assert session["app_id"] == "test-app"
    assert session["status"] == "running"


@pytest.mark.asyncio
async def test_launcher_data(client):
    resp = await client.get("/api/streaming-apps/launcher")
    assert resp.status_code == 200
    data = resp.json()
    assert "apps" in data
    assert "sessions" in data
    assert "agents" in data


@pytest.mark.asyncio
async def test_launch_and_stop(client):
    r = await client.post("/api/streaming-apps/launch", json={"app_id": "test-app"})
    assert r.status_code == 200
    session_id = r.json()["session_id"]

    r2 = await client.post(f"/api/streaming-apps/sessions/{session_id}/stop")
    assert r2.status_code == 200
    data = r2.json()
    assert data["status"] == "stopped"

    r3 = await client.get(f"/api/streaming-apps/sessions/{session_id}")
    assert r3.status_code == 200
    assert r3.json()["status"] == "stopped"
