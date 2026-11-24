"""Flows used by the not-yet-passing device-link tests.

Written from the design's route tables (sections 4.1, 5.1, 5.2); the PR that owns
a route adjusts the request shapes here when it lands, and that is the moment its
xfail markers come off. Anything the bed cannot yet do on its own (read a mailed
code, sign a device-key proof, tap an envelope) is a function that FAILS with the
PR that has to provide it, so no test can pass by skipping the step.
"""
from __future__ import annotations

import pytest

from _device_link_bed import CONSOLE_HEADER, Bed, Endpoint, Resp, expect

LINK = "/api/device-link"
MY = "/api/my-devices"


def missing(what: str, pr: str):
    pytest.fail(f"bed capability not built yet: {what} (comes with {pr})", pytrace=False)


# -- things the bed does not do yet ---------------------------------------
def account_password(bed: Bed, ep: Endpoint) -> str:
    """The password of the seeded account for ep's person. opcli seeds a RANDOM one, so
    the bed has to set a known one through the reset flow first."""
    missing("a known password for a seeded account (opcli sets a random one)", "M1")


def session_for(bed: Bed, who: str) -> dict:
    """Headers of a signed-in taos.my session for person `who` (A, B or F)."""
    missing("a signed-in taos.my session for a seeded account", "M1")


def controller_token(bed: Bed, ep: Endpoint, scope: str) -> dict:
    """Authorization headers: a taos.my controller token with `scope` for ep's host."""
    missing("minting a taos.my controller token for a roster device", "M2")


def as_member(bed: Bed, ep: Endpoint, name: str) -> dict:
    """Headers of a local, non-linked member `name` signed in on ep's controller."""
    missing("a local non-linked member session on a controller", "T4")


def inject_lock_state(bed: Bed, ep: Endpoint, answer: str) -> None:
    """Make the phone component read `answer` from the lock-state route (L8)."""
    missing("a way to feed the phone component a chosen lock-state answer", "T6")


def mailed_code(bed: Bed) -> str:
    missing("a mail sink to read the emailed one-time code", "M1")


def mail_count(bed: Bed) -> int:
    missing("a mail sink to count messages", "M1")


def forge_proof(bed: Bed, signer: Endpoint, audience: Endpoint, *, key: str = "own") -> dict:
    """A device-key proof header set for `signer` calling `audience`. key='made-up'
    signs with a key the roster does not know; 'harness' with a harness-held key."""
    missing("a device-key signer for requests", "T3")


def forged_envelope(bed: Bed, sender: Endpoint, claimed_from: Endpoint, to: Endpoint, **fields) -> dict:
    missing("a signed and sealed envelope builder", "T3")


def plaintext_tap(bed: Bed, ep: Endpoint):
    missing("a test-only plaintext tap on a node's 6977 listener", "T5")


def console_lock(bed: Bed, ep: Endpoint, locked: bool) -> None:
    """Drive the console lock/unlock through the controller's real console routes, from
    inside the container (loopback + console header). INFERRED feasible; T0 left the
    exact calls to T1, which owns the remote-connection lock."""
    missing("console lock/unlock driven from inside the container", "T1")


# -- my-devices (local UI surface, 6969) -----------------------------------
def my_devices(bed: Bed, ep: Endpoint) -> Resp:
    return bed.local(ep, "GET", MY)


def roster_ids(bed: Bed, ep: Endpoint) -> list[str]:
    r = expect(my_devices(bed, ep), 200)
    return [d["device_id"] for d in r.json["devices"]]


def device_id(bed: Bed, ep: Endpoint) -> str:
    return expect(my_devices(bed, ep), 200).json["this_device_id"]


def start_first_device(bed: Bed, ep: Endpoint, password: str | None = None) -> Resp:
    pw = password if password is not None else account_password(bed, ep)
    return bed.local(ep, "POST", f"{MY}/enrol", {"mode": "first_device", "password": pw})


def complete_first_device(bed: Bed, ep: Endpoint, code: str) -> Resp:
    return bed.local(ep, "POST", f"{MY}/enrol/code", {"code": code})


def enrol_first_device(bed: Bed, ep: Endpoint) -> None:
    expect(start_first_device(bed, ep), 200)
    expect(complete_first_device(bed, ep, mailed_code(bed)), 200)


def request_join(bed: Bed, ep: Endpoint, *, ephemeral: bool = False) -> Resp:
    return bed.local(ep, "POST", f"{MY}/enrol", {"mode": "join", "ephemeral": ephemeral})


def approve(bed: Bed, ep: Endpoint, request_id: str, secret: str) -> Resp:
    return bed.local(ep, "POST", f"{MY}/join-requests/{request_id}/approve", {"pairing_secret": secret})


def enrol_later_device(bed: Bed, new: Endpoint, vouching: Endpoint) -> str:
    """Enrol `new` on the person's account, vouched by `vouching` (an active device)."""
    j = expect(request_join(bed, new), 200).json
    expect(approve(bed, vouching, j["request_id"], j["pairing_secret"]), 200)
    return device_id(bed, new)


def remove_device(bed: Bed, ep: Endpoint, did: str, password: str | None = None) -> Resp:
    pw = password if password is not None else account_password(bed, ep)
    return bed.local(ep, "DELETE", f"{MY}/{did}", {"password": pw})


# -- device-link (6977 through the mesh) ------------------------------------
def token(bed: Bed, src: Endpoint, dst: Endpoint, *, aud_did: str | None = None, key: str = "own") -> Resp:
    return bed.link(src, dst, "POST", f"{LINK}/token", {"aud_did": aud_did},
                    headers=forge_proof(bed, src, dst, key=key))


def hello(bed: Bed, src: Endpoint, dst: Endpoint, tok: str) -> Resp:
    return bed.link(src, dst, "GET", f"{LINK}/hello", headers={"Authorization": f"Bearer {tok}"})


def lock_state(bed: Bed, ep: Endpoint) -> dict:
    """The console's own answer, from inside the node (loopback, no forwarding header, with
    the console header): exactly {"locked": bool, "session": int}."""
    j = expect(bed.local(ep, "GET", "/auth/lock-state", headers={CONSOLE_HEADER: "1"}), 200).json
    assert set(j) == {"locked", "session"}, j
    return j
