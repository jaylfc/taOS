"""Tests for the cluster capability-map endpoints.

Coverage:
- heartbeat requires worker HMAC; unsigned -> 401
- heartbeat node_id must match the authenticated worker name -> 403 on mismatch
- a signed heartbeat upserts and the row is readable via admin list
- list filters by status
- set-status validates the value and 404s an unknown node
- prune drops stale rows
- reads/mutations require an admin session
"""
from __future__ import annotations

import pytest

from test_routes_cluster_pairing import pair_worker, sign_worker_request


async def _signed_heartbeat(client, key, node):
    import json

    path = "/api/cluster/capability/heartbeat"
    body = json.dumps(node).encode()
    headers = sign_worker_request(key, node["node_id"], "POST", path, body)
    headers["Content-Type"] = "application/json"
    return await client.post(path, content=body, headers=headers)


@pytest.mark.asyncio
async def test_heartbeat_requires_hmac(client, app):
    """An unsigned heartbeat is rejected by the worker HMAC gate."""
    await app.state.capability_map.init()
    resp = await client.post(
        "/api/cluster/capability/heartbeat",
        json={"node_id": "node-a"},
    )
    assert resp.status_code == 401
    await app.state.capability_map.close()


@pytest.mark.asyncio
async def test_heartbeat_name_mismatch_rejected(client, app):
    """The signed worker name must equal the heartbeat node_id."""
    await app.state.capability_map.init()
    await app.state.cluster_pairing.init()
    key = await pair_worker(client, app, "node-a", "http://10.0.0.5:9000")
    # Sign as node-a but claim node_id node-b in the body.
    import json

    path = "/api/cluster/capability/heartbeat"
    body = json.dumps({"node_id": "node-b"}).encode()
    headers = sign_worker_request(key, "node-a", "POST", path, body)
    headers["Content-Type"] = "application/json"
    resp = await client.post(path, content=body, headers=headers)
    assert resp.status_code == 403
    await app.state.capability_map.close()


@pytest.mark.asyncio
async def test_heartbeat_upserts_and_lists(client, app):
    """A signed heartbeat upserts a row visible to the admin list."""
    await app.state.capability_map.init()
    await app.state.cluster_pairing.init()
    key = await pair_worker(client, app, "node-a", "http://10.0.0.5:9000")
    resp = await _signed_heartbeat(
        client,
        key,
        {
            "node_id": "node-a",
            "hostname": "pi5",
            "ram_mb": 16000,
            "gpu": {"name": "mali"},
            "npu": {"tops": 6},
            "status": "online",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["node_id"] == "node-a"
    assert resp.json()["gpu"] == {"name": "mali"}

    resp = await client.get("/api/cluster/capability")
    assert resp.status_code == 200
    nodes = resp.json()["nodes"]
    assert any(n["node_id"] == "node-a" and n["ram_mb"] == 16000 for n in nodes)
    await app.state.capability_map.close()


@pytest.mark.asyncio
async def test_list_filters_by_status(client, app):
    """?status= filters the returned rows."""
    await app.state.capability_map.init()
    await app.state.cluster_pairing.init()
    key_a = await pair_worker(client, app, "node-a", "http://10.0.0.5:9000")
    key_b = await pair_worker(client, app, "node-b", "http://10.0.0.6:9000")
    await _signed_heartbeat(client, key_a, {"node_id": "node-a", "status": "online"})
    await _signed_heartbeat(client, key_b, {"node_id": "node-b", "status": "draining"})

    resp = await client.get("/api/cluster/capability", params={"status": "draining"})
    assert resp.status_code == 200
    nodes = resp.json()["nodes"]
    assert [n["node_id"] for n in nodes] == ["node-b"]
    await app.state.capability_map.close()


@pytest.mark.asyncio
async def test_set_status_validates_and_404s(client, app):
    """set-status rejects bad values and unknown nodes."""
    await app.state.capability_map.init()
    await app.state.cluster_pairing.init()
    key = await pair_worker(client, app, "node-a", "http://10.0.0.5:9000")
    await _signed_heartbeat(client, key, {"node_id": "node-a", "status": "online"})

    resp = await client.post(
        "/api/cluster/capability/node-a/status", json={"status": "bogus"}
    )
    assert resp.status_code == 400

    resp = await client.post(
        "/api/cluster/capability/node-a/status", json={"status": "draining"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "draining"

    resp = await client.post(
        "/api/cluster/capability/ghost/status", json={"status": "online"}
    )
    assert resp.status_code == 404
    await app.state.capability_map.close()


@pytest.mark.asyncio
async def test_heartbeat_preserves_admin_draining(client, app):
    """A routine heartbeat must not clear an admin-set 'draining' status."""
    await app.state.capability_map.init()
    await app.state.cluster_pairing.init()
    key = await pair_worker(client, app, "node-a", "http://10.0.0.5:9000")
    await _signed_heartbeat(client, key, {"node_id": "node-a", "status": "online"})
    r = await client.post(
        "/api/cluster/capability/node-a/status", json={"status": "draining"}
    )
    assert r.json()["status"] == "draining"
    # A subsequent heartbeat (defaults status=online) must keep it draining.
    r = await _signed_heartbeat(client, key, {"node_id": "node-a", "status": "online"})
    assert r.json()["status"] == "draining"
    await app.state.capability_map.close()


@pytest.mark.asyncio
async def test_prune_drops_stale(client, app):
    """prune removes rows older than the cutoff."""
    await app.state.capability_map.init()
    await app.state.cluster_pairing.init()
    key = await pair_worker(client, app, "node-a", "http://10.0.0.5:9000")
    # last_seen far in the past so any positive cutoff prunes it.
    await _signed_heartbeat(
        client, key, {"node_id": "node-a", "status": "online"}
    )
    # Force the row stale directly via the store, then prune.
    await app.state.capability_map.upsert(
        {"node_id": "node-a", "status": "online", "last_seen": 1}
    )
    resp = await client.post(
        "/api/cluster/capability/prune", json={"older_than_s": 60}
    )
    assert resp.status_code == 200
    assert resp.json()["pruned"] == 1
    resp = await client.get("/api/cluster/capability")
    assert all(n["node_id"] != "node-a" for n in resp.json()["nodes"])
    await app.state.capability_map.close()


@pytest.mark.asyncio
async def test_worker_registration_populates_capability_map(client, app):
    """A paired worker's HMAC registration records its hardware into the map."""
    import json

    await app.state.capability_map.init()
    await app.state.cluster_pairing.init()
    key = await pair_worker(client, app, "rig-1", "http://10.2.0.1:9000")
    reg = json.dumps(
        {
            "name": "rig-1",
            "url": "http://10.2.0.1:9000",
            "platform": "linux",
            "host_lan_ip": "10.2.0.9",
            "hardware": {
                "cpu": {"cores": 8},
                "ram_mb": 16000,
                "gpu": {"name": "rtx3060"},
                "npu": {},
            },
        }
    ).encode()
    headers = sign_worker_request(key, "rig-1", "POST", "/api/cluster/workers", reg)
    headers["Content-Type"] = "application/json"
    resp = await client.post("/api/cluster/workers", content=reg, headers=headers)
    assert resp.status_code == 200, resp.text

    nodes = (await client.get("/api/cluster/capability")).json()["nodes"]
    node = next((n for n in nodes if n["node_id"] == "rig-1"), None)
    assert node is not None
    assert node["status"] == "online"
    assert node["ram_mb"] == 16000
    assert node["gpu"] == {"name": "rtx3060"}
    assert node["hostname"] == "10.2.0.9"
    await app.state.capability_map.close()


@pytest.mark.asyncio
async def test_registration_does_not_revive_drained_node(client, app):
    """If an admin drained a node, re-registration keeps it draining."""
    import json

    await app.state.capability_map.init()
    await app.state.cluster_pairing.init()
    key = await pair_worker(client, app, "rig-2", "http://10.2.0.2:9000")
    await app.state.capability_map.upsert({"node_id": "rig-2", "status": "draining"})

    reg = json.dumps({"name": "rig-2", "url": "http://10.2.0.2:9000", "platform": "linux"}).encode()
    headers = sign_worker_request(key, "rig-2", "POST", "/api/cluster/workers", reg)
    headers["Content-Type"] = "application/json"
    resp = await client.post("/api/cluster/workers", content=reg, headers=headers)
    assert resp.status_code == 200

    node = await app.state.capability_map.get("rig-2")
    assert node["status"] == "draining"
    await app.state.capability_map.close()


@pytest.mark.asyncio
async def test_reregistration_without_hardware_preserves_stored(client, app):
    """A re-register with empty hardware must not wipe previously-detected fields."""
    import json

    await app.state.capability_map.init()
    await app.state.cluster_pairing.init()
    key = await pair_worker(client, app, "rig-3", "http://10.3.0.1:9000")
    # First register with full hardware.
    reg1 = json.dumps({
        "name": "rig-3", "url": "http://10.3.0.1:9000", "platform": "linux",
        "hardware": {"cpu": {"cores": 12}, "ram_mb": 32000, "gpu": {"name": "rtx4090"}, "npu": {}},
    }).encode()
    h1 = sign_worker_request(key, "rig-3", "POST", "/api/cluster/workers", reg1)
    h1["Content-Type"] = "application/json"
    assert (await client.post("/api/cluster/workers", content=reg1, headers=h1)).status_code == 200

    # Re-register with NO hardware (legacy/flat-mode worker).
    reg2 = json.dumps({"name": "rig-3", "url": "http://10.3.0.1:9000", "platform": "linux"}).encode()
    h2 = sign_worker_request(key, "rig-3", "POST", "/api/cluster/workers", reg2)
    h2["Content-Type"] = "application/json"
    assert (await client.post("/api/cluster/workers", content=reg2, headers=h2)).status_code == 200

    node = await app.state.capability_map.get("rig-3")
    assert node["ram_mb"] == 32000  # preserved
    assert node["gpu"] == {"name": "rtx4090"}  # preserved
    assert node["status"] == "online"
    await app.state.capability_map.close()


@pytest.mark.asyncio
async def test_reregistration_with_zero_ram_updates_capability_map(client, app):
    """A worker reporting ram_mb=0 must update the map to 0, not preserve the old value."""
    import json

    await app.state.capability_map.init()
    await app.state.cluster_pairing.init()
    key = await pair_worker(client, app, "rig-4", "http://10.4.0.1:9000")
    reg1 = json.dumps({
        "name": "rig-4", "url": "http://10.4.0.1:9000", "platform": "linux",
        "hardware": {"ram_mb": 16000},
    }).encode()
    h1 = sign_worker_request(key, "rig-4", "POST", "/api/cluster/workers", reg1)
    h1["Content-Type"] = "application/json"
    assert (await client.post("/api/cluster/workers", content=reg1, headers=h1)).status_code == 200

    node = await app.state.capability_map.get("rig-4")
    assert node["ram_mb"] == 16000

    # Re-register with ram_mb=0 (worker is in a zero-memory state).
    reg2 = json.dumps({
        "name": "rig-4", "url": "http://10.4.0.1:9000", "platform": "linux",
        "hardware": {"ram_mb": 0},
    }).encode()
    h2 = sign_worker_request(key, "rig-4", "POST", "/api/cluster/workers", reg2)
    h2["Content-Type"] = "application/json"
    assert (await client.post("/api/cluster/workers", content=reg2, headers=h2)).status_code == 200

    node = await app.state.capability_map.get("rig-4")
    assert node["ram_mb"] == 0
    await app.state.capability_map.close()


@pytest.mark.asyncio
async def test_sweep_offlines_stale_online_nodes(client, app):
    """The sweep endpoint flips stale online nodes offline, keeping the row."""
    await app.state.capability_map.init()
    await app.state.capability_map.upsert({"node_id": "n-live", "status": "online"})
    await app.state.capability_map.upsert({"node_id": "n-gone", "status": "online", "last_seen": 1})
    resp = await client.post("/api/cluster/capability/sweep", json={"older_than_s": 60})
    assert resp.status_code == 200
    assert resp.json()["offlined"] == 1
    nodes = {n["node_id"]: n["status"] for n in (await client.get("/api/cluster/capability")).json()["nodes"]}
    assert nodes["n-gone"] == "offline"
    assert nodes["n-live"] == "online"
    await app.state.capability_map.close()
