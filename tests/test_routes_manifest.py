"""Endpoint tests for tinyagentos/routes/manifest.py."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_manifest_returns_200(client):
    resp = await client.get("/manifest?app=messages")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_manifest_response_shape(client):
    data = (await client.get("/manifest?app=messages")).json()
    for key in ("name", "short_name", "id", "start_url", "scope", "display", "theme_color", "background_color", "icons"):
        assert key in data, f"missing manifest key: {key}"
    assert isinstance(data["icons"], list)
    assert len(data["icons"]) == 2
    for icon in data["icons"]:
        for key in ("src", "sizes", "type", "purpose"):
            assert key in icon, f"missing icon key: {key}"


@pytest.mark.asyncio
async def test_manifest_unknown_app_returns_404(client):
    resp = await client.get("/manifest?app=unknown")
    assert resp.status_code == 404
    data = resp.json()
    assert data["detail"] == "App not found or not PWA-enabled"


@pytest.mark.asyncio
async def test_manifest_with_spaces_in_app_id(client):
    resp = await client.get("/manifest?app= messages")
    assert resp.status_code == 404
    data = resp.json()
    assert data["detail"] == "App not found or not PWA-enabled"