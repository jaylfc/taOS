"""Section 11.2: presence (F4 to F6) and remote-connection locking (SL1 to SL4).

xfail(strict=True) until the PR in each marker. Red and control share a test.
"""
from __future__ import annotations

from _device_link_bed import Counter, expect, needs
from _device_link_flows import (
    console_lock, device_id, enrol_first_device, enrol_later_device, lock_state, my_devices, session_for, token, MY,
)


def presence_of(bed, viewer, target):
    rows = {d["device_id"]: d for d in expect(my_devices(bed, viewer), 200).json["devices"]}
    return rows[device_id(bed, target)]["presence"]["state"]


def enrolled_pair(bed, a="a-host1", b="a-phone"):
    x, y = bed.at(a), bed.at(b)
    enrol_first_device(bed, x)
    enrol_later_device(bed, y, x)
    return x, y


@needs("T4")
def test_F4_presence_window(bed):
    a1, a2 = enrolled_pair(bed, "a-host1", "a-host2")
    assert presence_of(bed, a1, a2) == "online"                              # control: live beats -> Online
    bed.exec(a2.node, "sh", "-c", "kill -STOP $(pgrep -x taos)")             # stop beats (T4 supplies a cleaner switch)
    try:
        bed.sleep(50)
        assert presence_of(bed, a1, a2) == "online"                          # NOT before 60 s
        bed.sleep(50)
        assert presence_of(bed, a1, a2) == "offline"                         # Offline after 90 s
    finally:
        bed.exec(a2.node, "sh", "-c", "kill -CONT $(pgrep -x taos)")
    ap = bed.at("a-phone")
    enrol_later_device(bed, ap, a1)
    bed.exec(ap.node, "sh", "-c", "echo off >/bed-presence/screen")
    bed.sleep(930)
    assert presence_of(bed, a1, ap) == "asleep"                              # phone screen off -> Asleep inside 930 s


@needs("T4 and P2")
def test_F4b_phone_view_of_hosts(bed):
    a1, ap = enrolled_pair(bed)
    for _ in range(10 * 12):                                                 # control: sampled every 5 s for 10 min, never Offline
        assert presence_of(bed, ap, a1) == "online"
        bed.sleep(5)
    bed.exec(a1.node, "sh", "-c", "kill -STOP $(pgrep -x taos)")
    try:
        bed.sleep(120 + 30)
        assert presence_of(bed, ap, a1) == "offline"                         # red: Offline within one phone interval + 30 s
    finally:
        bed.exec(a1.node, "sh", "-c", "kill -CONT $(pgrep -x taos)")


@needs("T4")
def test_F5_hosts_never_beat_the_phone(bed):
    a1, ap = enrolled_pair(bed)
    seen = Counter(lambda: int(bed.exec(ap.node, "sh", "-c", "grep -c 'GET /api/device-link/beat' /var/log/bed/controller.log || true").stdout or 0))
    bed.sleep(600)
    assert seen.delta() == 0                                                 # red: no host request at a-phone:6977 in 10 min
    r = bed.link(ap, a1, "POST", "/api/device-link/beat", {})                # control: the phone's own beat reply carries host presence
    assert expect(r, 200).json["peers"]


@needs("T4")
def test_F6_taosmy_and_headscale_stopped(bed):
    a1, a2 = enrolled_pair(bed, "a-host1", "a-host2")
    bed.stop("taosmy", "headscale")
    try:
        did = device_id(bed, a2)
        expect(bed.local(a1, "PATCH", f"{MY}/{did}", {"name": "renamed"}), 503, "account_service_unreachable")   # red
        assert presence_of(bed, a1, a2) == "online"                          # control: everything on the existing tunnels still works
        expect(token(bed, a2, a1), 200)
    finally:
        bed.start("headscale", "taosmy")


@needs("T1")
def test_SL1_a_remote_connection_locks_the_console(bed):
    a1, b = bed.at("a-host1"), bed.at("b-host")
    console_lock(bed, a1, locked=False)
    state = lock_state(bed, a1)
    assert state["locked"] is False
    bed.http(b, f"http://{a1.ip}:6969/auth/login", "POST", {"username": "admin", "password": "x"})   # a foreign browser signs in over the LAN
    after = lock_state(bed, a1)
    assert after["locked"] is True and after["session"] == state["session"] + 1   # red leg: locked, session + 1
    console_lock(bed, a1, locked=False)
    assert lock_state(bed, a1)["locked"] is False                            # control: no new remote session -> stays unlocked


@needs("T1")
def test_SL2_own_devices_do_not_lock(bed):
    a1, ap = enrolled_pair(bed)
    console_lock(bed, a1, locked=False)
    before = lock_state(bed, a1)
    bed.sleep(600)                                                           # presence, mirroring, a device-bound UI session
    assert lock_state(bed, a1) == before                                     # same person's devices never lock each other
    bed.http(bed.at("b-host"), f"http://{a1.ip}:6969/auth/login", "POST", {"username": "admin", "password": "x"})
    assert lock_state(bed, a1)["locked"] is True                             # control: the foreign browser does lock it


@needs("T1")
def test_SL3_another_person_is_remote(bed):
    a1, ap = enrolled_pair(bed)
    b = bed.at("b-phone")
    console_lock(bed, a1, locked=False)
    expect(bed.link(b, a1, "GET", "/api/device-link/session"), 404)          # red: b-phone (or a non-active A device)
    assert lock_state(bed, a1)["locked"] is True
    console_lock(bed, a1, locked=False)
    expect(bed.link(ap, a1, "GET", "/api/device-link/session"), 200)         # control: a-phone, active, same person
    assert lock_state(bed, a1)["locked"] is False


@needs("M2")
def test_SL4_taosmy_new_login_exemption(bed):
    a1, ap = enrolled_pair(bed)
    first = session_for(bed, "A")                                            # a signed-in browser session
    session_for(bed, "A")                                                    # a second login from an unknown client
    expect(bed.my_http("GET", "/api/account/me", headers=first), 401, "session_locked")
    expect(token(bed, ap, a1), 200)                                          # control: device sessions of the same account lock nobody
    expect(token(bed, a1, ap), 200)
