"""The lock-screen USB-C mode switch (/auth/lock-usb).

Modelled on the radio tests in test_lock_power_menu.py. The privileged half is
the root helper behind the drop box; here the drop box is a tmp file and the
read-back is stubbed.
"""
from __future__ import annotations

import subprocess

import pytest

import tinyagentos.routes.auth as auth
from tinyagentos.auth_middleware import EXEMPT_PATHS

from test_lock_power_menu import _Req, _body, _call, _no_sleep

SAMPLE = (
    "mode: ncm\n"
    "udc: configured\n"
    "saved: ncm\n"
    "address: 172.16.42.1 (host gets 172.16.42.2 by DHCP)\n"
)
ALL = {"mode": "ncm", "saved": "ncm", "available": ["ncm", "charging", "mtp"]}


@pytest.fixture
def box(monkeypatch, tmp_path):
    target = tmp_path / "request"
    monkeypatch.setattr(auth, "_POWER_REQUEST", str(target))
    monkeypatch.setattr(auth, "_request_is_console", lambda _r: True)
    monkeypatch.setattr(auth.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(auth, "_read_usb", lambda: dict(ALL))
    return target


# --- malformed input first -------------------------------------------------

@pytest.mark.parametrize("mode", ["rndis", "adb", "", 1, None, ["ncm"], "NCM", "ncm "])
def test_unknown_modes_are_refused_and_nothing_is_written(box, mode):
    resp = _call(auth.set_lock_usb(_Req({"mode": mode})))
    assert resp.status_code == 400
    assert not box.exists()


def test_a_missing_mode_key_is_refused(box):
    assert _call(auth.set_lock_usb(_Req({}))).status_code == 400
    assert not box.exists()


def test_a_mode_not_available_on_this_phone_is_refused(box, monkeypatch):
    monkeypatch.setattr(
        auth, "_read_usb",
        lambda: {"mode": "ncm", "available": ["ncm", "charging"]})
    resp = _call(auth.set_lock_usb(_Req({"mode": "mtp"})))
    assert resp.status_code == 400
    assert not box.exists()


def test_an_empty_available_list_refuses_everything(box, monkeypatch):
    monkeypatch.setattr(auth, "_read_usb", lambda: {"available": []})
    assert _call(auth.set_lock_usb(_Req({"mode": "ncm"}))).status_code == 400
    assert not box.exists()


def test_non_console_is_refused_for_get_and_post(box, monkeypatch):
    monkeypatch.setattr(auth, "_request_is_console", lambda _r: False)
    assert _call(auth.lock_usb(_Req())).status_code == 403
    assert _call(auth.set_lock_usb(_Req({"mode": "ncm"}))).status_code == 403
    assert not box.exists()


def test_a_missing_drop_box_is_a_503(box, monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "_POWER_REQUEST", str(tmp_path / "nope" / "request"))
    assert _call(auth.set_lock_usb(_Req({"mode": "ncm"}))).status_code == 503


def test_exempt_path():
    assert "/auth/lock-usb" in EXEMPT_PATHS


# --- the verbs -------------------------------------------------------------

@pytest.mark.parametrize("mode,verb", [
    ("ncm", "usb-ncm"), ("charging", "usb-charging"), ("mtp", "usb-mtp"),
])
def test_each_mode_writes_exactly_its_verb(box, monkeypatch, mode, verb):
    monkeypatch.setattr(auth, "_read_usb", lambda: {"mode": mode, "available": list(ALL["available"])})
    resp = _call(auth.set_lock_usb(_Req({"mode": mode})))
    assert resp.status_code == 200
    assert box.read_text() == verb


def test_the_verb_map_is_closed():
    assert auth._USB_VERBS == {
        "ncm": "usb-ncm", "charging": "usb-charging", "mtp": "usb-mtp"}


# --- the answer is the read-back -------------------------------------------

def test_the_answer_is_the_read_back_not_the_request(box, monkeypatch):
    """Asks for mtp while the reader insists on ncm: the refusal must win."""
    sleeps = []

    async def counting(s):
        sleeps.append(s)

    monkeypatch.setattr(auth.asyncio, "sleep", counting)
    got = _body(_call(auth.set_lock_usb(_Req({"mode": "mtp"}))))
    assert got["mode"] == "ncm", got
    assert len(sleeps) > 1  # it polled rather than reading once


def test_it_polls_until_the_mode_lands(box, monkeypatch):
    reads = iter([dict(ALL)] + [dict(ALL)] * 3
                 + [{"mode": "charging", "available": ["ncm", "charging"]}])
    monkeypatch.setattr(auth, "_read_usb", lambda: next(reads))
    got = _body(_call(auth.set_lock_usb(_Req({"mode": "charging"}))))
    assert got["mode"] == "charging"


# --- the reader ------------------------------------------------------------

class _Done:
    def __init__(self, out, code=0):
        self.stdout, self.returncode = out, code


def _fake_run(listing="ncm charging mtp\n", status=SAMPLE):
    def run(cmd, **_k):
        return _Done(listing if cmd[-1] == "list" else status)
    return run


def test_parses_the_sample_output(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run())
    assert auth._read_usb() == ALL


def test_mtp_missing_from_the_list(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(listing="ncm charging\n"))
    assert auth._read_usb()["available"] == ["ncm", "charging"]


def test_other_modes_read_as_unknown(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(status="mode: rndis\nsaved: ???\n"))
    got = auth._read_usb()
    assert got["mode"] == "unknown" and got["saved"] == "unknown"


def test_a_missing_binary_gives_empty_available(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("taos-usb-mode")
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(auth, "_request_is_console", lambda _r: True)
    assert auth._read_usb() == {"available": []}
    resp = _call(auth.lock_usb(_Req()))
    assert resp.status_code == 200
    assert _body(resp) == {"available": []}


def test_a_failing_binary_gives_empty_available(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Done("", 1))
    assert auth._read_usb() == {"available": []}


def test_a_timeout_gives_empty_available(monkeypatch):
    def slow(*a, **k):
        raise subprocess.TimeoutExpired("taos-usb-mode", 4)
    monkeypatch.setattr(subprocess, "run", slow)
    assert auth._read_usb() == {"available": []}


# --- the page --------------------------------------------------------------

def test_the_page_fetches_and_labels_the_usb_tile():
    js = auth._LOCK_SCREEN_SCRIPT
    assert '"/auth/lock-usb"' in js
    for label in ("USB network", "Charge only", "File transfer"):
        assert label in js


@pytest.mark.parametrize("mode", ["rndis", "adb"])
def test_the_closed_map_refuses_even_if_the_reader_offers_the_mode(box, monkeypatch, mode):
    """The `available` check must not be the only wall: a reader that listed an
    unknown mode would otherwise let "usb-"+mode reach the root helper."""
    monkeypatch.setattr(auth, "_read_usb",
                        lambda: {"mode": "ncm", "available": ["ncm", mode]})
    assert _call(auth.set_lock_usb(_Req({"mode": mode}))).status_code == 400
    assert not box.exists()


# --- follow-up to #3328: partial reads and the wall-clock bound -------------


def test_a_failing_list_keeps_the_mode_already_read(monkeypatch):
    """The second helper call raising must not throw away the first's answer."""
    def run(cmd, **_k):
        if cmd[-1] == "list":
            raise subprocess.TimeoutExpired("taos-usb-mode", 4)
        return _Done(SAMPLE)
    monkeypatch.setattr(subprocess, "run", run)
    assert auth._read_usb() == {"mode": "ncm", "saved": "ncm", "available": []}


def test_a_slow_helper_cannot_hold_the_switch_past_the_deadline(box, monkeypatch):
    """Every read costs 3s of clock and the mode never lands. Counting
    iterations alone would read 24 times (~84s); the deadline stops it at 12s."""
    now = [0.0]
    reads = []

    async def tick(s):
        now[0] += s

    def slow_read():
        reads.append(now[0])
        now[0] += 3.0
        return dict(ALL)

    monkeypatch.setattr(auth, "_usb_clock", lambda: now[0])
    monkeypatch.setattr(auth.asyncio, "sleep", tick)
    monkeypatch.setattr(auth, "_read_usb", slow_read)
    got = _body(_call(auth.set_lock_usb(_Req({"mode": "mtp"}))))
    assert got["mode"] == "ncm"
    reads.pop(0)  # the availability check, which ends at t=3
    assert 1 < len(reads) < 24, reads
    assert all(t < 3.0 + auth._USB_SETTLE_SECONDS for t in reads), reads


def test_a_hung_read_is_abandoned_at_the_deadline(box, monkeypatch):
    """A read that never returns: the route answers on time with the last
    read-back it has, instead of waiting out the helper."""
    import threading
    import time

    release = threading.Event()
    calls = []

    def hung_read():
        calls.append(1)
        if len(calls) > 1:
            release.wait(5)
        return dict(ALL)

    async def timed():
        # Timed inside the loop: asyncio.run's shutdown joins the abandoned
        # thread, which a live server's loop never does.
        start = time.monotonic()
        try:
            resp = await auth.set_lock_usb(_Req({"mode": "mtp"}))
        finally:
            elapsed = time.monotonic() - start
            release.set()
        return resp, elapsed

    monkeypatch.setattr(auth, "_USB_SETTLE_SECONDS", 0.3)
    monkeypatch.setattr(auth, "_read_usb", hung_read)
    resp, elapsed = _call(timed())
    assert elapsed < 2.0, elapsed
    assert _body(resp)["mode"] == "ncm"
