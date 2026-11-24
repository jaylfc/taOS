"""Section 11.2: removal, recovery, expiry, revocation feed, token format.

All xfail(strict=True) until the PR named in each marker. Red and control sit in
the same test, same request shape.
"""
from __future__ import annotations

from _device_link_bed import expect, needs
from _device_link_flows import (
    LINK, controller_token, session_for, device_id, enrol_first_device, enrol_later_device, forge_proof, hello, remove_device, roster_ids, token,
)


def three_devices(bed):
    """a-host1 (first), a-host2 and a-phone enrolled; returns their endpoints."""
    a1, a2, ap = bed.at("a-host1"), bed.at("a-host2"), bed.at("a-phone")
    enrol_first_device(bed, a1)
    enrol_later_device(bed, a2, a1)
    enrol_later_device(bed, ap, a1)
    return a1, a2, ap


@needs("M2")
def test_RM1_controller_tokens_die_at_removal(bed):
    a1, a2, ap = three_devices(bed)
    routes = [("/api/roster/devices", "roster:read"), ("/api/roster/devices", "roster:write"), ("/api/taosnet/passkey", "taosnet:passkey")]
    expect(remove_device(bed, a1, device_id(bed, ap)), 200)
    for route, scope in routes:  # red: the removed phone's tokens on taos.my (minted before the removal)
        assert bed.my_http("GET", route, headers=controller_token(bed, ap, scope)).status == 401
    for route, scope in routes:  # control: a-host2's tokens on the same routes
        assert bed.my_http("GET", route, headers=controller_token(bed, a2, scope)).status == 200


@needs("T2")
def test_F7_removal_cuts_the_device_off(bed):
    a1, a2, ap = three_devices(bed)
    tok = token(bed, ap, a1).json["token"]
    did = device_id(bed, ap)
    expect(bed.local(a1, "POST", f"/api/my-devices/{did}/removal-request"), 200)
    expect(remove_device(bed, a2, did), 200)                                # the co-signer
    expect(hello(bed, ap, a1, tok), 403, "device_removed")                  # red: within 1 s on a-host1
    # control: a-host2's token on a-host1 still works
    tok2 = token(bed, a2, a1).json["token"]
    expect(hello(bed, a2, a1, tok2), 200)


@needs("T2")
def test_F8_removal_while_the_account_service_is_down(bed):
    a1, a2, ap = three_devices(bed)
    a3 = bed.at("a-host3")
    enrol_later_device(bed, a3, a1)
    did = device_id(bed, ap)
    bed.stop("taosmy", "headscale")
    try:
        expect(remove_device(bed, a1, did), 200)
        expect(remove_device(bed, a2, did), 200)
        bed.sleep(120)
        # red: a third device learns it by tombstone gossip, with taos.my down
        assert did not in roster_ids(bed, a3)
    finally:
        bed.start("headscale", "taosmy")
    # control: taos.my receives the notice after it returns, and the node leaves Headscale
    bed.sleep(120)
    assert ap.hostname not in {n["name"] for n in bed.hs_nodes()}


@needs("T2")
def test_R1_one_device_cannot_remove(bed):
    a1, a2, ap = three_devices(bed)
    did = device_id(bed, ap)
    # red: a device.removed signed by ONE active device -> 403 not_permitted everywhere
    expect(bed.local(a1, "POST", "/api/device-link/roster-event", {"type": "device.removed", "device_id": did}), 403, "not_permitted")
    expect(token(bed, ap, a1), 200)                                         # the phone keeps working
    # control: a second, distinct active co-signer -> applied, refused within one beat
    expect(remove_device(bed, a2, did), 200)
    bed.sleep(60)
    expect(token(bed, ap, a1), 403)


@needs("T2")
def test_R2_co_signer_rules(bed):
    a1, a2, ap = three_devices(bed)
    a3 = bed.at("a-host3")
    enrol_later_device(bed, a3, a1)
    did = device_id(bed, ap)
    expect(bed.local(a1, "POST", f"/api/my-devices/{did}/removal-request"), 200)
    # red: co-signer = the requester, = the target, = an already removed device -> refused, nothing revoked
    for signer in (a1, ap):
        assert remove_device(bed, signer, did).status in (403, 409)
    expect(token(bed, ap, a1), 200)
    # control: a third active device co-signs -> applied
    expect(remove_device(bed, a3, did), 200)


@needs("T2")
def test_R3_offline_two_device_removal(bed):
    a1, a2, ap = three_devices(bed)
    did = device_id(bed, ap)
    bed.stop("taosmy", "headscale")
    try:
        expect(remove_device(bed, a1, did), 200)
        expect(remove_device(bed, a2, did), 200)
        for host in (a1, a2):                                               # red: refused on both
            expect(token(bed, ap, host), 403)
    finally:
        bed.start("headscale", "taosmy")
    bed.sleep(120)
    assert ap.hostname not in {n["name"] for n in bed.hs_nodes()}           # control: the node leaves Headscale


@needs("T2")
def test_R4_account_page_removal(bed):
    a1, a2, ap = three_devices(bed)
    b = bed.at("b-host")
    did = device_id(bed, ap)
    # red: an account-page session of account B on a-phone's id -> 404
    expect(bed.my_http("DELETE", f"/api/roster/devices/{did}", headers=session_for(bed, "B")), 404)
    # control: A's account-page session removes it, applied everywhere through the roster generation
    expect(bed.my_http("DELETE", f"/api/roster/devices/{did}", headers=session_for(bed, "A")), 200)
    bed.sleep(60)
    expect(token(bed, ap, a1), 403)


@needs("T2")
def test_R5_request_expiry(bed):
    a1, a2, ap = three_devices(bed)
    did = device_id(bed, ap)
    expect(bed.local(a1, "POST", f"/api/my-devices/{did}/removal-request"), 200)
    bed.sleep(24 * 3600 + 60)                                               # 24 h + 1 min, scaled
    expect(remove_device(bed, a2, did), 409)                                # red: expired request has no effect
    expect(token(bed, ap, a1), 200)
    expect(bed.local(a1, "POST", f"/api/my-devices/{did}/removal-request"), 200)   # control: fresh request
    expect(remove_device(bed, a2, did), 200)


@needs("M2")
def test_RC1_recovery_wait(bed):
    a1, a2, ap = three_devices(bed)
    expect(bed.my_http("POST", "/api/roster/recovery/confirm", headers=session_for(bed, "A")), 200)
    # red: before effective_at the roster is unchanged and old tokens still work
    assert len(roster_ids(bed, a1)) == 3
    expect(token(bed, a2, a1), 200)
    bed.sleep(24 * 3600 + 60)                                               # control: at effective_at everything old is gone
    assert roster_ids(bed, a1) == []
    expect(token(bed, a2, a1), 403)


@needs("M2")
def test_RC2_recovery_cancel(bed):
    a1, a2, ap = three_devices(bed)
    expect(bed.my_http("POST", "/api/roster/recovery/confirm", headers=session_for(bed, "A")), 200)
    expect(bed.local(a1, "POST", "/api/my-devices/recovery/cancel"), 200)   # red: cancelled -> nothing wiped
    bed.sleep(24 * 3600 + 60)
    assert len(roster_ids(bed, a1)) == 3
    expect(bed.my_http("POST", "/api/roster/recovery/confirm", headers=session_for(bed, "A")), 200)       # control: no cancel -> wiped
    bed.sleep(24 * 3600 + 60)
    assert roster_ids(bed, a1) == []


@needs("M2")
def test_RC3_recovery_notice(bed):
    a1, a2, ap = three_devices(bed)
    shows = lambda ep: bed.local(ep, "GET", "/api/my-devices").json.get("recovery_notice")
    assert not any(shows(e) for e in (a1, a2, ap))                          # red leg first: no recovery -> no notice
    expect(bed.my_http("POST", "/api/roster/recovery/confirm", headers=session_for(bed, "A")), 200)
    bed.sleep(60)
    assert all(shows(e) for e in (a1, a2, ap))                              # control: every reachable device shows it


@needs("T4")
def test_F9_expiry_policy(bed):
    a1, a2, ap = three_devices(bed)
    did = device_id(bed, ap)
    expect(bed.local(a1, "PATCH", f"/api/my-devices/{did}", {"expiry": {"policy": "at", "seconds_from_now": 60}}), 200)
    bed.sleep(61)
    expect(token(bed, ap, a1), 403, "key_expired")                          # red: the timed device
    expect(token(bed, a2, a1), 200)                                         # control: a never device, same jump


@needs("T2")
def test_ID1_one_revocation_feed(bed):
    a1, a2, ap = three_devices(bed)
    before = bed.local(a1, "GET", "/api/agents/registry/revoked")
    agents_before = before.json["agents"]
    did = device_id(bed, ap)
    expect(remove_device(bed, a1, did), 200)
    expect(remove_device(bed, a2, did), 200)
    after = expect(bed.local(a1, "GET", "/api/agents/registry/revoked"), 200).json
    entry = next(e for e in after["device_keys"] if e["did"] == did)
    assert entry["notice"]                                                  # red: the signed notice is in the feed
    assert after["agents"] == agents_before                                 # control: the agents list is unchanged


@needs("T3")
def test_ID2_one_token_format(bed):
    a1, a2, ap = three_devices(bed)
    pub = expect(bed.local(a1, "GET", "/api/agents/registry/pubkey"), 200).json
    tok = token(bed, ap, a1).json["token"]
    verify = lambda t: bed.exec(a1.node, "python3", "-c",
                                "import sys,json;from tinyagentos.registry import verify_token;print(verify_token(sys.argv[1],sys.argv[2]))", t, pub["public_key"])
    assert verify(tok).returncode == 0                                      # offline, from the pubkey only
    expect(remove_device(bed, a1, device_id(bed, ap)), 200)
    expect(remove_device(bed, a2, device_id(bed, ap)), 200)
    assert verify(tok).returncode != 0                                      # red: refused once its dkid is in the feed
    agent_tok = bed.local(a1, "POST", "/api/agents/registry/token", {"agent": "bed"}).json["token"]
    assert verify(agent_tok).returncode == 0                                # control: an agent token still verifies
