"""Section 11.2: enrolment, first device, skip, resume, pairing secret, endorsement.

Every test here waits for code that is not written yet and is marked
xfail(strict=True) with the PR it waits for. Each carries its red (must be refused
or absent) and its control (must succeed, same request shape). The request shapes
are the design's; the owning PR adjusts them when it lands.
"""
from __future__ import annotations

from _device_link_bed import expect, needs
from _device_link_flows import (
    MY, account_password, approve, complete_first_device, controller_token, session_for, device_id, enrol_first_device,
    mail_count, mailed_code, request_join, roster_ids, start_first_device,
)


@needs("T1")
def test_F1_enrol_refuses_ephemeral(bed):
    ap, a1 = bed.at("a-phone"), bed.at("a-host1")
    enrol_first_device(bed, a1)
    # red: join with ephemeral:true
    expect(request_join(bed, ap, ephemeral=True), 400, "ephemeral_not_allowed")
    # control: the same with ephemeral:false
    j = expect(request_join(bed, ap, ephemeral=False), 200).json
    expect(approve(bed, a1, j["request_id"], j["pairing_secret"]), 200)
    node = next(n for n in bed.hs_nodes() if n["name"] == ap.hostname)
    assert not node.get("ephemeral", False)
    assert device_id(bed, ap) in roster_ids(bed, a1)


@needs("M1")
def test_FD1_first_device_only_on_an_empty_roster(bed):
    a1, a2 = bed.at("a-host1"), bed.at("a-host2")
    enrol_first_device(bed, a1)                       # one active device on the account
    mails = mail_count(bed)
    # red: a second first-device start -> 409, and nothing is mailed
    expect(start_first_device(bed, a2), 409, "roster_not_empty")
    assert mail_count(bed) == mails
    # control: an empty roster (person B) gets the code and becomes active
    b = bed.at("b-host")
    expect(start_first_device(bed, b), 200)
    expect(complete_first_device(bed, b, mailed_code(bed)), 200)
    assert len(roster_ids(bed, b)) == 1


@needs("M1")
def test_FD2_first_device_proof(bed):
    b = bed.at("b-host")
    mails = mail_count(bed)
    # red 1: wrong password -> 403, nothing mailed
    expect(start_first_device(bed, b, password="wrong-password"), 403)
    assert mail_count(bed) == mails
    # red 2: right password, wrong code five times -> 410 ticket_dead, no row, no node
    expect(start_first_device(bed, b), 200)
    for _ in range(4):
        expect(complete_first_device(bed, b, "000000"), 400)
    expect(complete_first_device(bed, b, "000000"), 410, "ticket_dead")
    assert roster_ids(bed, b) == []
    assert not [n for n in bed.hs_nodes() if n["name"] == b.hostname and n.get("registerMethod")]
    # control: right password + right code
    expect(start_first_device(bed, b), 200)
    expect(complete_first_device(bed, b, mailed_code(bed)), 200)


@needs("M1")
def test_FD3_first_device_race_has_one_winner(bed):
    ap, a1 = bed.at("a-phone"), bed.at("a-host1")
    # control first: a single device completes -> exactly one active
    expect(start_first_device(bed, a1), 200)
    code = mailed_code(bed)
    # red: two devices complete at once -> exactly one active, the other 409
    expect(start_first_device(bed, ap), 200)
    r1 = complete_first_device(bed, a1, code)
    r2 = complete_first_device(bed, ap, code)
    assert sorted([r1.status, r2.status]) == [200, 409]
    assert len(roster_ids(bed, a1)) == 1


@needs("M1")
def test_FD4_later_device_needs_a_vouch(bed):
    a1, ap = bed.at("a-host1"), bed.at("a-phone")
    enrol_first_device(bed, a1)
    nodes_before = len(bed.hs_nodes())
    # red: approved on the account page only, with no endorsement -> awaiting_vouch, no key
    j = expect(request_join(bed, ap), 200).json
    st = bed.my_http("GET", f"/api/cluster/join/requests/{j['request_id']}/poll")
    assert st.json.get("status") == "awaiting_vouch"
    assert len(bed.hs_nodes()) == nodes_before
    # control: vouched with the pairing secret -> key minted, active
    expect(approve(bed, a1, j["request_id"], j["pairing_secret"]), 200)
    assert device_id(bed, ap) in roster_ids(bed, a1)


@needs("T1")
def test_SK1_skip_means_no_enrolment_traffic(bed):
    a1 = bed.at("a-host1")
    expect(bed.local(a1, "POST", f"{MY}/enrol/skip"), 200)
    bed.sleep(600)
    # red side: no request from this container to taos.my or Headscale in 10 minutes of use
    assert bed.exec("taosmy", "sh", "-c", f"grep -c {a1.node} /var/log/taosmy-access.log || true").stdout.strip() == "0"
    # control: a node that did not skip is seen at taos.my
    other = bed.at("b-host")
    expect(start_first_device(bed, other), 200)
    assert bed.exec("taosmy", "sh", "-c", f"grep -c {other.node} /var/log/taosmy-access.log").stdout.strip() != "0"


@needs("T1")
def test_RS1_enrolment_is_resumable(bed):
    b = bed.at("b-host")
    expect(start_first_device(bed, b), 200)
    bed.exec(b.node, "sh", "-c", "pkill -f 'taos$' || true")      # controller restart (T1 pins the exact restart)
    r = bed.local(b, "GET", f"{MY}/enrol")
    assert r.json["state"] == "awaiting_code"
    expect(complete_first_device(bed, b, mailed_code(bed)), 200)
    # the ticket file holds no password
    grep = bed.exec(b.node, "grep", "-r", account_password(bed, b), "/data")
    assert grep.returncode == 1 and grep.stdout == ""
    # control: the same flow without leaving completes (person A)
    a = bed.at("a-host1")
    enrol_first_device(bed, a)


@needs("T1")
def test_RS2_ticket_limits(bed):
    b = bed.at("b-host")
    expect(start_first_device(bed, b), 200)
    code = mailed_code(bed)
    bed.sleep(31 * 60)                                            # past 30 min, scaled
    expect(complete_first_device(bed, b, code), 410, "ticket_expired")
    # a used ticket replayed
    expect(start_first_device(bed, b), 200)
    code = mailed_code(bed)
    expect(complete_first_device(bed, b, code), 200)
    expect(complete_first_device(bed, b, code), 410)
    # control: within 30 min it completes (person A)
    a = bed.at("a-host1")
    expect(start_first_device(bed, a), 200)
    expect(complete_first_device(bed, a, mailed_code(bed)), 200)


@needs("T1")
def test_F2_pairing_secret(bed):
    a1, ap = bed.at("a-host1"), bed.at("a-phone")
    enrol_first_device(bed, a1)
    j = expect(request_join(bed, ap), 200).json
    roster = roster_ids(bed, a1)
    # red: wrong secret -> 400, roster unchanged; the 6th try -> 410
    for _ in range(5):
        expect(approve(bed, a1, j["request_id"], "WRONG SECRET 1"), 400, "pairing_secret_mismatch")
    assert roster_ids(bed, a1) == roster
    expect(approve(bed, a1, j["request_id"], "WRONG SECRET 1"), 410, "request_dead")
    # control: a fresh request with the right secret -> active
    j = expect(request_join(bed, ap), 200).json
    expect(approve(bed, a1, j["request_id"], j["pairing_secret"]), 200)


@needs("T1")
def test_F3_endorsement_makes_a_device_active(bed):
    a1, ap = bed.at("a-host1"), bed.at("a-phone")
    enrol_first_device(bed, a1)
    j = expect(request_join(bed, ap), 200).json
    # red: approved from the account page only -> pending, its token is refused
    bed.my_http("POST", f"/api/cluster/join/requests/{j['request_id']}/approve", headers=session_for(bed, "A"))
    expect(bed.my_http("GET", "/api/roster/devices", headers=controller_token(bed, ap, "roster:read")), 403, "device_pending")
    # control: confirmed on a-host1 -> token 200
    expect(approve(bed, a1, j["request_id"], j["pairing_secret"]), 200)
    expect(bed.my_http("GET", "/api/roster/devices", headers=controller_token(bed, ap, "roster:read")), 200)


@needs("T1")
def test_MT9b_key_swap_at_enrolment(bed):
    a1, ap = bed.at("a-host1"), bed.at("a-phone")
    enrol_first_device(bed, a1)
    # the harness proxies a-phone's join request and swaps both keys for its own
    real = expect(request_join(bed, ap), 200).json
    swapped = bed.my_http("POST", "/api/cluster/join/request",       # M1.1 with the harness's own keys
                          {"device_name": "swap", "pub_ed25519": "harness", "pub_x25519": "harness", "key_mac": "guess"},
                          headers=session_for(bed, "A"))
    assert swapped.status == 200
    # red: a-phone's real secret on a-host1 -> 400, no endorsement
    expect(approve(bed, a1, real["request_id"], real["pairing_secret"]), 400, "pairing_secret_mismatch")
    # control: an unmodified request with the same secret is endorsed
    j = expect(request_join(bed, ap), 200).json
    expect(approve(bed, a1, j["request_id"], j["pairing_secret"]), 200)
