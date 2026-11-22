"""T0: tests of the bed itself. These are REAL tests: they need no device-link code.

They prove the bed is what the design says it is (sections 4.0, 5.0, 3.9, 11.1):
Headscale pinned and never expiring, taos.my seeded, one userspace tailscaled per
person with the `serve` mappings, no kernel state from the taOS instances.
"""
from __future__ import annotations

import os
import re

import pytest
import yaml

from _device_link_bed import HERE, STATE, SOCKS_BASE

STUBS = os.environ.get("TAOS_BED_STUBS", "1") != "0"
KERNEL_NODES = {"a-host1", "a-phone"}  # carry the personal-tailnet stand-in


def never_expires(expiry) -> bool:
    """Headscale prints 'never' as null, an empty value, or the zero time."""
    if expiry in (None, "", {}):
        return True
    if isinstance(expiry, dict):
        return not expiry.get("seconds")
    return str(expiry).startswith("0001-01-01")


def test_never_detector_can_fail():
    # The control for the expiry test: the detector must say "expires" for a real date.
    assert never_expires(None) and never_expires("0001-01-01T00:00:00Z")
    assert not never_expires({"seconds": 1900000000})
    assert not never_expires("2031-01-01T00:00:00Z")


def test_headscale_is_the_pinned_version(bed):
    out = bed.hs("version")
    assert "0.29.1" in out, f"taos.my deploys 0.29.1; the bed runs {out!r}"
    compose = yaml.safe_load((HERE / "compose.core.yml").read_text())
    assert compose["services"]["headscale"]["image"].endswith(":0.29.1")
    assert compose["services"]["headscale-personal"]["image"].endswith(":0.29.1")


def test_headscale_config_never_expires_and_has_embedded_derp():
    cfg = yaml.safe_load((HERE / "headscale/config.yaml").read_text())
    assert cfg["node"]["expiry"] == 0, "design 4.0: node.expiry: 0, set explicitly"
    assert cfg["derp"]["server"]["enabled"] is True, "0.29 refuses an empty DERP map"
    assert cfg["server_url"].startswith("http://"), "a client must accept a plain-http login server"
    assert cfg["policy"]["mode"] == "file"


def test_the_repo_policy_is_what_is_loaded():
    live = (STATE / "policy.hujson").read_text()
    repo = (STATE / "policy.repo.hujson").read_text()
    assert live == repo, "the bed must start on the taos.my repo's policy (a policy swap was not undone)"
    assert "autogroup:self" in live and "tag:relay" in live and "*:6969" in live


def test_taosmy_is_up_with_seeded_accounts(bed):
    assert bed.my_http("GET", "/healthz").status == 200
    for person, acct in bed.accounts.items():
        r = bed.sh("my", "user", "show", acct["email"])
        assert r.returncode == 0, f"{person}: {r.stdout}{r.stderr}"
        assert acct["email"] in r.stdout
    # control: the lookup really fails for a person that was never seeded
    r = bed.sh("my", "user", "show", "nobody@bed.test")
    assert r.returncode != 0


def test_every_node_is_registered_and_never_expires(bed):
    nodes = bed.hs_nodes()
    by_name = {n["name"]: n for n in nodes}
    expected = {e.hostname for e in bed.endpoints()}
    assert expected <= set(by_name), f"missing nodes: {expected - set(by_name)}"
    assert len(nodes) == len(expected), f"unexpected extra nodes: {set(by_name) - expected}"
    for name in sorted(expected):
        assert never_expires(by_name[name].get("expiry")), f"{name} has an expiry: {by_name[name].get('expiry')!r}"


def test_headscale_users_follow_the_taos_my_naming(bed):
    nodes = {n["name"]: n for n in bed.hs_nodes()}
    for e in bed.endpoints():
        if e.node == "relay":
            continue
        assert nodes[e.hostname]["user"]["name"] == f"taos-{e.account_id}", e.hostname
    # a tagged node is owned by its tag (design 3.9); the JSON field name is confirmed on the first run
    relay = nodes["taos-relay"]
    tags = relay.get("tags") or relay.get("forcedTags") or relay.get("validTags") or []
    assert "tag:relay" in tags, relay
    # one computer, two people: two nodes under two different Headscale users (design 3.9)
    s_a, s_f = bed.at("shared-s/a"), bed.at("shared-s/f")
    assert nodes[s_a.hostname]["user"]["name"] != nodes[s_f.hostname]["user"]["name"]
    # control: a person's own devices share one user
    assert nodes["a-host1"]["user"]["name"] == nodes["a-phone"]["user"]["name"]


def test_every_taos_instance_is_userspace_and_running(bed):
    for e in bed.endpoints():
        ps = bed.exec(e.node, "ps", "-eo", "user:20,args").stdout
        mine = [ln for ln in ps.splitlines() if f"--socket={e.sock}" in ln]
        assert len(mine) == 1, f"{e.node}/{e.person}: expected one tailscaled, saw {mine}"
        assert "--tun=userspace-networking" in mine[0]
        assert f"--socks5-server=localhost:{SOCKS_BASE + e.slot}" in mine[0]
        if e.person != "relay":
            assert mine[0].split()[0] == f"p_{e.person}", "the person's daemon runs as the person's Linux user"
        st = bed.ts_json(e, "status", "--json")
        assert st["BackendState"] == "Running", f"{e.node}/{e.person}: {st['BackendState']}"


def test_userspace_instances_leave_no_kernel_state(bed):
    # Measured in the two-tailscaled test: kernel instances share ip rule 5270 and
    # routing table 52. A userspace-only node must have neither.
    for node in ("a-host2", "a-host3", "b-host", "b-phone", "shared-s", "f-phone", "relay"):
        link = bed.exec(node, "ip", "-br", "link").stdout
        assert "tailscale0" not in link, f"{node}: a tun device exists"
        assert not re.search(r"\b5270:", bed.exec(node, "ip", "rule").stdout), f"{node}: ip rule 5270"
        assert bed.exec(node, "ip", "route", "show", "table", "52").stdout.strip() == "", f"{node}: table 52"
    # control: where a kernel-tun instance runs, the same probes DO see it
    for node in sorted(KERNEL_NODES):
        assert "tailscale0" in bed.exec(node, "ip", "-br", "link").stdout, node
        assert re.search(r"\b5270:", bed.exec(node, "ip", "rule").stdout), node


def test_serve_mappings_follow_section_5_0(bed):
    for e in bed.endpoints():
        if e.person == "relay":
            continue
        cfg = bed.ts_json(e, "serve", "status", "--json")
        tcp = cfg.get("TCP", {})
        assert tcp.get("6977", {}).get("TCPForward") == f"127.0.0.1:{27700 + e.slot}", (e.node, e.person, tcp)
        assert tcp.get("6969", {}).get("TCPForward") == f"127.0.0.1:{27750 + e.slot}", (e.node, e.person, tcp)
        # 6969 itself is never served onto the node (design 5.0, "forwarded is never console")
        assert all(v.get("TCPForward") != "127.0.0.1:6969" for v in tcp.values())
    # control: the relay stand-in has no serve config at all
    assert not bed.ts_json(bed.at("relay"), "serve", "status", "--json").get("TCP")


def test_a_shared_computer_has_one_slot_per_person(bed):
    a, f = bed.at("shared-s/a"), bed.at("shared-s/f")
    assert (a.slot, f.slot) == (0, 1)
    assert a.sock != f.sock
    ta = bed.ts_json(a, "serve", "status", "--json")["TCP"]
    tf = bed.ts_json(f, "serve", "status", "--json")["TCP"]
    assert ta["6977"]["TCPForward"] == "127.0.0.1:27700" and tf["6977"]["TCPForward"] == "127.0.0.1:27701"
    assert ta["6969"]["TCPForward"] == "127.0.0.1:27750" and tf["6969"]["TCPForward"] == "127.0.0.1:27751"
    assert a.ip != f.ip, "two people on one computer are two nodes with two addresses"


def test_a_socket_belongs_to_its_person(bed):
    # SC5's mechanism: F's Linux user cannot open A's mesh socket; A's can. (The
    # full SC5 also reads A's device key file, which needs T9.)
    probe = "python3 -c \"import socket,sys; s=socket.socket(socket.AF_UNIX); s.connect(sys.argv[1])\" "
    red = bed.exec("shared-s", "sh", "-c", probe + "/run/taos/mesh/a.sock", as_user="p_f")
    assert red.returncode != 0 and "ermission" in red.stderr, red.stderr
    ctl = bed.exec("shared-s", "sh", "-c", probe + "/run/taos/mesh/a.sock", as_user="p_a")
    assert ctl.returncode == 0, ctl.stderr


@pytest.mark.skipif(not STUBS, reason="stand-in listeners are off; the real listener answers instead")
def test_serve_forwards_reach_the_loopback_listener(bed):
    phone, host = bed.at("a-phone"), bed.at("a-host1")
    body = bed.reach(phone, host.ip, 6977)
    assert "stub:a-host1:a:6977" in body, body
    body = bed.reach(phone, host.ip, 6969)
    assert "stub:a-host1:a:6969" in body, body
    # and the shared computer's two people do not mix
    f_phone = bed.at("f-phone")
    assert "stub:shared-s:f:6977" in bed.reach(f_phone, bed.at("shared-s/f").ip, 6977)


def test_outbound_through_the_socks5_proxy(bed):
    # a-host2 has no kernel tun, so nothing but its own daemon can route a mesh address
    phone, host = bed.at("a-host2"), bed.at("a-host1")
    r = bed.http(phone, f"http://{host.ip}:6977/", via=phone)
    assert r.status == 404 and (not STUBS or r.headers.get("x-bed-listener") == "a-host1:a:6977"), (r.status, r.headers)
    # control: the same request WITHOUT the person's proxy cannot reach a mesh address
    # (a userspace node has no interface for it)
    r = bed.http(phone, f"http://{host.ip}:6977/", wait=6)
    assert r.status == 0, "a mesh address answered without going through the person's daemon"
