"""Helpers for the device-link e2e tests: talk to the bed through bed.sh.

Nothing here runs unless TAOS_E2E_DEVICE_LINK=1 (see conftest.py); importing it
is side-effect free. The bed is driven only through `bed.sh`, so the tests never
need to know docker or compose details.
"""
from __future__ import annotations

import json
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
BED_SH = HERE / "bed.sh"
STATE = HERE / ".state"

# Section 5.0 mapping.
LINK_PORT = 6977
LOCAL_UI_PORT = 6969
LISTENER_BASE = 27700
RELAY_LISTENER_BASE = 27750
SOCKS_BASE = 27800
# routes/auth.py LOCK_CONSOLE_HEADER: console routes need it on top of a loopback peer.
CONSOLE_HEADER = "X-taOS-Console"


def needs(pr: str):
    """Mark a test that waits for code in PR `pr` (strict: it must start passing
    the moment the PR lands, and then the marker has to come off)."""
    return pytest.mark.xfail(strict=True, reason=f"needs {pr}")


@dataclass
class Resp:
    status: int
    headers: dict
    body: str

    @property
    def json(self):
        try:
            return json.loads(self.body)
        except ValueError:
            return None

    @property
    def error(self):
        j = self.json
        if not isinstance(j, dict):
            return None
        d = j.get("error", j.get("detail"))
        return d.get("error") if isinstance(d, dict) else d


def expect(resp: Resp, status: int, error: str | None = None) -> Resp:
    """Assert a status (and, for refusals, the error code); print the body on failure."""
    assert resp.status == status, f"want {status}, got {resp.status}: {resp.body[:300]!r}"
    if error is not None:
        assert resp.error == error, f"want error {error!r}, got {resp.error!r}: {resp.body[:300]!r}"
    return resp


@dataclass
class Endpoint:
    """One person's node on one container: (container, person)."""
    node: str
    person: str
    slot: int
    hostname: str
    ip: str
    account_id: str

    @property
    def sock(self) -> str:
        return f"/run/taos/mesh/{self.person}.sock"

    @property
    def listener_port(self) -> int:  # the loopback port behind serve :6977
        return LISTENER_BASE + self.slot

    @property
    def relay_listener_port(self) -> int:  # the loopback port behind serve :6969
        return RELAY_LISTENER_BASE + self.slot

    @property
    def socks(self) -> str:
        return f"127.0.0.1:{SOCKS_BASE + self.slot}"


class Bed:
    def __init__(self) -> None:
        data = json.loads((STATE / "nodes.json").read_text())
        self.accounts: dict = data["accounts"]
        self.personal: dict = data["personal"]
        self._eps = [Endpoint(n["node"], n["person"], n["slot"], n["hostname"], n["ip"], n["account_id"])
                     for n in data["nodes"]]
        self.time_scale = 60

    # -- topology ---------------------------------------------------------
    def at(self, spec: str) -> Endpoint:
        """'a-host1' or 'shared-s/f' (container/person)."""
        node, _, person = spec.partition("/")
        hits = [e for e in self._eps if e.node == node and (not person or e.person == person)]
        assert len(hits) == 1, f"{spec!r} matches {len(hits)} endpoints (use node/person)"
        return hits[0]

    def endpoints(self) -> list[Endpoint]:
        return list(self._eps)

    # -- running things ---------------------------------------------------
    def sh(self, *args: str, input: str | None = None, timeout: int = 120) -> subprocess.CompletedProcess:
        return subprocess.run([str(BED_SH), *args], input=input, capture_output=True, text=True, timeout=timeout)

    def exec(self, node: str, *argv: str, input: str | None = None, timeout: int = 60,
             as_user: str | None = None) -> subprocess.CompletedProcess:
        """Run argv inside a container. as_user drops to that Linux user first."""
        if as_user:
            argv = ("setpriv", f"--reuid={as_user}", f"--regid={as_user}", "--init-groups", *argv)
        return self.sh("exec", node, *argv, input=input, timeout=timeout)

    def hs(self, *args: str) -> str:
        r = self.sh("hs", *args)
        assert r.returncode == 0, r.stderr
        return r.stdout

    def hs_nodes(self) -> list[dict]:
        return json.loads(self.hs("nodes", "list", "-o", "json") or "[]")

    def ts(self, ep: Endpoint, *args: str, timeout: int = 30) -> subprocess.CompletedProcess:
        """tailscale CLI for one person's instance. ALWAYS --socket (design 5.0)."""
        return self.exec(ep.node, "tailscale", f"--socket={ep.sock}", *args, timeout=timeout)

    def ts_json(self, ep: Endpoint, *args: str) -> dict:
        r = self.ts(ep, *args)
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)

    # -- the network, as a client sees it ---------------------------------
    def reach(self, src: Endpoint, dst_ip: str, port: int, wait: int = 8) -> str:
        """Open a TCP connection from src's userspace node to dst_ip:port with
        `tailscale nc`, send a minimal HTTP request, and return what came back.
        '' means no connection was made (dropped by the ACL, or refused)."""
        cmd = (f"printf 'GET / HTTP/1.0\\r\\n\\r\\n' | timeout {wait} "
               f"tailscale --socket={shlex.quote(src.sock)} nc {shlex.quote(dst_ip)} {port}")
        r = self.exec(src.node, "sh", "-c", cmd, timeout=wait + 15)
        return r.stdout

    def http(self, src: Endpoint | str, url: str, method: str = "GET", body=None,
             headers: dict | None = None, via: Endpoint | None = None, wait: int = 15) -> Resp:
        """curl from inside a container. With via=<endpoint>, the request leaves
        through that person's SOCKS5 proxy (design 5.0 outbound); otherwise it is a
        plain request from the container's own network namespace."""
        node = src.node if isinstance(src, Endpoint) else src
        argv = ["curl", "-s", "-i", "--max-time", str(wait), "-X", method]
        for k, v in (headers or {}).items():
            argv += ["-H", f"{k}: {v}"]
        if body is not None:
            argv += ["-H", "Content-Type: application/json", "--data", json.dumps(body)]
        if via is not None:
            argv += ["--socks5-hostname", via.socks]
        argv.append(url)
        r = self.exec(node, *argv, timeout=wait + 15)
        return self._parse(r.stdout)

    @staticmethod
    def _parse(raw: str) -> Resp:
        head, _, body = raw.partition("\r\n\r\n")
        lines = head.split("\r\n")
        try:
            status = int(lines[0].split()[1])
        except (IndexError, ValueError):
            return Resp(0, {}, raw)
        hdrs = {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:])}
        return Resp(status, hdrs, body)

    # -- the controller's two surfaces ------------------------------------
    def local(self, ep: Endpoint | str, method: str, path: str, body=None, headers=None) -> Resp:
        """The local UI surface: loopback 6969 inside the node (what the console and web UI use)."""
        return self.http(ep, f"http://127.0.0.1:{LOCAL_UI_PORT}{path}", method, body, headers)

    def link(self, src: Endpoint, dst: Endpoint, method: str, path: str, body=None, headers=None) -> Resp:
        """The device-link surface: src's SOCKS5 -> dst's node :6977 -> serve -> dst's loopback listener."""
        return self.http(src, f"http://{dst.ip}:{LINK_PORT}{path}", method, body, headers, via=src)

    def via_relay(self, dst: Endpoint, method: str, path: str, body=None, headers=None) -> Resp:
        """What the relay does: reach the person's node on :6969 (-> serve -> the remote-marked listener)."""
        return self.http("relay", f"http://{dst.ip}:{LOCAL_UI_PORT}{path}", method, body, headers,
                         via=self.at("relay"))

    # -- taos.my ----------------------------------------------------------
    def my_http(self, method: str, path: str, body=None, headers: dict | None = None) -> Resp:
        script = (
            "import sys,json,urllib.request,urllib.error\n"
            "a=json.loads(sys.argv[1])\n"
            "d=json.dumps(a['body']).encode() if a['body'] is not None else None\n"
            "r=urllib.request.Request('http://localhost:8080'+a['path'],data=d,method=a['method'])\n"
            "r.add_header('Content-Type','application/json')\n"
            "[r.add_header(k,v) for k,v in a['headers'].items()]\n"
            "try:\n  x=urllib.request.urlopen(r,timeout=15); s=x.status; b=x.read().decode(); h=x.headers\n"
            "except urllib.error.HTTPError as e:\n  s=e.code; b=e.read().decode(); h=e.headers\n"
            "print(s); print(b)\n"
        )
        arg = json.dumps({"method": method, "path": path, "body": body, "headers": headers or {}})
        r = self.exec("taosmy", "python3", "-c", script, arg)
        status, _, text = r.stdout.partition("\n")
        return Resp(int(status or 0), {}, text)

    # -- time and failure injection ---------------------------------------
    def scaled(self, seconds: float) -> float:
        """Real seconds to wait for `seconds` of design time (TAOS_DEVICE_LINK_TIME_SCALE)."""
        return seconds / self.time_scale

    def sleep(self, seconds: float) -> None:
        time.sleep(self.scaled(seconds))

    def stop(self, *services: str) -> None:
        assert self.sh("stop", *services).returncode == 0

    def start(self, *services: str) -> None:
        assert self.sh("start", *services).returncode == 0

    def policy(self, which: str) -> None:
        r = self.sh("policy", which)
        assert r.returncode == 0, r.stderr


@dataclass
class Counter:
    """A before/after count, so 'nothing arrived' can never mean 'nothing was measured'."""
    read: object
    before: int = field(init=False)

    def __post_init__(self):
        self.before = self.read()

    def delta(self) -> int:
        return self.read() - self.before
