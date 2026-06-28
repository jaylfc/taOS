"""TAOS_ADVERTISE_IP overrides the worker's advertised URL + host_lan_ip.

Inside a worker LXC the only locally-detectable address is the NAT'd
incusbr0 IP, which the controller cannot reach. The installer passes the
bare-host LAN IP as TAOS_ADVERTISE_IP; register() must advertise that
instead, or the controller stores an unreachable URL and the incus
host-match (enrollment) fails.
"""
import json
from typing import ClassVar

import pytest


class _CaptureResponse:
    status_code = 200

    def json(self):
        return {"status": "registered"}

    def raise_for_status(self):
        return None


class _CaptureClient:
    """Minimal async httpx.AsyncClient stand-in that records the POST body."""

    captured: ClassVar[dict] = {}

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, content=None, headers=None):
        _CaptureClient.captured = {"url": url, "body": json.loads(content)}
        return _CaptureResponse()


@pytest.mark.asyncio
async def test_register_prefers_advertise_ip(monkeypatch):
    from tinyagentos.worker import agent as agent_mod

    monkeypatch.setenv("TAOS_ADVERTISE_IP", "192.168.6.108")
    # Avoid touching disk/network for the heavy detection helpers.
    monkeypatch.setattr(agent_mod.WorkerAgent, "detect_backends",
                        lambda self: _aval([]))
    monkeypatch.setattr("tinyagentos.worker.pairing.load_signing_key",
                        lambda state_dir: b"k" * 32)
    monkeypatch.setattr(agent_mod, "_detect_lan_ip", lambda url: "10.228.114.210")
    monkeypatch.setattr(agent_mod.httpx, "AsyncClient", _CaptureClient)

    a = agent_mod.WorkerAgent("http://192.168.6.123:6969", name="fedora-worker")
    result = await a.register()

    assert result is True
    body = _CaptureClient.captured["body"]
    # Both the advertised URL host and host_lan_ip must be the bare-host IP,
    # NOT the LXC-internal 10.x that _detect_lan_ip returns.
    assert body["url"] == "http://192.168.6.108"
    assert body["host_lan_ip"] == "192.168.6.108"


async def _aval(v):
    return v
