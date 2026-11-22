"""T0: the mesh layer on its own (MT3, MT4, MT11, SC1, MTU2 at bed level, the MT5 lever).

REAL tests: they need only the Headscale policy, the userspace nodes and a
listener behind each `serve` mapping (the bed's stand-in until T3). The app is
not involved, which is the point of these cases: each isolation layer is proven
alone (design 10, rule 5). Every refusal has its must-succeed control right next
to it, using the same request shape.
"""
from __future__ import annotations

import os
import time

import pytest

from _device_link_bed import CONSOLE_HEADER

STUBS = os.environ.get("TAOS_BED_STUBS", "1") != "0"


def up(body: str) -> bool:
    """Something answered on the connection (the stand-in or the controller)."""
    return bool(body.strip())


def test_MT3_mesh_acl_blocks_the_other_person(bed):
    a_host, a_phone, b_host, b_phone = bed.at("a-host1"), bed.at("a-phone"), bed.at("b-host"), bed.at("b-phone")
    # red: B's node cannot connect to A's node, on either port
    assert not up(bed.reach(b_host, a_host.ip, 6977))
    assert not up(bed.reach(b_host, a_host.ip, 6969))
    assert not up(bed.reach(b_phone, a_host.ip, 6977))
    # control: A's own phone connects, on the same ports, in the same run
    assert up(bed.reach(a_phone, a_host.ip, 6977))
    assert up(bed.reach(a_phone, a_host.ip, 6969))
    # control: B reaches B's own node, so "nothing came back" is not a dead B node
    assert up(bed.reach(b_host, b_phone.ip, 6977))
    # and the other way round
    assert not up(bed.reach(a_phone, b_host.ip, 6977))


def test_MT4_relay_reaches_6969_only(bed):
    relay, a_host, b_host = bed.at("relay"), bed.at("a-host1"), bed.at("b-host")
    # red: the relay node cannot reach the device-link port of any person's node
    assert not up(bed.reach(relay, a_host.ip, 6977))
    assert not up(bed.reach(relay, b_host.ip, 6977))
    # control: the same relay reaches 6969 on both
    assert up(bed.reach(relay, a_host.ip, 6969))
    assert up(bed.reach(relay, b_host.ip, 6969))
    # and 6969 on the node is the remote-marked listener, never the console port
    if STUBS:
        assert "stub:a-host1:a:6969" in bed.reach(relay, a_host.ip, 6969)


def test_SC1_mesh_isolation_on_a_shared_computer(bed):
    a_phone, f_phone = bed.at("a-phone"), bed.at("f-phone")
    s_a, s_f = bed.at("shared-s/a"), bed.at("shared-s/f")
    # red: A's phone cannot reach F's node on S on any port tried
    for port in (6977, 6969, 22, 8080):
        assert not up(bed.reach(a_phone, s_f.ip, port)), port
    # control: A's phone reaches A's node on S on 6977 (and 6969)
    assert up(bed.reach(a_phone, s_a.ip, 6977))
    assert up(bed.reach(a_phone, s_a.ip, 6969))
    # the mirror image, so neither direction is the unmeasured one
    for port in (6977, 6969):
        assert not up(bed.reach(f_phone, s_a.ip, port)), port
    assert up(bed.reach(f_phone, s_f.ip, 6977))


def test_MT11_personal_tailnet_cannot_reach_device_link(bed):
    """A kernel-tun tailscaled on another tailnet (the person's own) reaches the host
    but NOT port 6977: nothing listens there, the loopback listener is served only onto
    the taOSgo node. Port 6969 is deliberately not asserted: the controller's existing
    0.0.0.0 bind is today's behaviour and out of scope for this layer."""
    host = bed.at("a-host1")
    personal_ip = bed.personal["a-host1"]
    # a listener on the host's kernel side so "refused" cannot mean "no path"
    bed.exec("a-host1", "sh", "-c",
             "nohup python3 -m http.server 7001 --bind 0.0.0.0 >/dev/null 2>&1 & echo $! >/tmp/dl-7001.pid")
    try:
        time.sleep(1.5)
        # control 1: the personal tailnet reaches the host's kernel address on a port that listens
        ok = bed.exec("personal", "nc", "-z", "-w", "5", personal_ip, "7001")
        assert ok.returncode == 0, f"the personal tailnet does not reach {personal_ip}: {ok.stderr}"
        ping = bed.exec("personal", "ping", "-c", "5", "-W", "2", personal_ip)
        assert "0% packet loss" in ping.stdout, ping.stdout
        # red: the same path to 6977 is refused
        red = bed.exec("personal", "nc", "-z", "-w", "5", personal_ip, "6977")
        assert red.returncode != 0, "port 6977 answered on the personal tailnet address"
    finally:
        bed.exec("a-host1", "sh", "-c", "kill $(cat /tmp/dl-7001.pid) 2>/dev/null; rm -f /tmp/dl-7001.pid")
    # control 2: from the taOSgo tailnet the same host's 6977 connects, after the above
    assert up(bed.reach(bed.at("a-phone"), host.ip, 6977))
    # and the coexistence held: the kernel instance is still healthy
    assert "0% packet loss" in bed.exec("personal", "ping", "-c", "5", "-W", "2", personal_ip).stdout


def test_MTU2_forwarded_traffic_is_never_console(bed):
    """Bed-level proof of the 5.0 rule. A relay request reaches the node on 6969 and is
    forwarded by `serve` to the per-person 27750+n listener, never to the controller's
    6969, so the console routes cannot see it as a loopback caller. (The full MTU2, with
    the controller's own 27750+n listener, belongs to T3; see test_device_link_multitenancy.)"""
    host = bed.at("a-host1")
    # red: through the relay path, /auth/lock-state is not answered as the console
    # the console header is sent on this leg too, so only the PEER decides the answer
    r = bed.via_relay(host, "GET", "/auth/lock-state", headers={CONSOLE_HEADER: "1"})
    assert r.status == 404, (r.status, r.body[:200])
    if STUBS:
        assert r.headers.get("x-bed-listener") == "a-host1:a:6969"  # it landed on 27750, not 6969
    cfg = bed.ts_json(host, "serve", "status", "--json")["TCP"]
    assert cfg["6969"]["TCPForward"] == "127.0.0.1:27750"
    # control: the same request shape from the real console path (loopback 6969 inside the
    # node, console header, no forwarding header; tinyagentos/routes/auth.py lock_state)
    # answers with exactly {"locked": bool, "session": int}
    c = bed.local(host, "GET", "/auth/lock-state", headers={CONSOLE_HEADER: "1"})
    assert c.status == 200 and "x-bed-listener" not in c.headers, (c.status, c.body[:200])
    assert set(c.json) == {"locked", "session"}, c.body


def test_the_policy_swap_lever_opens_and_closes_the_mesh(bed):
    """MT5 needs a deliberate misconfiguration (allow-all). Prove the lever works both
    ways here, so MT5 can trust it: closed, open, closed again."""
    a_host, b_host = bed.at("a-host1"), bed.at("b-host")
    assert not up(bed.reach(b_host, a_host.ip, 6977))  # repo policy: closed
    try:
        bed.policy("allow-all")
        deadline = time.time() + 30
        opened = False
        while time.time() < deadline and not opened:
            opened = up(bed.reach(b_host, a_host.ip, 6977, wait=5))
        assert opened, "allow-all did not open the mesh within 30 s"
    finally:
        bed.policy("repo")
    deadline = time.time() + 30
    closed = False
    while time.time() < deadline and not closed:
        closed = not up(bed.reach(b_host, a_host.ip, 6977, wait=5))
    assert closed, "restoring the repo policy did not close the mesh within 30 s"
    assert up(bed.reach(bed.at("a-phone"), a_host.ip, 6977))  # and A's own path never broke
