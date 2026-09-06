"""Tests for the dynamic PWA manifest endpoint (GET /manifest?app=<id>)."""
import pytest


@pytest.mark.asyncio
async def test_manifest_messages_returns_200(client):
    resp = await client.get("/manifest?app=messages")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_manifest_messages_has_correct_shape(client):
    resp = await client.get("/manifest?app=messages")
    assert resp.status_code == 200
    data = resp.json()
    assert data["display"] == "standalone"
    assert data["start_url"] == "/app.html?app=messages"
    assert "name" in data
    assert "short_name" in data
    assert "icons" in data
    assert len(data["icons"]) >= 2


@pytest.mark.asyncio
async def test_manifest_unknown_app_returns_404(client):
    resp = await client.get("/manifest?app=unknown-app")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_manifest_non_pwa_app_returns_404(client):
    # "files" is a real app that does not have pwa:true
    resp = await client.get("/manifest?app=files")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_manifest_missing_param_returns_404(client):
    resp = await client.get("/manifest?app=")
    assert resp.status_code == 404
