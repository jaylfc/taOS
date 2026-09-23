"""The lock screen's call zone, EXECUTED under node rather than grepped for.

The server half (tests/test_lock_call_demo.py) decides what the call IS. This
file pins what the page DOES with it, through the same harnesses the other
lock-screen suites use: the real functions are sliced out of
`_LOCK_SCREEN_SCRIPT` and run against the DOM stand-in from the repaint tests.

The property that matters most is the one the lock screen has been bitten by
before: **a poll that changed nothing must not rebuild anything.** The zone is
polled every 300ms during a call, and a transcript rebuilt on each poll would
replay every bubble's entrance three times a second. So, as in the repaint
suite, the transcript assertions are about NODE IDENTITY, and a control puts
the wipe back and requires identity to break -- without it, a reconcile that
quietly did nothing would pass everything below.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from tinyagentos.routes.auth import _LOCK_SCREEN_SCRIPT as LOCK_SCRIPT
from tinyagentos.routes.auth import _lock_head_html
import tinyagentos.routes.auth as auth

from test_lock_screen_gestures import (
    _HARNESS as _GESTURE_HARNESS,
    _balanced,
    _function,
    _gesture_source,
    _scenario,
)
from test_lock_screen_repaint import _DOM


def _node() -> str:
    node = shutil.which("node")
    if node is None:  # pragma: no cover - depends on the runner image
        pytest.fail(
            "node is required to execute the lock screen call source and was not "
            "found on PATH. These tests cannot be skipped: a skip reads as green "
            "while proving nothing."
        )
    return node


def _run(body: str, payload=None):
    done = subprocess.run(
        [_node(), "-e", body],
        env={**os.environ, "LS_CALL": json.dumps(payload)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert done.returncode == 0, f"node failed:\n{done.stderr}"
    return json.loads(done.stdout)


_PURE = ["callView", "callOutcomeText", "revealWords", "callEnvelope",
         "callRingPulse", "callTimerText"]


def _pure(expr: str):
    """Evaluate `expr` against the pure call helpers."""
    src = "\n".join(_function(n) for n in _PURE)
    return _run(src + "\nprocess.stdout.write(JSON.stringify(" + expr + "));")


SCRIPT = [
    {"who": "pa", "text": "Hi, and thank you for calling.", "at_ms": 900, "dur_ms": 2000},
    {"who": "caller", "text": "Oh hi! Could you ask him?", "at_ms": 3550, "dur_ms": 1800},
    {"who": "pa", "text": "Of course.", "at_ms": 6000, "dur_ms": 1100},
]


# ------------------------------------------------------ state -> view mapping


class TestTheStateDecidesTheView:
    def test_every_state_maps_to_its_view(self):
        got = _pure("""[
          callView({state: "ringing", caller: {name: "Mary"}}),
          callView({state: "pa", caller: {name: "Mary"}}),
          callView({state: "live", caller: {name: "Mary"}, taken_over: false}),
          callView({state: "live", caller: {name: "Mary"}, taken_over: true}),
          callView({state: "ended", outcome: "pa-done"}),
          callView({state: "idle"}),
          callView(null)
        ]""")
        ringing, pa, answered, taken, ended, idle, none = got
        assert (ringing["view"], ringing["controls"], ringing["tone"]) == ("ringing", None, "caller")
        assert (pa["view"], pa["controls"], pa["log"], pa["tone"]) == ("talk", "pa", True, "pa")
        assert pa["title"] == "Your PA is talking to Mary"
        assert (answered["view"], answered["controls"], answered["log"]) == ("talk", "live", False)
        assert answered["title"] == "On call with Mary"
        # After a take-over the transcript STAYS -- the spec's "marked You took
        # over" has nothing to mark without it.
        assert (taken["controls"], taken["log"]) == ("live", True)
        assert ended["view"] == "ended"
        assert idle["view"] is None and none["view"] is None

    def test_the_pa_phase_always_pins_take_over_and_end(self):
        """Jay, twice: Take over and End call stay visible the WHOLE time the
        PA is talking. The controls are a function of the state alone, so no
        speaking/not-speaking detail of a snapshot can take them away."""
        got = _pure("""[
          callView({state: "pa", speaking: null, transcript: []}).controls,
          callView({state: "pa", speaking: {who: "pa", line: 0, progress: 0.4}}).controls,
          callView({state: "pa", speaking: {who: "caller", line: 1, progress: 0.9}}).controls
        ]""")
        assert got == ["pa", "pa", "pa"]
        html = _lock_head_html()
        assert html.count('data-call-act="takeover"') == 1
        ctl = html[html.index('class="ls-call-ctl" data-for="pa"'):]
        ctl = ctl[: ctl.index('class="ls-call-ctl" data-for="live"')]
        assert 'data-call-act="takeover"' in ctl and 'data-call-act="end"' in ctl

    @pytest.mark.parametrize("snap, text", [
        ({"outcome": "pa-done"}, "Call ended · your PA took a message"),
        ({"outcome": "declined"}, "Declined"),
        ({"outcome": "voicemail"}, "Sent to voicemail"),
        ({"outcome": "ended-by-user", "taken_over": True}, "Call ended · you took over"),
        ({"outcome": "ended-by-user"}, "Call ended"),
    ])
    def test_the_outcome_line(self, snap, text):
        assert _pure("callOutcomeText(" + json.dumps(snap) + ")") == text


# ------------------------------------------------------------ word reveal


class TestWordReveal:
    TEXT = "one two three four five six seven eight nine ten"

    @pytest.mark.parametrize("progress, words", [
        (-0.5, 0), (0, 0), (0.001, 1), (0.1, 1), (0.25, 3), (0.5, 5),
        (0.99, 10), (1, 10), (1.7, 10),
    ])
    def test_progress_reveals_whole_words(self, progress, words):
        got = _pure(f"revealWords({json.dumps(self.TEXT)}, {progress})")
        want = " ".join(self.TEXT.split()[:words])
        assert got == want

    def test_nan_and_missing_progress_reveal_nothing(self):
        assert _pure('[revealWords("a b", NaN), revealWords("a b", undefined), '
                     'revealWords("", 0.5)]') == ["", "", ""]

    def test_the_reveal_never_goes_backwards(self):
        got = _pure("(function(){var out=[];for(var p=0;p<=1.0001;p+=0.01)"
                    "out.push(revealWords(" + json.dumps(self.TEXT) + ",p).length);return out;})()")
        assert got == sorted(got)


class TestTheEnvelope:
    def test_it_stays_in_range_and_tapers_at_the_ends(self):
        got = _pure("""(function(){
          var lo=1, hi=0;
          for (var t=0;t<20;t+=0.013){ for (var p=0;p<=1;p+=0.05){
            var e=callEnvelope(t,p); lo=Math.min(lo,e); hi=Math.max(hi,e);} }
          var edge=0, mid=0;
          for (var s=0;s<5;s+=0.01){ edge=Math.max(edge,callEnvelope(s,0),callEnvelope(s,1));
                                     mid=Math.max(mid,callEnvelope(s,0.5)); }
          var ring=[]; for (var r=0;r<4.4;r+=0.01) ring.push(callRingPulse(r));
          return [lo, hi, edge, mid, Math.min.apply(null,ring), Math.max.apply(null,ring)];
        })()""")
        lo, hi, edge, mid, rlo, rhi = got
        assert 0 <= lo and hi <= 1
        # A line starts and ends at rest; the middle of one actually moves.
        assert edge == pytest.approx(0.22, abs=1e-6)
        assert mid > 0.8
        assert 0 <= rlo < 0.05 and rhi > 0.95

    @pytest.mark.parametrize("ms, text", [
        (0, "0:00"), (999, "0:00"), (1000, "0:01"), (59_999, "0:59"),
        (60_000, "1:00"), (61_500, "1:01"), (-5, "0:00"), (None, "0:00"),
    ])
    def test_the_timer(self, ms, text):
        assert _pure(f"callTimerText({json.dumps(ms)})") == text


# ------------------------------------------------ the transcript reconcile

_RECONCILE = _DOM + r"""
__SOURCE__
var BUILT = 0;
var __real = callBubble;
callBubble = function (line, i) { BUILT += 1; return __real(line, i); };
var logEl = makeNode("div");
var SCN = JSON.parse(process.env.LS_CALL);
var snaps = [];
for (var t = 0; t < SCN.ticks.length; t++) {
  var tk = SCN.ticks[t];
  __PAINT__(logEl, tk.lines, tk.speaking, !!tk.tookOver, "Mary");
  snaps.push(logEl.children.map(function (el) {
    return { id: el.__id, line: el.getAttribute("data-line"),
             who: el.getAttribute("data-who"), speaking: el.getAttribute("data-speaking"),
             cut: el.getAttribute("data-cut"), mark: el.getAttribute("data-mark"),
             text: el.children.length > 1 ? el.children[1].textContent : el.textContent,
             label: el.children.length ? el.children[0].children[0].textContent : null };
  }));
}
process.stdout.write(JSON.stringify({ snaps: snaps, built: BUILT }));
"""

_WIPE = r"""
function wipeAndRebuild(logEl, lines, speakingLine, tookOver, name) {
  logEl.textContent = "";
  return reconcileTranscript(logEl, lines, speakingLine, tookOver, name);
}
"""


def _reconcile(ticks, *, wipe=False):
    src = "\n".join([_function("revealWords"), _function("callBubble"),
                     _function("reconcileTranscript")])
    if wipe:
        src += _WIPE
    body = _RECONCILE.replace("__SOURCE__", src).replace(
        "__PAINT__", "wipeAndRebuild" if wipe else "reconcileTranscript")
    return _run(body, {"ticks": ticks})


def _ids(snap):
    return [row["id"] for row in snap]


class TestTheTranscriptIsReconciledNotRepainted:
    def test_an_unchanged_poll_rebuilds_no_bubble(self):
        """THE RED CASE. Two identical polls: every bubble is the same node and
        nothing new was constructed."""
        tick = {"lines": SCRIPT[:2], "speaking": 1}
        out = _reconcile([tick, tick, tick])
        assert _ids(out["snaps"][0]) == _ids(out["snaps"][1]) == _ids(out["snaps"][2])
        assert out["built"] == 2

    def test_the_harness_observes_the_defect(self):
        """The control: with the wipe put back, identity breaks. If this ever
        passes identity, the stand-in DOM has gone inert and every property
        in this class is unproven."""
        tick = {"lines": SCRIPT[:2], "speaking": 1}
        out = _reconcile([tick, tick], wipe=True)
        assert set(_ids(out["snaps"][0])).isdisjoint(_ids(out["snaps"][1]))
        assert out["built"] == 4

    def test_a_new_line_is_appended_and_the_old_ones_kept(self):
        out = _reconcile([
            {"lines": SCRIPT[:1], "speaking": 0},
            {"lines": SCRIPT[:2], "speaking": 1},
            {"lines": SCRIPT[:3], "speaking": 2},
        ])
        s0, s1, s2 = out["snaps"]
        assert _ids(s1)[:1] == _ids(s0)
        assert _ids(s2)[:2] == _ids(s1)
        assert out["built"] == 3
        assert [r["who"] for r in s2] == ["pa", "caller", "pa"]
        assert [r["label"] for r in s2] == ["Your PA", "Mary", "Your PA"]

    def test_only_the_line_being_spoken_is_marked_and_left_for_the_reveal(self):
        """The reconcile writes finished lines in full and leaves the current
        one's text alone -- the animation loop owns its word-by-word reveal,
        and two writers would fight over it every 300ms."""
        out = _reconcile([{"lines": SCRIPT[:2], "speaking": 1}])
        first, second = out["snaps"][0]
        assert (first["speaking"], first["text"]) == (None, SCRIPT[0]["text"])
        assert (second["speaking"], second["text"]) == ("1", "")

    def test_a_finished_line_is_filled_in_and_unmarked(self):
        out = _reconcile([
            {"lines": SCRIPT[:2], "speaking": 1},
            {"lines": SCRIPT[:2], "speaking": -1},
        ])
        second = out["snaps"][1][1]
        assert second["speaking"] is None
        assert second["text"] == SCRIPT[1]["text"]
        assert _ids(out["snaps"][0]) == _ids(out["snaps"][1])

    def test_a_take_over_cuts_the_line_and_adds_one_marker(self):
        cut = dict(SCRIPT[1], upto=0.5)
        tick = {"lines": [SCRIPT[0], cut], "speaking": -1, "tookOver": True}
        out = _reconcile([{"lines": SCRIPT[:2], "speaking": 1}, tick, tick])
        s1, s2 = out["snaps"][1], out["snaps"][2]
        assert s1[1]["cut"] == "1"
        assert s1[1]["text"] == "Oh hi! Could"          # half of six words
        assert [r["mark"] for r in s1] == [None, None, "takeover"]
        assert s1[2]["text"] == "You took over"
        # Idempotent: the marker is not added again on the next poll.
        assert _ids(s1) == _ids(s2)


# -------------------------------------------------------- touch on the zone


_CALL_GESTURE_HARNESS = _GESTURE_HARNESS.replace(
    "var document = { body: body };",
    "var callZone = makeEl({ sel: ['.ls-live-call'], parent: body });\n"
    "var callButton = makeEl({ sel: ['button'], parent: callZone });\n"
    "var document = { body: body };",
).replace(
    "var targets = { body: body, card: card, feed: feedEl };",
    "var targets = { body: body, card: card, feed: feedEl, call: callButton };",
)


def _drive_call(scenario, *, mutate=False):
    src = _gesture_source()
    arm = '      if (t && t.closest && t.closest(".ls-live-call")) return true;\n'
    if mutate:
        assert arm in src, "the call arm is gone from the unlock veto"
        src = src.replace(arm, "")
    body = _CALL_GESTURE_HARNESS.replace("__GESTURE_SOURCE__", src)
    done = subprocess.run([_node(), "-e", body],
                          env={**os.environ, "LS_SCENARIO": json.dumps(scenario)},
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)["unlocked"]


class TestTheZoneIsNotAnUnlockPad:
    def test_a_drag_that_starts_on_the_call_card_does_not_unlock(self):
        """Buttons under a thumb: a drag that begins on Take over must not
        throw the user into the keypad in the middle of a call."""
        assert _drive_call(_scenario(startOn="call", endOn="call")) == 0

    def test_the_same_drag_unlocks_without_the_call_arm(self):
        """The control. The drag is long and vertical and starts OUTSIDE the
        feed, so only the new arm can be what stops it."""
        assert _drive_call(_scenario(startOn="call", endOn="call"), mutate=True) == 1

    def test_elsewhere_on_the_glass_still_unlocks(self):
        assert _drive_call(_scenario(startOn="body", endOn="body")) == 1

    def test_the_shade_refuses_a_pull_that_starts_on_the_call_card(self):
        start = LOCK_SCRIPT.index("vetoed unless it began up top")
        veto = LOCK_SCRIPT[start - 400:start]
        assert 'closest(".ls-live-call")) return true' in veto


class TestTheWiring:
    def test_the_zone_polls_and_stops_on_404(self):
        src = LOCK_SCRIPT[LOCK_SCRIPT.index("var pollCall = function"):]
        src = src[: src.index("var callAct = function")]
        assert 'fetch("/auth/lock-call"' in src
        assert "r.status === 404" in src and "callOff = true" in src
        sched = LOCK_SCRIPT[LOCK_SCRIPT.index("var callSchedule = function"):]
        assert "active ? 300 : 2000" in sched[:400]

    def test_it_listens_whether_or_not_the_shared_stream_exists(self):
        """The zone uses the page's one shared stream (lockEvents(), created
        outside `if (powerSheet)`), never an EventSource of its own."""
        src = LOCK_SCRIPT[LOCK_SCRIPT.index("var callStream ="):]
        src = src[:800]
        assert "var callStream = lockEvents();" in src
        assert "new EventSource(" not in src
        assert 'addEventListener("call"' in src
        assert LOCK_SCRIPT.count('new EventSource("/auth/lock-events")') == 1

    def test_the_feed_is_hidden_by_its_own_attribute_only(self):
        """The call must never borrow `hidden`, `data-off` or `data-hidden`:
        each has an owner, and the feed would come back in the wrong state."""
        block = LOCK_SCRIPT[LOCK_SCRIPT.index("var callEl = document.getElementById"):]
        block = block[: block.index("// Keypad -> the existing PIN input.")]
        assert "feedEl.hidden" not in block
        assert "setFeedHidden(" not in block
        assert 'feedEl.setAttribute("data-off"' not in block
        assert 'feedEl.setAttribute("data-hidden"' not in block
        assert 'feedEl.setAttribute("data-call", "1")' in block

    def test_the_animation_loop_stops_when_the_screen_is_dark(self):
        src = LOCK_SCRIPT[LOCK_SCRIPT.index("var callLoopOn = function"):]
        src = src[: src.index("};") + 2]
        assert 'hasAttribute("data-blanked")' in src
        assert "document.hidden" in src
        assert "callShown" in src


# ------------------------------------------------------------- the glow

#: The flicker Jay saw, measured before the fix in chromium at 540x1200 while
#: the PA spoke: the edge's opacity stepped by up to 0.48 in a single frame and
#: by more than 0.08 on 99 of 230 frames. No animation restarted and no
#: attribute changed on the ring -- the rotation's currentTime was continuous.
#: The cause was the per-frame opacity taking the raw speech envelope, which
#: moves at syllable rate. These drive the loop's arithmetic the way callTick
#: does and bound the step per frame.
_GLOW_SIM = r"""
function simulate(fps, seconds, raw) {
  var dt = 1000 / fps, glow = 0.3, prevOp = null, maxStep = 0, big = 0, n = 0;
  for (var ms = 0; ms < seconds * 1000; ms += dt) {
    var t = ms / 1000;
    var progress = (ms % 4000) / 4000;          // a 4s line, repeated
    var amp = callEnvelope(t, progress);
    var op;
    if (raw) op = 0.28 + 0.72 * amp;             // the shipped-then-flickering mapping
    else { glow = callGlowStep(glow, amp, dt); op = 0.45 + 0.55 * glow; }
    if (prevOp !== null) {
      var d = Math.abs(op - prevOp);
      maxStep = Math.max(maxStep, d); if (d > 0.08) big++; n++;
    }
    prevOp = op;
  }
  return { maxStep: maxStep, big: big, frames: n };
}
"""


def _glow(expr):
    src = "\n".join(_function(n) for n in ["callEnvelope", "callGlowStep"]) + _GLOW_SIM
    return _run(src + "\nprocess.stdout.write(JSON.stringify(" + expr + "));")


class TestTheGlowDoesNotFlicker:
    def test_the_harness_sees_the_flicker_in_the_old_mapping(self):
        """The control: the raw mapping this replaced must read as a flicker
        here, or the bound below proves nothing."""
        # At a clean 60fps the raw mapping steps up to ~0.22 a frame (the
        # browser, with real frame jitter, measured 0.48) -- over four times
        # the bound the fixed mapping is held to below.
        got = _glow("simulate(60, 8, true)")
        assert got["maxStep"] > 0.15
        assert got["big"] > got["frames"] * 0.2

    @pytest.mark.parametrize("fps", [60, 30])
    def test_the_edge_changes_by_little_per_frame_while_speaking(self, fps):
        got = _glow(f"simulate({fps}, 8, false)")
        assert got["big"] == 0
        assert got["maxStep"] <= (0.05 if fps == 60 else 0.09)

    def test_it_still_breathes_with_the_voice(self):
        """Smooth, not flat: a glow that no longer moved would pass the bound
        above and fail the brief ("needs fixing not removing")."""
        got = _run("\n".join(_function(n) for n in ["callEnvelope", "callGlowStep"]) + r"""
          var g = 0.3, lo = 1, hi = 0;
          for (var ms = 0; ms < 8000; ms += 16.7) {
            g = callGlowStep(g, callEnvelope(ms / 1000, (ms % 4000) / 4000), 16.7);
            if (ms > 1000) { lo = Math.min(lo, g); hi = Math.max(hi, g); }
          }
          process.stdout.write(JSON.stringify([lo, hi]));""")
        lo, hi = got
        assert hi - lo > 0.3

    def test_a_long_pause_cannot_become_one_jump(self):
        """The loop stops with the screen off. The first frame back must not
        apply seconds of easing at once."""
        got = _glow("[callGlowStep(0, 1, 5000), callGlowStep(0, 1, 100), callGlowStep(0.5, 0.5, 16)]")
        assert got[0] == got[1] < 0.5
        assert got[2] == 0.5

    def test_the_spin_rate_does_not_change_with_the_state(self):
        """A running animation's position is its elapsed time over its
        duration, so a duration that changed at ringing -> PA snapped the
        sweep to a new angle in one frame."""
        style = auth._LOCK_SCREEN_STYLE
        assert '[data-state="ringing"] .ls-call-ring' not in style
        assert "animation-duration" not in style[style.index(".ls-call-ring {"):
                                                 style.index("@keyframes ls-call-spin")].replace(
            "animation-duration: 6.4s", "")


# ------------------------------------ a poll must not touch the glow at all

def _var_fn(name: str) -> str:
    """The source of `var <name> = function (...) {...};`."""
    head = f"var {name} = function"
    assert head in LOCK_SCRIPT, f"{head!r} is gone from the lock screen script"
    start = LOCK_SCRIPT.index(head)
    return _balanced(LOCK_SCRIPT, start, "{", "}") + ";"


_PAINT_HARNESS = _DOM + r"""
__HELPERS__
var WRITES = [];
function watch(el, name) {
  var s = el.setAttribute, r = el.removeAttribute;
  el.setAttribute = function (k, v) { WRITES.push(name + " set " + k); return s.call(this, k, v); };
  el.removeAttribute = function (k) { WRITES.push(name + " remove " + k); return r.call(this, k); };
  var cls = el.className;
  Object.defineProperty(el, "className", {
    get: function () { return cls; },
    set: function (v) { WRITES.push(name + " class"); cls = v; }
  });
  return el;
}
var callEl = makeNode("section");
var glow = makeNode("div");
var rings = [makeNode("span"), makeNode("span")];
var washes = [makeNode("span"), makeNode("span")];
callEl.appendChild(glow);
rings.forEach(function (r) { glow.appendChild(r); });
washes.forEach(function (w) { callEl.appendChild(w); });
var callTitle = makeNode("span"), callLog = makeNode("div"), callOutcome = makeNode("span");
var callPill = makeNode("div"), callPillIco = makeNode("span");
var callCtls = { pa: makeNode("div"), live: makeNode("div") };
["ls-call-name", "ls-call-label", "ls-call-avatar"].forEach(function (id) {
  makeNode("div").setAttribute("id", id);
});
var callSpeakEl = null, reduceMotion = true;
var CALL_ICONS = { end: "", calendar: "" };
function callSwitch() {}
function callFocus() {}
__PAINT__
var SCN = JSON.parse(process.env.LS_CALL);
paintCall(SCN.snap);
watch(callEl, "zone"); watch(glow, "glow");
rings.forEach(function (r, i) { watch(r, "ring" + i); });
washes.forEach(function (w, i) { watch(w, "wash" + i); });
paintCall(SCN.snap);
paintCall(SCN.snap);
process.stdout.write(JSON.stringify({ writes: WRITES }));
"""


def _paint_twice(snap, *, unconditional=False):
    helpers = "\n".join(_function(n) for n in [
        "callView", "callOutcomeText", "revealWords", "callBubble",
        "reconcileTranscript", "setText", "setAttrIfChanged"])
    paint = _var_fn("paintCall")
    if unconditional:
        old = 'setAttrIfChanged(callEl, "data-view", v.view || "");'
        assert old in paint, "the mutation no longer matches paintCall"
        paint = paint.replace(old, 'callEl.setAttribute("data-view", v.view || "");')
    body = _PAINT_HARNESS.replace("__HELPERS__", helpers).replace("__PAINT__", paint)
    return _run(body, {"snap": snap})["writes"]


_SNAPS = {
    "ringing": {"state": "ringing", "call_id": 1, "caller": {"name": "Mary", "label": "mobile"},
                "transcript": [], "speaking": None, "taken_over": False},
    "pa": {"state": "pa", "call_id": 1, "caller": {"name": "Mary", "label": "mobile"},
           "transcript": SCRIPT[:2], "speaking": {"who": "caller", "line": 1, "progress": 0.4},
           "taken_over": False, "elapsed_ms": 5000},
    "live": {"state": "live", "call_id": 1, "caller": {"name": "Mary", "label": "mobile"},
             "transcript": [SCRIPT[0], dict(SCRIPT[1], upto=0.5)], "speaking": None,
             "taken_over": True, "elapsed_ms": 9000},
    "ended": {"state": "ended", "call_id": 1, "outcome": "pa-done",
              "caller": {"name": "Mary", "label": "mobile"}, "transcript": SCRIPT,
              "speaking": None, "taken_over": False},
}


class TestAnUnchangedPollLeavesTheGlowAlone:
    @pytest.mark.parametrize("state", sorted(_SNAPS))
    def test_no_attribute_or_class_write_on_the_zone_or_its_glow(self, state):
        """Polled every 300ms. The zone is the ANCESTOR of the spinning edge,
        so each write there is a style invalidation over the glow; the glow's
        own nodes must not be touched by a poll at all."""
        assert _paint_twice(_SNAPS[state]) == []

    def test_the_harness_sees_an_unconditional_write(self):
        """The control: put back one unconditional setAttribute and the
        harness must report it, or the empty lists above prove nothing."""
        writes = _paint_twice(_SNAPS["pa"], unconditional=True)
        assert writes.count("zone set data-view") == 2

    def test_nothing_else_in_the_script_writes_the_glows_attributes(self):
        """The rings and washes are touched only through style.opacity, in the
        animation loop. Their data-tone is read, never written."""
        block = LOCK_SCRIPT[LOCK_SCRIPT.index("var callEl = document.getElementById"):]
        block = block[: block.index("// Keypad -> the existing PIN input.")]
        for name in ("callRings", "callWashes"):
            for bad in (".setAttribute(", ".removeAttribute(", ".className", ".classList"):
                assert f"{name}[r]{bad}" not in block and f"{name}[q]{bad}" not in block


# ------------------------------------------- the PA's reminder in Alerts

from test_lock_screen_repaint import _NOTIF_HARNESS, _notif_source, _stacks  # noqa: E402

_REMINDER_HARNESS = _NOTIF_HARNESS.replace(
    "__SOURCE__",
    "__SOURCE__\n"
    "var REM = makeNode('div'); REM.className = 'ls-row ls-call-reminder';\n"
    "REM.setAttribute('id', 'ls-call-reminder'); notifsEl.appendChild(REM);\n"
    "notifsEl.hidden = false;\n",
).replace(
    "process.stdout.write(JSON.stringify({",
    "process.stdout.write(JSON.stringify({ rem: REM.__id, remIn: REM.parent === notifsEl, "
    "remAt: notifsEl.children.indexOf(REM),",
)


def _paint_with_reminder(ticks):
    body = _REMINDER_HARNESS.replace("__SOURCE__", _notif_source()).replace(
        "__PAINT__", "paintNotifications")
    done = subprocess.run([_node(), "-e", body],
                          env={**os.environ, "LS_NOTIFS": json.dumps({"ticks": ticks})},
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


class TestTheReminderSurvivesTheNotificationPoll:
    """The reminder sits in #ls-notifs but is not a notification: it comes and
    goes with the call. The stacks' own reconcile removes everything past its
    last wanted child, so it must carry the reminder through."""

    def test_a_poll_with_stacks_keeps_the_reminder_node_on_top(self):
        out = _paint_with_reminder([_stacks(), _stacks()])
        assert out["remIn"] and out["remAt"] == 0
        assert out["snapshots"][0][0]["id"] == out["snapshots"][1][0]["id"] == out["rem"]

    def test_an_empty_poll_keeps_it_and_the_panel_showing(self):
        out = _paint_with_reminder([_stacks(), {"groups": []}])
        assert out["remIn"]
        assert [r["id"] for r in out["snapshots"][1]] == [out["rem"]]
        assert out["hidden"] is False

    def test_without_it_an_empty_poll_still_hides_the_panel(self):
        """The old behaviour is untouched when there is no reminder."""
        out = _paint_notifs_plain([_stacks(), {"groups": []}])
        assert out["snapshots"][1] == [] and out["hidden"] is True


def _paint_notifs_plain(ticks):
    from test_lock_screen_repaint import _paint_notifs
    return _paint_notifs(ticks)


class TestTheReminderCard:
    def test_the_words_are_the_pas(self):
        assert _pure_with("callReminderPill", 'callReminderPill({title: "Reminder added", '
                          'event: "Call Mary", time: "Tomorrow 12:30 pm"})') == \
            "Reminder added · Call Mary · Tomorrow 12:30 pm"

    def test_it_has_a_real_dismiss_button_that_clears_it_on_the_server(self):
        block = LOCK_SCRIPT[LOCK_SCRIPT.index("var buildReminder = function"):]
        block = block[: block.index("var leaveReminder = function")]
        assert 'document.createElement("button")' in block
        assert 'dismiss.type = "button"' in block
        assert 'textContent = "Dismiss"' in block
        assert 'fetch("/auth/lock-call/dismiss", { method: "POST"' in block

    def test_a_drag_that_starts_on_the_reminder_does_not_unlock(self):
        harness = _CALL_GESTURE_HARNESS.replace(
            "var targets = { body: body, card: card, feed: feedEl, call: callButton };",
            "var rem = makeEl({ sel: ['.ls-call-reminder'], parent: feedEl });\n"
            "var remBtn = makeEl({ sel: ['button'], parent: rem });\n"
            "var targets = { body: body, card: card, feed: feedEl, call: callButton, rem: remBtn };")
        # A feed scrolled to its END, where a drag on a plain card DOES unlock
        # (tsk-36i6ed): only the reminder arm can be what stops this one.
        scn = _scenario(startOn="rem", endOn="rem", feedScrollTop=600)
        plain = _scenario(startOn="card", endOn="card", feedScrollTop=600)
        for s, want in ((scn, 0), (plain, 1)):
            body = harness.replace("__GESTURE_SOURCE__", _gesture_source())
            done = subprocess.run([_node(), "-e", body],
                                  env={**os.environ, "LS_SCENARIO": json.dumps(s)},
                                  capture_output=True, text=True, timeout=30)
            assert done.returncode == 0, done.stderr
            assert json.loads(done.stdout)["unlocked"] == want, s["startOn"]

    def test_the_ended_pill_matches_the_card(self):
        block = LOCK_SCRIPT[LOCK_SCRIPT.index("var callEnded = function"):][:1200]
        assert "callReminderPill(snap.notification)" in block
        assert "New event added" not in LOCK_SCRIPT


def _pure_with(name, expr):
    return _run(_function(name) + "\nprocess.stdout.write(JSON.stringify(" + expr + "));")


class TestTheWidgetDoesNotShareAClassWithPhoneRows:
    """The Phone panel's rows are `ls-row ls-call`. The call widget once used
    `.ls-call` as its own root class, so its 1.5 px padding, clipped overflow
    and 28 px radius landed on every phone row -- the source icon sat in the
    rounded corner and the time was cut off (Jay: "too high and cut off") --
    and the unlock veto `closest(".ls-call")` refused swipes that started on
    a missed call."""

    def test_no_rule_or_lookup_targets_a_bare_ls_call(self):
        import re
        from tinyagentos.routes import auth
        for name, text in (("script", auth._LOCK_SCREEN_SCRIPT),
                           ("page", auth._lock_head_html() if callable(getattr(auth, "_lock_head_html", None)) else "")):
            assert not re.search(r'closest\("\.ls-call"\)', text), name
        css_and_all = open(auth.__file__, encoding="utf-8").read()
        bare = re.findall(r'^\.ls-call(?![-\w])[^\n]*\{', css_and_all, re.M)
        assert bare == [], bare

    def test_the_widget_root_has_its_own_class(self):
        from tinyagentos.routes import auth
        src = open(auth.__file__, encoding="utf-8").read()
        assert '<section class="ls-live-call" id="ls-call"' in src
