import pytest


@pytest.mark.asyncio
async def test_sdk_served(client):
    r = await client.get("/api/userspace-apps/sdk.js")
    assert r.status_code == 200
    assert "application/javascript" in r.headers["content-type"]
    assert "window.taos" in r.text
