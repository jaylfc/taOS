"""Cluster invariants for the worker-as-LXC architecture.

Two checks:
  1. One worker LXC per bare host — enforced via host_lan_ip uniqueness.
  2. Privileged + nesting verification at incus-enroll (separate test
     coverage in test_local_worker_enroll.py once that exists).

This file covers (1).
"""
import pytest


@pytest.mark.asyncio
async def test_second_worker_with_same_host_lan_ip_returns_409(client):
    payload1 = {
        "name": "worker-a",
        "url": "http://192.168.1.50:6970",
        "host_lan_ip": "192.168.1.50",
    }
    r1 = await client.post("/api/cluster/workers", json=payload1)
    assert r1.status_code == 200

    payload2 = {
        "name": "worker-b",
        "url": "http://192.168.1.50:6970",
        "host_lan_ip": "192.168.1.50",
    }
    r2 = await client.post("/api/cluster/workers", json=payload2)
    assert r2.status_code == 409
    body = r2.json()
    assert "already" in body["error"].lower() or "exists" in body["error"].lower()


@pytest.mark.asyncio
async def test_two_workers_with_different_host_lan_ips_both_succeed(client):
    r1 = await client.post("/api/cluster/workers", json={
        "name": "worker-a",
        "url": "http://192.168.1.50:6970",
        "host_lan_ip": "192.168.1.50",
    })
    r2 = await client.post("/api/cluster/workers", json={
        "name": "worker-b",
        "url": "http://192.168.1.51:6970",
        "host_lan_ip": "192.168.1.51",
    })
    assert r1.status_code == 200
    assert r2.status_code == 200


@pytest.mark.asyncio
async def test_worker_without_host_lan_ip_does_not_collide(client):
    """Legacy flat-mode workers (no host_lan_ip) shouldn't trigger the
    one-per-host check — they go through the normal name-uniqueness path
    only.
    """
    r1 = await client.post("/api/cluster/workers", json={
        "name": "legacy-a",
        "url": "http://192.168.1.50:6970",
    })
    r2 = await client.post("/api/cluster/workers", json={
        "name": "legacy-b",
        "url": "http://192.168.1.50:6970",
    })
    assert r1.status_code == 200
    assert r2.status_code == 200
