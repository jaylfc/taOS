import pytest


@pytest.mark.asyncio
async def test_activity_endpoint_returns_shape(client):
    resp = await client.get("/api/activity")
    assert resp.status_code == 200
    data = resp.json()
    # Core shape
    assert "timestamp" in data
    assert "hardware" in data
    assert "cpu" in data
    assert "memory" in data
    assert "npu" in data
    assert "gpu" in data
    assert "thermal" in data
    assert "disk" in data
    assert "network" in data
    assert "processes" in data
    # CPU cores list
    assert isinstance(data["cpu"]["cores"], list)
    # Memory numbers
    assert data["memory"]["total_mb"] > 0
