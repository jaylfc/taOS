"""taosnet/mesh.py must never re-point or log out a tailscaled it did not join.

Both friend-test nodes run one system tailscaled already logged in to the
owner's own tailnet, which is the only remote path to them. A mesh join that
drives that daemon at hs.taos.my strands the node, and a mesh_down that runs
`tailscale logout` on it takes the node off its own tailnet.

These tests put a fake `tailscale` on PATH that records every argv and answers
`debug prefs` (where tailscale reports ControlURL; `status --json` does not
carry it) and `status --json` from canned bodies.
"""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from tinyagentos.taosnet import mesh

FOREIGN = "https://controlplane.tailscale.com"
OURS = "https://hs.taos.my"

_FAKE = """#!{python}
import json, sys
from pathlib import Path
here = Path(__file__).resolve().parent
with open(here / "argv.log", "a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\\n")
args = [a for a in sys.argv[1:] if not a.startswith("--socket=")]
if args[:2] == ["debug", "prefs"]:
    print((here / "prefs.json").read_text())
elif args[:2] == ["status", "--json"]:
    print((here / "status.json").read_text())
sys.exit(0)
"""


def _fake_tailscale(tmp_path: Path, monkeypatch, control_url: str, logged_out: bool = False) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "tailscale"
    exe.write_text(_FAKE.format(python=sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    (bindir / "prefs.json").write_text(json.dumps({"ControlURL": control_url, "LoggedOut": logged_out}))
    (bindir / "status.json").write_text(json.dumps({
        "BackendState": "Running",
        "CurrentTailnet": {"Name": "owner-tailnet"},
        "Self": {"Online": True, "HostName": "handset", "TailscaleIPs": ["100.64.0.9"]},
    }))
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.delenv("TAOS_TAILSCALE_SOCKET", raising=False)
    monkeypatch.delenv("TAOS_HEADSCALE_URL", raising=False)
    return bindir / "argv.log"


def _calls(log: Path) -> list[list[str]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]


def _subcommands(log: Path) -> list[str]:
    return [next((a for a in argv if not a.startswith("--socket=")), "") for argv in _calls(log)]


@pytest.mark.asyncio
async def test_mesh_up_refuses_a_daemon_on_another_control_server(tmp_path, monkeypatch):
    log = _fake_tailscale(tmp_path, monkeypatch, FOREIGN)
    r = await mesh.mesh_up("preauth", "handset")
    assert r["ok"] is False
    assert r["detail"] == "foreign-control-server"
    assert "up" not in _subcommands(log)


@pytest.mark.asyncio
async def test_mesh_up_with_a_dedicated_socket_passes_it_on_every_call(tmp_path, monkeypatch):
    log = _fake_tailscale(tmp_path, monkeypatch, FOREIGN)
    monkeypatch.setenv("TAOS_TAILSCALE_SOCKET", "/tmp/x.sock")
    r = await mesh.mesh_up("preauth", "handset")
    assert r["ok"] is True
    calls = _calls(log)
    assert "up" in _subcommands(log)
    assert calls and all(argv[0] == "--socket=/tmp/x.sock" for argv in calls)


@pytest.mark.asyncio
async def test_mesh_down_never_logs_out_a_foreign_daemon(tmp_path, monkeypatch):
    log = _fake_tailscale(tmp_path, monkeypatch, FOREIGN)
    r = await mesh.mesh_down()
    assert r["ok"] is False
    assert r["detail"] == "foreign-control-server"
    assert "logout" not in _subcommands(log)


@pytest.mark.asyncio
async def test_mesh_up_on_our_own_control_server_proceeds_without_socket(tmp_path, monkeypatch):
    log = _fake_tailscale(tmp_path, monkeypatch, OURS + "/")
    r = await mesh.mesh_up("preauth", "handset")
    assert r["ok"] is True
    assert "up" in _subcommands(log)
    assert not any(a.startswith("--socket=") for argv in _calls(log) for a in argv)


@pytest.mark.asyncio
async def test_a_logged_out_daemon_may_be_joined(tmp_path, monkeypatch):
    log = _fake_tailscale(tmp_path, monkeypatch, FOREIGN, logged_out=True)
    r = await mesh.mesh_up("preauth", "handset")
    assert r["ok"] is True
    assert "up" in _subcommands(log)


@pytest.mark.asyncio
async def test_status_on_a_foreign_daemon_is_not_joined(tmp_path, monkeypatch):
    """The cluster-join poll skips the join when is_joined() is true, so a
    host on its owner's tailnet must not read as already on the mesh."""
    _fake_tailscale(tmp_path, monkeypatch, FOREIGN)
    s = await mesh.mesh_status()
    assert s["joined"] is False
    assert s["detail"] == "foreign-control-server"
    assert await mesh.is_joined() is False


@pytest.mark.asyncio
async def test_status_on_our_control_server_is_joined(tmp_path, monkeypatch):
    _fake_tailscale(tmp_path, monkeypatch, OURS)
    s = await mesh.mesh_status()
    assert s["joined"] is True
    assert s["hostname"] == "handset"


@pytest.mark.asyncio
async def test_unreadable_prefs_refuse_rather_than_guess(tmp_path, monkeypatch):
    log = _fake_tailscale(tmp_path, monkeypatch, FOREIGN)
    (log.parent / "prefs.json").write_text("not json")
    assert (await mesh.mesh_down())["detail"] == "control-server-unknown"
    assert (await mesh.mesh_up("preauth", "handset"))["detail"] == "control-server-unknown"
    assert not {"up", "logout"} & set(_subcommands(log))
