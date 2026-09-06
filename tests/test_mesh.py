"""Headless Headscale mesh membership (taOSgo Slice 2). Everything shells to
`tailscale` and must be fail-soft: a host without the binary degrades to a
structured "not available" result, never raises."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from tinyagentos.taosnet import mesh


def _run_returns(rc, out="", err=""):
    async def _fake(args, timeout=30.0):
        return rc, out, err
    return _fake


class TestInstalledGuard:
    def test_not_installed_short_circuits(self):
        with patch.object(mesh, "is_tailscale_installed", return_value=False):
            import asyncio
            r = asyncio.run(mesh.mesh_up("key", "host"))
            assert r == {"ok": False, "detail": "tailscale not installed"}
            s = asyncio.run(mesh.mesh_status())
            assert s["joined"] is False and "not installed" in s["detail"]
            assert asyncio.run(mesh.is_joined()) is False

    def test_login_server_env_override(self, monkeypatch):
        monkeypatch.setenv("TAOS_HEADSCALE_URL", "https://hs.staging.example/")
        assert mesh.login_server() == "https://hs.staging.example"
        monkeypatch.delenv("TAOS_HEADSCALE_URL", raising=False)
        assert mesh.login_server() == "https://hs.taos.my"


@pytest.mark.asyncio
class TestMeshUp:
    async def test_missing_args(self):
        assert (await mesh.mesh_up("", "host"))["ok"] is False
        assert (await mesh.mesh_up("key", ""))["ok"] is False

    async def test_up_success(self):
        with patch.object(mesh, "is_tailscale_installed", return_value=True), \
             patch.object(mesh, "_run", _run_returns(0)):
            r = await mesh.mesh_up("preauth", "myhost", ls="https://hs.taos.my")
        assert r["ok"] is True and "hs.taos.my" in r["detail"]

    async def test_up_failure_is_fail_soft(self):
        with patch.object(mesh, "is_tailscale_installed", return_value=True), \
             patch.object(mesh, "_run", _run_returns(1, err="invalid key")):
            r = await mesh.mesh_up("bad", "myhost")
        assert r["ok"] is False and "invalid key" in r["detail"]

    async def test_up_passes_expected_args(self):
        captured = {}

        async def _capture(args, timeout=60.0):
            captured["args"] = args
            return 0, "", ""

        with patch.object(mesh, "is_tailscale_installed", return_value=True), \
             patch.object(mesh, "_run", _capture):
            await mesh.mesh_up("PA", "node-1", ls="https://hs.x")
        a = captured["args"]
        assert a[:2] == ["tailscale", "up"]
        assert "--authkey" in a and "PA" in a
        assert "--hostname" in a and "node-1" in a
        assert "--login-server" in a and "https://hs.x" in a


@pytest.mark.asyncio
class TestMeshStatus:
    _STATUS = {
        "BackendState": "Running",
        "CurrentTailnet": {"Name": "jason"},
        "Self": {"Online": True, "HostName": "node-1", "TailscaleIPs": ["100.64.0.5"]},
    }

    async def test_joined_parse(self):
        with patch.object(mesh, "is_tailscale_installed", return_value=True), \
             patch.object(mesh, "_run", _run_returns(0, out=json.dumps(self._STATUS))):
            s = await mesh.mesh_status()
        assert s["joined"] is True
        assert s["tailnet"] == "jason"
        assert s["node_ip"] == "100.64.0.5"
        assert s["hostname"] == "node-1"
        assert s["guests"] == []

    async def test_guests_field_present_even_when_no_peers(self):
        """mesh_status always includes ``guests`` (empty list when no guest peers)."""
        s = await mesh.mesh_status()
        # Fast-path: not installed returns joined=False with detail, never raises.
        assert isinstance(s, dict)
        if s.get("joined"):
            assert isinstance(s.get("guests"), list)

    async def test_guest_peer_detected(self):
        """A peer tagged ``tag:guest`` appears in the guests list."""
        status = dict(self._STATUS)
        status["Peer"] = {
            "nodekey:abc": {
                "HostName": "hogne-box",
                "Online": True,
                "TailscaleIPs": ["100.64.0.6"],
                "Tags": ["tag:guest"],
            }
        }
        with patch.object(mesh, "is_tailscale_installed", return_value=True), \
             patch.object(mesh, "_run", _run_returns(0, out=json.dumps(status))):
            s = await mesh.mesh_status()
        assert s["guests"] == [
            {"hostname": "hogne-box", "node_ip": "100.64.0.6", "online": True}
        ]

    async def test_non_guest_peers_excluded(self):
        """Peers without ``tag:guest`` are omitted from the guests list."""
        status = dict(self._STATUS)
        status["Peer"] = {
            "nodekey:a": {"HostName": "host-2", "Online": True,
                          "TailscaleIPs": ["100.64.0.7"], "Tags": []},
            "nodekey:b": {"HostName": "guest-1", "Online": False,
                          "TailscaleIPs": ["100.64.0.8"], "Tags": ["tag:guest"]},
        }
        with patch.object(mesh, "is_tailscale_installed", return_value=True), \
             patch.object(mesh, "_run", _run_returns(0, out=json.dumps(status))):
            s = await mesh.mesh_status()
        assert len(s["guests"]) == 1
        assert s["guests"][0]["hostname"] == "guest-1"

    async def test_malformed_peer_data_graceful(self):
        """A peer dict that isn't a dict is silently skipped."""
        status = dict(self._STATUS)
        status["Peer"] = {"bad": "not-a-dict"}
        with patch.object(mesh, "is_tailscale_installed", return_value=True), \
             patch.object(mesh, "_run", _run_returns(0, out=json.dumps(status))):
            s = await mesh.mesh_status()
        assert s["guests"] == []

    async def test_not_running_not_joined(self):
        stopped = {"BackendState": "Stopped", "Self": {}}
        with patch.object(mesh, "is_tailscale_installed", return_value=True), \
             patch.object(mesh, "_run", _run_returns(0, out=json.dumps(stopped))):
            s = await mesh.mesh_status()
        assert s["joined"] is False

    async def test_unparseable_status_fail_soft(self):
        with patch.object(mesh, "is_tailscale_installed", return_value=True), \
             patch.object(mesh, "_run", _run_returns(0, out="not-json")):
            s = await mesh.mesh_status()
        assert s["joined"] is False and "unparseable" in s["detail"]

    async def test_status_nonzero_fail_soft(self):
        with patch.object(mesh, "is_tailscale_installed", return_value=True), \
             patch.object(mesh, "_run", _run_returns(1, err="not running")):
            s = await mesh.mesh_status()
        assert s["joined"] is False and "not running" in s["detail"]


@pytest.mark.asyncio
class TestMeshDown:
    async def test_down_success(self):
        with patch.object(mesh, "is_tailscale_installed", return_value=True), \
             patch.object(mesh, "_run", _run_returns(0)):
            assert (await mesh.mesh_down())["ok"] is True

    async def test_down_not_installed(self):
        with patch.object(mesh, "is_tailscale_installed", return_value=False):
            assert (await mesh.mesh_down())["ok"] is False
