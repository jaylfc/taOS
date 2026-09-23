"""The charger-connected overlay: a coding-agent CLI homage for three seconds.

Product owner: "we need a charger connected notification, screen on and
animation for 3 seconds, we could emulate a claude code session where it shows
a percentage and a working message like 'Drinking the juice....'".

The device half already exists: the handset's session watcher POSTs
/auth/lock-charge with {"screen": "on"|"off"} on every plug-in and wakes a dark
panel 150 ms later. This file covers the route (a pre-auth, console-only
endpoint, so the hostile cases come first), the pure pieces of the animation
run under node, and the dark-panel flow: over a dark panel the terminal has to
be painted over PURE BLACK before the panel lights, or the first lit frame is a
flash of lock screen.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess

import pytest

import tinyagentos.routes.auth as auth
from tinyagentos.auth_middleware import EXEMPT_PATHS

from test_lock_screen_gestures import _balanced, _function
from test_lock_screen_repaint import _var


class _Req:
    def __init__(self, body=None, raw_error=False):
        self._body = body
        self._raw_error = raw_error

    async def json(self):
        if self._raw_error:
            raise ValueError("not json")
        return self._body


def _call(coro):
    return asyncio.run(coro)


def _listen():
    auth._LOCK_EVENT_WAITERS.clear()
    queue: asyncio.Queue = asyncio.Queue(maxsize=8)
    auth._LOCK_EVENT_WAITERS.add(queue)
    return queue


def _battery(root, name="battery", kind="Battery", capacity="73", status="Charging"):
    node = root / name
    node.mkdir()
    (node / "type").write_text(kind + "\n")
    if capacity is not None:
        (node / "capacity").write_text(capacity + "\n")
    if status is not None:
        (node / "status").write_text(status + "\n")
    return node


# ------------------------------------------------------------ hostile first


class TestTheRouteRefusesWhatItShould:
    def test_a_non_console_request_is_refused_and_pushes_nothing(self, monkeypatch):
        monkeypatch.setattr(auth, "_request_is_console", lambda _r: False)
        queue = _listen()
        try:
            resp = _call(auth.lock_charge(_Req({"screen": "on"})))
            assert resp.status_code == 403
            assert queue.empty(), "a refused request must not reach the page"
        finally:
            auth._LOCK_EVENT_WAITERS.clear()

    @pytest.mark.parametrize("body", [
        {}, {"screen": "dim"}, {"screen": ""}, {"screen": None}, {"screen": 1},
        {"screen": "ON"}, ["on"], "on", None,
    ])
    def test_a_bad_body_is_refused_and_pushes_nothing(self, monkeypatch, body):
        monkeypatch.setattr(auth, "_request_is_console", lambda _r: True)
        queue = _listen()
        try:
            resp = _call(auth.lock_charge(_Req(body)))
            assert resp.status_code == 400, body
            assert queue.empty(), body
        finally:
            auth._LOCK_EVENT_WAITERS.clear()

    def test_a_body_that_is_not_json_is_refused(self, monkeypatch):
        monkeypatch.setattr(auth, "_request_is_console", lambda _r: True)
        queue = _listen()
        try:
            assert _call(auth.lock_charge(_Req(raw_error=True))).status_code == 400
            assert queue.empty()
        finally:
            auth._LOCK_EVENT_WAITERS.clear()

    def test_the_caller_cannot_choose_the_percentage(self, monkeypatch, tmp_path):
        """The reading comes from sysfs. A pre-auth route that relayed a body's
        number would be a way to paint anything on the phone."""
        monkeypatch.setattr(auth, "_request_is_console", lambda _r: True)
        _battery(tmp_path, capacity="41")
        monkeypatch.setattr(auth, "_POWER_SUPPLY_DIR", str(tmp_path))
        queue = _listen()
        try:
            _call(auth.lock_charge(_Req({"screen": "on", "percent": 99,
                                         "status": "<b>x</b>"})))
            _kind, data = queue.get_nowait()
            assert data["percent"] == 41 and data["status"] == "Charging"
        finally:
            auth._LOCK_EVENT_WAITERS.clear()

    def test_it_is_exempt_from_the_session_gate(self):
        """It fires before sign-in, like every other lock route."""
        assert "/auth/lock-charge" in EXEMPT_PATHS


# ------------------------------------------------------------ the reading


class TestTheBatteryReading:
    @pytest.mark.parametrize("screen", ["on", "off"])
    def test_a_plug_in_pushes_the_sysfs_reading(self, monkeypatch, tmp_path, screen):
        monkeypatch.setattr(auth, "_request_is_console", lambda _r: True)
        # A charger node sorts FIRST and is not the battery: the reader must
        # pick by type, not by position.
        _battery(tmp_path, name="ac", kind="Mains", capacity="100", status=None)
        _battery(tmp_path, name="qcom-battery", capacity="73", status="Charging")
        monkeypatch.setattr(auth, "_POWER_SUPPLY_DIR", str(tmp_path))
        queue = _listen()
        try:
            resp = _call(auth.lock_charge(_Req({"screen": screen})))
            assert resp.status_code == 204
            kind, data = queue.get_nowait()
            assert kind == "charger"
            assert data == {"percent": 73, "status": "Charging", "screen": screen}
        finally:
            auth._LOCK_EVENT_WAITERS.clear()

    def test_no_battery_means_percent_null(self, monkeypatch, tmp_path):
        monkeypatch.setattr(auth, "_request_is_console", lambda _r: True)
        _battery(tmp_path, name="ac", kind="Mains", capacity="100")
        monkeypatch.setattr(auth, "_POWER_SUPPLY_DIR", str(tmp_path))
        queue = _listen()
        try:
            assert _call(auth.lock_charge(_Req({"screen": "off"}))).status_code == 204
            _kind, data = queue.get_nowait()
            assert data["percent"] is None and data["screen"] == "off"
        finally:
            auth._LOCK_EVENT_WAITERS.clear()

    def test_a_missing_directory_means_percent_null(self, tmp_path):
        got = auth._read_battery(str(tmp_path / "nope"))
        assert got == {"percent": None, "status": ""}

    def test_an_unreadable_capacity_is_null_not_zero(self, tmp_path):
        """0% would read as a phone about to die; nobody measured that."""
        _battery(tmp_path, capacity="garbage")
        assert auth._read_battery(str(tmp_path))["percent"] is None

    def test_an_odd_status_is_not_echoed(self, tmp_path):
        _battery(tmp_path, status="<script>")
        assert auth._read_battery(str(tmp_path))["status"] == ""

    def test_capacity_is_clamped(self, tmp_path):
        _battery(tmp_path, capacity="104")
        assert auth._read_battery(str(tmp_path))["percent"] == 100


# ------------------------------------------------------------ the animation


def _node(script: str, env: dict | None = None) -> dict:
    node = shutil.which("node")
    if node is None:  # pragma: no cover - depends on the runner image
        pytest.fail("node is required to execute the charge overlay source")
    done = subprocess.run([node, "-e", script], capture_output=True, text=True,
                          timeout=30, env={**os.environ, **(env or {})})
    assert done.returncode == 0, f"node failed:\n{done.stderr}"
    return json.loads(done.stdout)


def _pure() -> str:
    return "\n".join([
        _var("CHARGE_SLOGANS"), _var("CHARGE_SPIN"), _var("CHARGE_COUNT_MS"),
        _function("chargeCount"), _function("chargeSpinGlyph"),
        _function("pickSlogan"),
    ])


class TestTheCountUp:
    def _frames(self, pct, times):
        script = _pure() + """
var out = [];
var T = %s;
for (var i = 0; i < T.length; i++) out.push(chargeCount(%s, T[i]));
process.stdout.write(JSON.stringify(out));
""" % (json.dumps(times), json.dumps(pct))
        return _node(script)

    def test_it_starts_fifteen_short_and_lands_on_the_reading(self):
        frames = self._frames(73, [0, 1500, 3500, 7500, 7900])
        assert frames[0]["percent"] == 58
        assert frames[3]["percent"] == 73 and frames[4]["percent"] == 73
        shown = [f["percent"] for f in frames]
        assert shown == sorted(shown), "a count-up never goes backwards"
        assert 58 < frames[2]["percent"] < 73, "it moves in between"

    def test_it_never_starts_below_zero(self):
        assert self._frames(6, [0])[0]["percent"] == 0

    def test_the_bar_is_twenty_cells_and_fills_with_the_number(self):
        for f in self._frames(73, [0, 2500, 5000, 7500]):
            assert len(f["fill"]) + len(f["rest"]) == 20
            assert set(f["fill"]) <= {"█"} and set(f["rest"]) <= {"░"}
            assert len(f["fill"]) == round(f["percent"] / 5)
        assert len(self._frames(100, [7500])[0]["fill"]) == 20
        assert len(self._frames(0, [7500])[0]["fill"]) == 0

    def test_an_unread_battery_has_no_number_and_no_bar(self):
        assert self._frames(None, [0, 7500]) == [None, None]


class TestTheSpinnerAndSlogans:
    def test_the_spinner_ping_pongs(self):
        got = _node(_pure() + """
var out = [];
for (var i = 0; i < 12; i++) out.push(chargeSpinGlyph(i));
process.stdout.write(JSON.stringify(out));
""")
        assert got == ["·", "✢", "✳", "✶", "✻", "✽",
                       "✻", "✶", "✳", "✢", "·", "✢"]

    def test_a_slogan_never_repeats_back_to_back(self):
        """Over many draws, including the wrap after every slogan has been
        used -- the wrap is where a naive 'fresh pool' repeats the last one."""
        got = _node(_pure() + """
var seed = 7;
function rand() { seed = (seed * 16807) % 2147483647; return (seed - 1) / 2147483646; }
var used = [];
for (var i = 0; i < 400; i++) used.push(pickSlogan(CHARGE_SLOGANS, used, rand));
process.stdout.write(JSON.stringify({ used: used, n: CHARGE_SLOGANS.length }));
""")
        used, n = got["used"], got["n"]
        for a, b in zip(used, used[1:]):
            assert a != b, "the same slogan twice in a row"
        # One showing draws a handful; every slogan is used before any repeats.
        assert len(set(used[:n])) == n

    def test_a_worst_case_random_still_does_not_repeat(self):
        """rand() pinned at its extremes: the picker must not fall back onto
        the previous slogan when the pool is exhausted."""
        got = _node(_pure() + """
var out = [];
[0, 0.9999999].forEach(function (r) {
  var used = [];
  for (var i = 0; i < 40; i++) used.push(pickSlogan(CHARGE_SLOGANS, used, function () { return r; }));
  out.push(used);
});
process.stdout.write(JSON.stringify(out));
""")
        for used in got:
            for a, b in zip(used, used[1:]):
                assert a != b

    def test_the_slogans_are_the_product_owners(self):
        slogans = _node(_var("CHARGE_SLOGANS")
                        + "process.stdout.write(JSON.stringify(CHARGE_SLOGANS));")
        assert "Drinking the juice…" in slogans
        assert "Stealing your power…" in slogans
        assert len(slogans) == 12

    def test_it_is_a_homage_and_not_a_brand(self):
        js = auth._LOCK_SCREEN_SCRIPT
        start = js.index("var CHARGE_SLOGANS")
        block = js[start:js.index("function chargeShow(", start)]
        code = "\n".join(l for l in block.splitlines()
                         if not l.strip().startswith("//"))
        for mark in ("Claude", "claude", "Anthropic", "anthropic"):
            assert mark not in code, mark


# ------------------------------------------------------------ the dark panel


_FLOW = r"""
var TIMERS = [], NOW = 0, RAF = [];
var window = {
  setTimeout: function (fn, ms) { TIMERS.push({ at: NOW + (ms || 0), fn: fn }); return TIMERS.length; },
  clearTimeout: function (id) { if (TIMERS[id - 1]) TIMERS[id - 1].fn = null; },
  requestAnimationFrame: function (fn) { RAF.push(fn); return RAF.length; },
  cancelAnimationFrame: function () {},
  matchMedia: function () { return { matches: false }; }
};
Date.now = function () { return NOW; };
function advance(ms) {
  var end = NOW + ms;
  while (true) {
    var next = null;
    for (var i = 0; i < TIMERS.length; i++) {
      if (TIMERS[i].fn && TIMERS[i].at <= end && (!next || TIMERS[i].at < next.at)) next = TIMERS[i];
    }
    if (!next) break;
    NOW = next.at; var fn = next.fn; next.fn = null; fn();
  }
  NOW = end;
  var frames = RAF; RAF = []; frames.forEach(function (f) { f(); });
}
function el() {
  return {
    _a: {}, hidden: false, offsetWidth: 1, textContent: "", innerHTML: "",
    setAttribute: function (k, v) { this._a[k] = String(v); },
    getAttribute: function (k) { return k in this._a ? this._a[k] : null; },
    hasAttribute: function (k) { return k in this._a; },
    removeAttribute: function (k) { delete this._a[k]; },
    addEventListener: function () {},
    querySelector: function (sel) { return this._q[sel] || (this._q[sel] = el()); },
    _q: {}
  };
}
var BLACK = false;
function setBlack(on) { BLACK = !!on; }
function setText(e, t) { e.textContent = t; return e; }
var screenEl = el(), carEl = el(), volEl = el();
var document = { body: { appendChild: function () {} }, createElement: function () { return el(); } };
var chargeEl = null, chargeRun = null;
var CHARGE_MS = 10000, CHARGE_FADE_MS = 440, CHARGE_DONE_MS = 8600;
var CHARGE_SLOGAN_MS = 1600, CHARGE_SPIN_MS = 120;
__SOURCE__
function snap(label) {
  return { label: label, black: BLACK, blanked: screenEl.hasAttribute("data-blanked"),
           fromdark: screenEl.hasAttribute("data-fromdark"),
           shown: !!chargeEl && !chargeEl.hidden, on: !!chargeEl && chargeEl.getAttribute("data-on") === "1",
           dark: !!chargeEl && chargeEl.getAttribute("data-dark") === "1",
           done: !!chargeEl && chargeEl.getAttribute("data-done") === "1" };
}
var out = [];
var SCN = JSON.parse(process.env.LS_CHARGE);
chargeShow(SCN.data);
out.push(snap("painted"));
advance(16); out.push(snap("frame"));
advance(1500); out.push(snap("mid"));
advance(7400); out.push(snap("late"));
advance(1200); out.push(snap("fading"));
advance(600); out.push(snap("after"));
process.stdout.write(JSON.stringify(out));
"""


def _flow(data: dict) -> dict:
    src = "\n".join([
        _pure(), _function("chargeReduced"), _function("chargeBuild"),
        _function("chargePaint"), _function("chargeTick"),
        _function("chargeFinish"), _function("chargeShow"),
    ])
    frames = _node(_FLOW.replace("__SOURCE__", src),
                   env={"LS_CHARGE": json.dumps({"data": data})})
    return {f["label"]: f for f in frames}


class TestOverADarkPanel:
    def test_the_terminal_is_over_black_before_the_panel_lights(self):
        """The watcher posts, then wakes the panel 150 ms later. By the time
        this returns -- synchronously, before any frame -- the page must be
        black and the lock screen hidden, or the first lit frame is a flash of
        lock screen."""
        f = _flow({"percent": 64, "screen": "off"})
        painted = f["painted"]
        assert painted["black"] and painted["blanked"] and painted["fromdark"]
        assert painted["shown"] and painted["dark"]

    def test_it_holds_for_ten_seconds_then_reveals_the_lock_screen(self):
        f = _flow({"percent": 64, "screen": "off"})
        assert f["mid"]["on"] and f["mid"]["black"], "still up at 1.5 s"
        assert f["late"]["done"], "the green tick lands before the fade"
        assert not f["fading"]["on"], "fading by 10.1 s"
        after = f["after"]
        assert not after["shown"]
        assert not after["black"] and not after["blanked"] and not after["fromdark"], \
            "after the fade the lock screen comes back up out of the black"

    def test_a_lit_panel_is_never_blackened(self):
        f = _flow({"percent": 64, "screen": "on"})
        for label, snap in f.items():
            assert not snap["black"] and not snap["blanked"], label
        assert f["painted"]["shown"] and not f["painted"]["dark"]
        assert f["mid"]["on"]
        assert not f["after"]["shown"]

    def test_an_unread_battery_still_plays(self):
        f = _flow({"percent": None, "screen": "off"})
        assert f["mid"]["on"] and not f["after"]["shown"]


class TestTheStreamIsNotHostageToThePowerMenu:
    def test_the_charger_listener_does_not_live_inside_the_power_menu_block(self):
        """The EventSource used to be created inside `if (powerSheet)`. The
        charger listener must get the shared stream from lockEvents()."""
        js = auth._LOCK_SCREEN_SCRIPT
        assert js.count('new EventSource("/auth/lock-events")') == 1
        start = js.index("if (powerSheet) {")
        block = _balanced(js, start, "{", "}")
        assert "new EventSource(" not in block
        assert 'addEventListener("charger"' not in block
        assert 'addEventListener("charger"' in js
