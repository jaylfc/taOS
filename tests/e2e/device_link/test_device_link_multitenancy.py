"""Section 11.3: multi-tenancy tests that need code not written yet (two accounts,
each layer alone), including the shared-computer cases SC2 to SC8 and the two
loopback cases MTU1 and MTU2 in their full form.

The pure-network layer (MT3, MT4, MT11, SC1, and the bed-level MTU2) is REAL and
lives in test_device_link_mesh_isolation.py. Everything here is
xfail(strict=True) until the PR in its marker.

Mutation checks (run by hand in a scratch branch, see the README): for MT5, MT6 and
MT9 remove the guard and the red must turn green-for-the-attacker, so the test fails.
"""
from __future__ import annotations

from _device_link_bed import CONSOLE_HEADER, expect, needs
from _device_link_flows import (
    LINK, device_id, enrol_first_device, enrol_later_device, forge_proof, forged_envelope, hello, lock_state, my_devices,
    as_member, controller_token, roster_ids, session_for, token, MY,
)


def two_people(bed):
    """Person A: a-host1 + a-phone. Person B: b-host + b-phone."""
    a1, ap, b, bp = bed.at("a-host1"), bed.at("a-phone"), bed.at("b-host"), bed.at("b-phone")
    enrol_first_device(bed, a1)
    enrol_later_device(bed, ap, a1)
    enrol_first_device(bed, b)
    enrol_later_device(bed, bp, b)
    return a1, ap, b, bp


# -- taos.my layer --------------------------------------------------------
@needs("M1")
def test_MT1_taosmy_roster_is_per_account(bed):
    a1, ap, b, bp = two_people(bed)
    a_did = device_id(bed, ap)
    unknown = bed.my_http("PATCH", "/api/roster/devices/00000000-0000-0000-0000-000000000000", {"display_name": "x"}, headers=session_for(bed, "B"))
    assert a_did not in roster_ids(bed, b)                                   # red: B lists no A device id
    cross = bed.my_http("PATCH", f"/api/roster/devices/{a_did}", {"display_name": "x"}, headers=session_for(bed, "B"))
    assert (cross.status, cross.body) == (unknown.status, unknown.body) == (404, unknown.body)   # byte-identical to an unknown id
    expect(bed.my_http("PATCH", f"/api/roster/devices/{a_did}", {"display_name": "x"}, headers=session_for(bed, "A")), 200)   # control


@needs("M1")
def test_MT2_taosmy_approve_is_per_account(bed):
    a1, ap, b, bp = two_people(bed)
    a2 = bed.at("a-host2")
    from _device_link_flows import request_join
    j = expect(request_join(bed, a2), 200).json
    expect(bed.my_http("POST", f"/api/cluster/join/requests/{j['request_id']}/approve", headers=session_for(bed, "B")), 404)
    expect(bed.my_http("POST", f"/api/cluster/join/requests/{j['request_id']}/approve", headers=session_for(bed, "A")), 200)


# -- app layer, mesh opened ----------------------------------------------
@needs("T3")
def test_MT5_app_layer_alone_with_the_mesh_open(bed):
    a1, ap, b, bp = two_people(bed)
    bed.policy("allow-all")                                                  # a deliberate misconfiguration
    try:
        bed.sleep(120)
        expect(token(bed, bp, a1, key="own"), 404, "unknown_device")         # red: a VALID B proof, mesh reaches, app refuses
        expect(token(bed, ap, a1), 200)                                      # control: a-phone
    finally:
        bed.policy("repo")


@needs("T4 and T5")
def test_MT6_a_local_member_sees_nothing_mirrored(bed):
    a1, ap, b, bp = two_people(bed)
    member = as_member(bed, a1, "m")                                         # a local member of a-host1 who is not linked
    items = bed.local(a1, "GET", "/api/notifications", headers=member).json["items"]
    assert not [i for i in items if str(i.get("id", "")).startswith("mirror:")]    # red: no mirror: rows
    expect(bed.local(a1, "GET", MY, headers=member), 404, "not_linked")
    assert my_devices(bed, a1).status == 200                                 # control: the linked user sees the feature


@needs("T3")
def test_MT7_tailnet_member_without_a_roster_key(bed):
    a1, ap, b, bp = two_people(bed)
    a3 = bed.at("a-host3")                                                   # joined with an app-join key (mesh only), never enrolled
    expect(token(bed, a3, a1), 404, "unknown_device")                        # red
    expect(token(bed, ap, a1), 200)                                          # control


@needs("T3")
def test_MT8_forged_or_replayed_envelope(bed):
    a1, ap, b, bp = two_people(bed)
    forged = forged_envelope(bed, sender=bp, claimed_from=ap, to=a1)
    expect(bed.link(bp, a1, "POST", f"{LINK}/notifications", forged), 403, "bad_signature")      # red 1
    good = forged_envelope(bed, sender=ap, claimed_from=ap, to=a1)
    expect(bed.link(ap, a1, "POST", f"{LINK}/notifications", good), 200)
    expect(bed.link(ap, a1, "POST", f"{LINK}/notifications", good), 409, "replay")               # red 2: replay
    expect(bed.link(ap, a1, "POST", f"{LINK}/notifications", forged_envelope(bed, ap, ap, a1)), 200)   # control: a fresh one


@needs("M2")
def test_MT9_compromised_taosmy_cannot_swap_a_key(bed):
    a1, ap, b, bp = two_people(bed)
    did = device_id(bed, ap)
    bed.exec("taosmy", "python3", "-c",
             f"import sqlite3;c=sqlite3.connect('/data/accounts.db');"
             f"c.execute(\"update hosts set pub_ed25519='harness' where device_id='{did}'\");c.commit()")
    bed.sleep(120)
    expect(token(bed, ap, a1, key="harness"), 404)                           # red: controllers refuse (endorsement_invalid)
    assert did in roster_ids(bed, a1)                                        # the device stays as it was
    a3 = bed.at("a-host3")
    enrol_later_device(bed, a3, a1)                                          # control: a properly endorsed new device is accepted
    expect(token(bed, a3, a1), 200)


# -- listener bind and loopback ------------------------------------------
@needs("T3")
def test_MT10_listener_bind(bed):
    a1, ap, b, bp = two_people(bed)
    bridge = bed.exec(a1.node, "sh", "-c", "hostname -i").stdout.split()[0]
    for port in (a1.listener_port, 6977):                                    # red: not on the container bridge address
        r = bed.exec(a1.node, "sh", "-c", f"nc -z -w 3 {bridge} {port}")
        assert r.returncode != 0, port
    expect(bed.link(ap, a1, "GET", f"{LINK}/hello"), 401)                    # control: 6977 on the taOSgo node connects (answered, no token)


@needs("T3")
def test_MTU1_loopback_is_not_trust(bed):
    a1, ap, b, bp = two_people(bed)
    a_dev = device_id(bed, ap)
    local = lambda m, p, b=None, h=None: bed.http(a1, f"http://127.0.0.1:{a1.listener_port}{p}", m, b, h)
    expect(local("POST", f"{LINK}/token", {"aud_did": a_dev}, forge_proof(bed, a1, a1, key="made-up")), 404, "unknown_device")
    expect(local("GET", f"{LINK}/hello", None, {"Authorization": "Bearer local-token"}), 401)
    other = bed.at("shared-s/f")                                             # a valid A-phone proof sent to person F's port
    r = bed.http(a1, f"http://127.0.0.1:{27701}{LINK}/token", "POST", {"aud_did": a_dev}, forge_proof(bed, ap, other))
    expect(r, 403, "wrong_node")
    expect(token(bed, ap, a1), 200)                                          # control: through A's node and serve with a valid proof


@needs("T3")
def test_MTU2_forwarded_is_never_console_controller_listener(bed):
    """Full form: the CONTROLLER's own 27750+n listener, not the bed stand-in."""
    a1, ap, b, bp = two_people(bed)
    r = bed.via_relay(a1, "GET", "/auth/lock-state", headers={CONSOLE_HEADER: "1"})
    assert r.status == 404 and "x-bed-listener" not in r.headers             # answered by the controller, not the stub
    for unlock in ("/auth/unlock", "/auth/pin-login", "/auth/swipe-unlock"):
        assert bed.via_relay(a1, "POST", unlock, {}).status == 404
    assert lock_state(bed, a1)["locked"] is not None                         # control: the real console answers 200


# -- shared computer S: person A owns, person F is an invited member -----
def shared(bed):
    s_a, s_f, ap, fp = bed.at("shared-s/a"), bed.at("shared-s/f"), bed.at("a-phone"), bed.at("f-phone")
    enrol_first_device(bed, s_a)
    enrol_later_device(bed, ap, s_a)
    enrol_first_device(bed, s_f)
    enrol_later_device(bed, fp, s_f)
    return s_a, s_f, ap, fp


@needs("T9")
def test_SC2_app_check_picks_the_person(bed):
    s_a, s_f, ap, fp = shared(bed)
    expect(bed.link(ap, s_a, "POST", f"{LINK}/token", {"aud_did": device_id(bed, s_f)}, forge_proof(bed, ap, s_a)), 404, "unknown_device")
    expect(bed.link(ap, s_a, "POST", f"{LINK}/token", {"aud_did": device_id(bed, s_a)}, forge_proof(bed, ap, s_a)), 200)


@needs("T9")
def test_SC3_arriving_node_check_alone(bed):
    """Needs a scratch build of the controller with the roster check removed; the
    red must still be 403 wrong_node, which proves this layer without the app check."""
    s_a, s_f, ap, fp = shared(bed)
    expect(bed.link(ap, s_a, "POST", f"{LINK}/token", {"aud_did": device_id(bed, s_f)}, forge_proof(bed, ap, s_a)), 403, "wrong_node")
    expect(bed.link(fp, s_f, "POST", f"{LINK}/token", {"aud_did": device_id(bed, s_f)}, forge_proof(bed, fp, s_f)), 200)


@needs("T5 and T9")
def test_SC4_data_on_s_is_per_person(bed):
    s_a, s_f, ap, fp = shared(bed)
    ids = lambda ep, who: [i["id"] for i in bed.local(ep, "GET", "/api/notifications", headers=as_member(bed, ep, who)).json["items"]]
    assert not [i for i in ids(s_f, "f") if str(i).startswith("mirror:")]    # red: F sees none of a-phone's rows, nor events
    assert [i for i in ids(s_a, "a") if str(i).startswith("mirror:")]        # control: A on S sees them


@needs("T9")
def test_SC5_os_layer_on_s(bed):
    s_a, s_f, ap, fp = shared(bed)
    key = "/var/lib/taos/mesh/a/device.key"
    red = bed.exec("shared-s", "cat", key, as_user="p_f")
    assert red.returncode != 0 and "ermission" in red.stderr
    sock = bed.exec("shared-s", "tailscale", "--socket=/run/taos/mesh/a.sock", "status", as_user="p_f")
    assert sock.returncode != 0 and "ermission" in sock.stderr
    assert bed.exec("shared-s", "cat", key, as_user="p_a").returncode == 0   # control: as A's own Linux user


@needs("T9")
def test_SC6_relay_two_handles_on_one_computer(bed):
    s_a, s_f, ap, fp = shared(bed)
    expect(bed.my_http("GET", f"/api/relay/authorize?handle={s_f.hostname}", headers=session_for(bed, "A")), 403)   # red
    expect(bed.my_http("GET", f"/api/relay/authorize?handle={s_f.hostname}", headers=session_for(bed, "F")), 200)   # control


@needs("T9")
def test_SC7_per_person_removal(bed):
    s_a, s_f, ap, fp = shared(bed)
    expect(bed.my_http("DELETE", f"/api/roster/devices/{device_id(bed, s_a)}", headers=session_for(bed, "A")), 200)
    bed.sleep(60)
    expect(token(bed, ap, s_a), 403)                                         # red: A's device on S is refused
    assert s_a.hostname not in {n["name"] for n in bed.hs_nodes()}           # and A's node on S is deleted
    expect(token(bed, fp, s_f), 200)                                         # control: F's device on S keeps working
    assert s_f.hostname in {n["name"] for n in bed.hs_nodes()}


@needs("M2")
def test_SC8_family_membership_ends(bed):
    s_a, s_f, ap, fp = shared(bed)
    expect(bed.my_http("POST", "/api/account/family/leave", headers=session_for(bed, "F")), 200)   # route shape: the family add-on's
    bed.sleep(120)
    expect(token(bed, fp, s_f), 403)                                         # red: F's tokens, relay route and node are gone
    assert s_f.hostname not in {n["name"] for n in bed.hs_nodes()}
    expect(token(bed, ap, s_a), 200)                                         # control: A on S unaffected, same run
