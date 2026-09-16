"""The lock screen's view row, the avatar menu, and the honesty of the stats.

Like `test_lock_screen_gestures.py`, the JavaScript tests here EXECUTE the real
source out of `_LOCK_SCREEN_SCRIPT` under node against a small DOM stand-in,
rather than asserting that the source contains a string. A string assertion for
any of this passes whether or not the code works, and two of these behaviours
(the latch, the two-owner attribute rule) are precisely the kind that look right
in the source and do the wrong thing when driven.

Every behavioural class here carries a MUTATION CONTROL: a test that re-runs the
same scenario against a deliberately broken version of the code and requires it
to fail. Without one, a harness that quietly stubs everything out reports a row
of passes having measured nothing -- which is a mistake this file's author has
made before.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from tinyagentos.routes import auth
from tinyagentos.routes.auth import _LOCK_SCREEN_SCRIPT as LOCK_SCRIPT
from test_lock_screen_gestures import _balanced


class Failed(AssertionError):
    """The scenario ran and the behaviour was wrong."""


def _node() -> str:
    exe = shutil.which("node")
    if exe is None:
        # FAIL, never skip. A skipped suite reports green having measured
        # nothing, and these tests are the only thing standing between the view
        # switcher and a silent regression. CI provisions node for this reason.
        raise Failed(
            "node is required to execute the lock-screen source. "
            "CI provisions it in the shards job; install node to run this suite."
        )
    return exe


def _press_machinery() -> str:
    """Source of the island press IIFE -- the block that owns the long press."""
    anchor = "var HOLD_MS"
    assert anchor in LOCK_SCRIPT, "the island press machinery has moved"
    start = LOCK_SCRIPT.index(anchor)
    iife = LOCK_SCRIPT.index("(function ()", start)
    # _balanced returns the source FROM the index it is given through the
    # balanced close, so it already carries the "(function () {" itself. Adding
    # that prefix again -- or slicing head up to the newline after it -- hands
    # node a program with the opener twice.
    head = LOCK_SCRIPT[start:iife]
    body = _balanced(LOCK_SCRIPT, iife, "{", "}")
    return head + body + ")();"


def _show_view() -> str:
    head = "function showView("
    assert head in LOCK_SCRIPT, "showView is gone from the lock screen script"
    return _balanced(LOCK_SCRIPT, LOCK_SCRIPT.index(head), "{", "}")


#: A DOM stand-in with just enough of the shape these behaviours touch.
_DOM = r"""
function makeEl(opts) {
  opts = opts || {};
  var el = {
    _sel: opts.sel || [],
    _parent: opts.parent || null,
    _attrs: opts.attrs || {},
    _kids: opts.kids || [],
    _h: {},
    // `hidden` is a real property so an assignment to it is OBSERVABLE. That is
    // the whole point of the two-owner test: we need to know whether the
    // switcher wrote to it, not merely what it ended up as.
    _hiddenWrites: 0,
    scrollTop: 0,
    scrollHeight: opts.scrollHeight || 0,
    clientHeight: opts.clientHeight || 0,
    focus: function () { this._focused = true; },
    addEventListener: function (t, fn) { (this._h[t] = this._h[t] || []).push(fn); },
    removeEventListener: function () {},
    setAttribute: function (k, v) { this._attrs[k] = String(v); },
    removeAttribute: function (k) { delete this._attrs[k]; },
    getAttribute: function (k) {
      return Object.prototype.hasOwnProperty.call(this._attrs, k) ? this._attrs[k] : null;
    },
    closest: function (sel) {
      var n = this;
      while (n) { if (n._sel.indexOf(sel) !== -1) return n; n = n._parent; }
      return null;
    },
    querySelectorAll: function (sel) {
      // Only the two selectors this code uses.
      return this._kids.filter(function (k) {
        if (sel === "[data-view]") return k.getAttribute("data-view") !== null;
        return k._sel.indexOf(sel) !== -1;
      });
    },
    querySelector: function (sel) { return this.querySelectorAll(sel)[0] || null; },
    fire: function (t, ev) { (this._h[t] || []).slice().forEach(function (fn) { fn(ev); }); }
  };
  var hiddenValue = !!opts.hidden;
  Object.defineProperty(el, "hidden", {
    get: function () { return hiddenValue; },
    set: function (v) { hiddenValue = !!v; el._hiddenWrites++; }
  });
  return el;
}
"""


# ---------------------------------------------------------------------------
# THE VIEW SWITCHER
# ---------------------------------------------------------------------------

_VIEW_KEYS = [key for key, _l, _i, _p in auth._LOCK_VIEWS]


def _run_views(*, target: str, mutate: bool = False) -> dict:
    """Drive the real showView and report what it did to the panels."""
    source = _show_view()
    if mutate:
        # THE MUTATION: drive `hidden` instead of `data-off`. This is the shape
        # the code would have had if the two owners had not been separated, and
        # it is what the poll then fights with. If a test cannot tell this
        # version from the real one, that test is not measuring anything.
        source = source.replace(
            'panels[j].removeAttribute("data-off");',
            "panels[j].hidden = false;",
        ).replace(
            'panels[j].setAttribute("data-off", "1");',
            "panels[j].hidden = true;",
        )

    program = (
        _DOM
        + "\nvar VIEWS = %s;\nvar VIEW_DEFAULT = %s;\n"
        % (json.dumps({k: 1 for k in _VIEW_KEYS}), json.dumps(_VIEW_KEYS[0]))
        + r"""
var panels = %(keys)s.map(function (k) {
  return makeEl({ sel: [], attrs: { "data-view": k } });
});
var tabs = %(keys)s.map(function (k) {
  return makeEl({ sel: [".ls-view-tab"], attrs: { "data-view": k } });
});
var feedEl = makeEl({ kids: panels });
var viewsEl = makeEl({ kids: tabs });
var currentView = VIEW_DEFAULT;
function viewTabs() { return tabs; }
function renderView() {}
function syncFeedFade() {}
function startStats() {}
function stopStats() {}
function renderPlaceholder() {}

%(showView)s

showView(%(target)s, false);

var byKey = {};
panels.forEach(function (p) {
  byKey[p.getAttribute("data-view")] = {
    off: p.getAttribute("data-off"),
    hidden: p.hidden,
    hiddenWrites: p._hiddenWrites
  };
});
console.log(JSON.stringify({
  panels: byKey,
  selected: tabs.filter(function (t) { return t.getAttribute("aria-selected") === "true"; })
                .map(function (t) { return t.getAttribute("data-view"); }),
  tabindexZero: tabs.filter(function (t) { return t.getAttribute("tabindex") === "0"; })
                    .map(function (t) { return t.getAttribute("data-view"); })
}));
"""
        % {
            "keys": json.dumps(_VIEW_KEYS),
            "showView": source,
            "target": json.dumps(target),
        }
    )

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "views.js"
        path.write_text(program, encoding="utf-8")
        proc = subprocess.run(
            [_node(), str(path)], capture_output=True, text=True, timeout=60
        )
    if proc.returncode != 0:
        raise Failed("the view switcher source threw:\n" + proc.stderr[-2000:])
    return json.loads(proc.stdout.strip().splitlines()[-1])


class TestViewSwitcher:
    """Choosing a view shows that panel and only that panel."""

    def test_the_chosen_view_is_the_only_one_not_marked_off(self):
        got = _run_views(target="stats")
        assert got["panels"]["stats"]["off"] is None, "the chosen panel is marked off"
        others = [k for k in _VIEW_KEYS if k != "stats"]
        still_on = [k for k in others if got["panels"][k]["off"] is None]
        assert not still_on, f"panels left showing alongside stats: {still_on}"

    def test_exactly_one_tab_is_selected_and_it_is_the_chosen_one(self):
        got = _run_views(target="mailbox")
        assert got["selected"] == ["mailbox"], got["selected"]

    def test_the_row_keeps_a_single_tab_stop(self):
        # Seven tab stops would put six dead ends between the clock and the
        # unlock button for a keyboard user.
        got = _run_views(target="apps")
        assert got["tabindexZero"] == ["apps"], got["tabindexZero"]

    def test_the_switcher_never_writes_hidden(self):
        """`hidden` belongs to the poll. The switcher must not touch it.

        This is the test that the two-owner rule exists for. If the switcher
        drove `hidden`, the next 15s poll -- which sets `hidden` on the agents
        and alerts panels from their own content -- would show the agents panel
        on top of whichever view the user had actually chosen.
        """
        got = _run_views(target="stats")
        wrote = {k: v["hiddenWrites"] for k, v in got["panels"].items() if v["hiddenWrites"]}
        assert not wrote, f"the switcher wrote to `hidden` on: {wrote}"

    def test_an_unknown_view_falls_back_rather_than_blanking_the_screen(self):
        got = _run_views(target="does-not-exist")
        assert got["selected"] == [_VIEW_KEYS[0]]
        assert got["panels"][_VIEW_KEYS[0]]["off"] is None

    def test_harness_observes_the_defect(self):
        """THE CONTROL. The mutation must fail the test above.

        Without this, every assertion in this class would still pass against a
        stub that did nothing at all -- `off` would be None everywhere and no
        `hidden` write would ever be recorded.
        """
        got = _run_views(target="stats", mutate=True)
        wrote = {k: v["hiddenWrites"] for k, v in got["panels"].items() if v["hiddenWrites"]}
        assert wrote, (
            "the mutated switcher drives `hidden` and the harness did not "
            "notice -- these tests are not measuring the attribute at all"
        )


# ---------------------------------------------------------------------------
# THE AVATAR LONG PRESS
# ---------------------------------------------------------------------------


def _run_press(*, down_on: str, up_on: str, hold: bool, mutate: bool = False) -> dict:
    """Drive the real press machinery and report which thing it opened.

    `down_on` / `up_on` are "avatar" or "island": where the press began and
    where it ended. They differ in the test that matters.
    """
    source = _press_machinery()
    if mutate:
        # THE MUTATION, and it is the same trap that caught this author on
        # tsk-6bjsvg: decide from where the press ENDED rather than where it
        # began. A finger travels during a 420ms hold.
        source = source.replace(
            'onAvatar = !!ev.target.closest(".ls-avatar");',
            "onAvatar = false;",
        ).replace(
            "if (onAvatar) openAgentMenu(el);",
            'if (upTarget && upTarget.closest(".ls-avatar")) openAgentMenu(el);',
        )

    program = (
        _DOM
        + r"""
var opened = [];
function openChat(a) { opened.push("chat"); }
function openDecision(a) { opened.push("decision"); }
function openAgentMenu(el) { opened.push("menu"); }
var RESTING = ["", "stopped", "idle", "exited", "error"];
var navigator = {};
var upTarget = null;

// A fake clock: the hold fires when we say it does, so the test does not sleep.
var pending = [];
var window = {
  setTimeout: function (fn, ms) { pending.push({ fn: fn, ms: ms }); return pending.length; },
  clearTimeout: function (id) { if (pending[id - 1]) pending[id - 1].fn = null; }
};

var island = makeEl({ sel: [".ls-island"] });
island.__agent = { name: "scout", status: "running" };
var avatar = makeEl({ sel: [".ls-avatar"], parent: island });
var agentsEl = makeEl({ sel: [] });

%(press)s

var DOWN = %(down)s === "avatar" ? avatar : island;
var UP = %(up)s === "avatar" ? avatar : island;
upTarget = UP;

agentsEl.fire("pointerdown", { target: DOWN, clientX: 10, clientY: 10 });
if (%(hold)s) {
  // Let the hold timer fire, the way 420ms of a real finger would.
  pending.forEach(function (p) { if (p.fn && p.ms === HOLD_MS) p.fn(); });
}
agentsEl.fire("pointerup", { target: UP, clientX: 10, clientY: 10 });

console.log(JSON.stringify({ opened: opened }));
"""
        % {
            "press": source,
            "down": json.dumps(down_on),
            "up": json.dumps(up_on),
            "hold": "true" if hold else "false",
        }
    )

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "press.js"
        path.write_text(program, encoding="utf-8")
        proc = subprocess.run(
            [_node(), str(path)], capture_output=True, text=True, timeout=60
        )
    if proc.returncode != 0:
        raise Failed("the press source threw:\n" + proc.stderr[-2000:])
    return json.loads(proc.stdout.strip().splitlines()[-1])


class TestAvatarLongPress:
    """A long press on the avatar opens the agent menu; elsewhere, the chat."""

    def test_holding_the_avatar_opens_the_menu(self):
        got = _run_press(down_on="avatar", up_on="avatar", hold=True)
        assert got["opened"] == ["menu"], got["opened"]

    def test_holding_the_island_still_opens_the_conversation(self):
        # The pre-existing behaviour. A guard that passes by disabling the long
        # press altogether would be caught here.
        got = _run_press(down_on="island", up_on="island", hold=True)
        assert got["opened"] == ["chat"], got["opened"]

    def test_a_short_tap_on_the_avatar_opens_the_conversation(self):
        # Tap and hold must stay different gestures, or the menu swallows the
        # ordinary way of opening an agent.
        got = _run_press(down_on="avatar", up_on="avatar", hold=False)
        assert got["opened"] == ["chat"], got["opened"]

    def test_the_press_is_judged_by_where_it_began(self):
        """THE ONE THAT MATTERS. A hold that starts on the avatar and drifts
        off it is still a press on the avatar.

        A 420ms hold is long enough for a fingertip to move several pixels, so
        deciding at the end would make the gesture mean different things
        depending on where the finger happened to settle. This is the same trap
        that made the tsk-6bjsvg fix subtle, and the reason it is latched.
        """
        got = _run_press(down_on="avatar", up_on="island", hold=True)
        assert got["opened"] == ["menu"], (
            "a hold that began on the avatar opened %r once the finger drifted "
            "onto the island -- the target is being read at the end, not "
            "latched at the start" % (got["opened"],)
        )

    def test_harness_observes_the_defect(self):
        """THE CONTROL: the end-target mutation must break the test above.

        On tsk-6bjsvg the specified red-case test passed under this exact
        mutation. The lesson was to drive a scenario where the start and the end
        DIFFER -- which is what the test above does, and this proves it.
        """
        got = _run_press(down_on="avatar", up_on="island", hold=True, mutate=True)
        assert got["opened"] != ["menu"], (
            "the end-target mutation still opened the menu, so the latch test "
            "above cannot fail on this defect and is not evidence of anything"
        )


# ---------------------------------------------------------------------------
# THE STATS: what the hardware does NOT expose must not be drawn
# ---------------------------------------------------------------------------


class TestStatsAreHonest:
    """No gauge without a counter behind it."""

    def test_the_first_cpu_reading_is_unknown_rather_than_zero(self):
        """A percentage needs two samples. The first call has only one.

        Returning 0.0 would put a bar at zero and claim the phone is idle, which
        is a measurement nobody took.
        """
        auth._CPU_LAST.clear()
        assert auth._read_cpu_percent() is None
        # The second call has a delta to work with and may legitimately be 0.0.
        second = auth._read_cpu_percent()
        assert second is None or 0.0 <= second <= 100.0

    def test_a_counter_that_went_backwards_yields_no_number(self):
        """/proc/stat is cumulative, and it does not always go forwards.

        Across a suspend -- which a phone does constantly -- the next reading
        can be lower than the one before it. Dividing by that negative delta
        produces a number between -inf and +inf which would be clamped into a
        plausible-looking percentage. It must be refused instead.
        """
        auth._CPU_LAST.clear()
        auth._read_cpu_percent()  # prime
        # Claim the previous total was far larger than anything /proc/stat can
        # currently report, so this call sees the counter run backwards.
        auth._CPU_LAST["total"] = 1e18
        auth._CPU_LAST["idle"] = 1e17
        assert auth._read_cpu_percent() is None

    def test_the_reading_stays_inside_nought_to_a_hundred(self):
        auth._CPU_LAST.clear()
        auth._read_cpu_percent()
        for _ in range(3):
            got = auth._read_cpu_percent()
            assert got is None or 0.0 <= got <= 100.0, got

    def test_no_npu_utilisation_is_ever_reported(self):
        """⛔ The finding this whole panel was designed around.

        Measured on the handset: remoteproc exposes name, state, firmware,
        coredump and recovery. There is no utilisation counter for the cDSP
        anywhere in mainline, so any percentage next to the word NPU would be
        fabricated. The accelerators are reported as STATES.
        """
        source = Path(auth.__file__).read_text(encoding="utf-8")
        for invented in ("npu_percent", "npu_usage", "npu_util", "dsp_percent"):
            assert invented not in source, (
                f"{invented!r} appears in the lock screen: there is no counter "
                "behind it and the number would be invented"
            )
        states = auth._read_dsp_states()
        for entry in states:
            assert set(entry) <= {"name", "state"}, entry
            assert not any(isinstance(v, (int, float)) for v in entry.values())

    def test_the_gpu_is_reported_as_a_clock_not_as_usage(self):
        """The devfreq node has no `load` file, so there is no busy percent.

        The field names have to keep saying that: `freq_hz` is a clock and
        `active_percent` is time above the idle clock since boot. Renaming
        either to something that reads as utilisation is the regression.
        """
        gpu = auth._read_gpu()
        if gpu is None:
            # No devfreq node on this host. The FIELD NAMES are still checkable
            # from the source, and that is what this test is really about -- a
            # skip here would let a rename to "gpu_usage" through in CI.
            source = Path(auth.__file__).read_text(encoding="utf-8")
            assert "gpu_percent" not in source and "gpu_usage" not in source
            return
        assert "usage" not in json.dumps(gpu)
        assert set(gpu) <= {"freq_hz", "max_freq_hz", "active_percent"}, gpu

    def test_time_above_the_idle_clock_is_derived_from_trans_stat(self, tmp_path):
        """The one real calculation in the panel, against a known table.

        Without this the parser is only ever exercised on hardware, and this
        host has no devfreq node -- so a broken trans_stat reader would skip in
        CI and be discovered on the phone. The numbers below are chosen so the
        right answer is not a round one: 1000ms at the lowest of 4000ms total
        is 75% above idle, and 75 is not what any off-by-one would produce.
        """
        node = tmp_path / "gpu"
        node.mkdir()
        (node / "cur_freq").write_text("450000000\n")
        (node / "max_freq").write_text("608000000\n")
        (node / "trans_stat").write_text(
            "     From  :   To\n"
            "           :  315000000  450000000  550000000  608000000   time(ms)\n"
            "*  315000000:          0          1          0          0       1000\n"
            "   450000000:          1          0          1          0       1500\n"
            "   550000000:          0          1          0          1       1000\n"
            "   608000000:          0          0          1          0        500\n"
            "Total transitions: 5\n"
        )
        import tinyagentos.routes.auth as mod

        old = mod._GPU_DEVFREQ
        try:
            mod._GPU_DEVFREQ = str(node)
            got = mod._read_gpu()
        finally:
            mod._GPU_DEVFREQ = old

        assert got["freq_hz"] == 450000000
        assert got["max_freq_hz"] == 608000000
        # 1000ms of 4000ms sits at the lowest state, so 75% is above it.
        assert got["active_percent"] == 75.0, got

    def test_a_devfreq_node_with_no_trans_stat_reports_only_the_clock(self, tmp_path):
        """Absent, not zero. A GPU that never left idle and a GPU we could not
        measure must not render as the same bar."""
        node = tmp_path / "gpu"
        node.mkdir()
        (node / "cur_freq").write_text("315000000\n")
        (node / "max_freq").write_text("608000000\n")
        import tinyagentos.routes.auth as mod

        old = mod._GPU_DEVFREQ
        try:
            mod._GPU_DEVFREQ = str(node)
            got = mod._read_gpu()
        finally:
            mod._GPU_DEVFREQ = old
        assert "active_percent" not in got, got

    def test_a_reading_that_cannot_be_taken_is_absent_not_zero(self, monkeypatch):
        """An absent key renders as "--". A zero renders as a confident lie."""
        monkeypatch.setattr(auth, "_read_cpu_percent", lambda: None)
        monkeypatch.setattr(auth, "_read_memory", lambda: None)
        monkeypatch.setattr(auth, "_read_gpu", lambda: None)
        monkeypatch.setattr(auth, "_read_dsp_states", lambda: [])

        import asyncio

        class _Req:
            pass

        monkeypatch.setattr(auth, "_request_is_console", lambda r: True)
        resp = asyncio.run(auth.lock_stats(_Req()))
        payload = json.loads(bytes(resp.body).decode())
        for key in ("cpu_percent", "memory", "gpu", "dsps", "models"):
            assert key not in payload, f"{key} was invented when nothing measured it"


class TestTheViewListCannotDrift:
    """One list of views, generated into both the markup and the script."""

    def test_every_tab_points_at_a_panel_that_exists(self):
        head = auth._lock_head_html()
        for _key, _label, _icon, panel in auth._LOCK_VIEWS:
            assert f'id="{panel}"' in head, f"no panel with id {panel}"
            assert f'aria-controls="{panel}"' in head, f"no tab controls {panel}"

    def test_the_script_is_served_with_the_view_list_filled_in(self):
        js = auth._lock_screen_js()
        assert auth._LOCK_VIEWS_TOKEN not in js, "the marker survived substitution"
        for key, _l, _i, _p in auth._LOCK_VIEWS:
            assert f'"{key}"' in js

    def test_a_missing_marker_fails_loudly_rather_than_at_runtime(self, monkeypatch):
        """Without this the switcher would throw on the first tap instead.

        A lock screen whose icons silently do nothing is far harder to trace
        back to a renamed constant than an exception naming the marker.
        """
        monkeypatch.setattr(auth, "_LOCK_SCREEN_SCRIPT", "(function(){})();")
        with pytest.raises(RuntimeError, match="__LOCK_VIEWS__"):
            auth._lock_screen_js()


class TestTheTopEdgeIsMeasured:
    """The status row sits on the camera line, and the cards are capped once.

    Both numbers came from a measurement rather than from nudging something
    until it looked right, so both are re-derived here: if someone changes the
    token, this says WHICH measurement they just contradicted.
    """

    #: Nothing's own `config_mainBuiltInDisplayCutout` for spacewar, verbatim,
    #: out of the stock FrameworksResCommon_Sys_Spacewar.apk overlay (and
    #: byte-identical in LineageOS's device tree -- two independent sources).
    #: Android reads this in PHYSICAL pixels unless it ends `@dp`; `@left` only
    #: moves the origin to the top-left of the panel.
    CUTOUT_PATH = (
        "M89.3,42.31 m0,26.19 a26.19,26.19 0 1,0 52.38,0 a26.19,26.19 0 1,0 -52.38,0 Z @left"
    )
    #: sway's scale for DSI-1, measured on the handset with `swaymsg -t
    #: get_outputs`: the panel is 1080x2400 and the page sees 540x1200.
    OUTPUT_SCALE = 2.0

    def _token(self, name: str) -> float:
        m = re.search(rf"{re.escape(name)}:\s*([0-9.]+)px", auth._LOCK_SCREEN_STYLE)
        assert m, f"{name} is gone from the lock screen stylesheet"
        return float(m.group(1))

    def test_the_status_row_is_centred_on_the_camera(self):
        """Derived from the cutout path, not copied from the stylesheet."""
        start_y = float(re.match(r"M[\d.]+,([\d.]+)", self.CUTOUT_PATH).group(1))
        # The `m` hop moves the pen down one radius, to the circle's left-hand
        # point, so the centre's y is the pen's y after the hop.
        hop_y = float(re.search(r" m[\d.-]+,([\d.-]+)", self.CUTOUT_PATH).group(1))
        centre_y_physical = start_y + hop_y
        assert centre_y_physical == 68.50

        expected_css_px = centre_y_physical / self.OUTPUT_SCALE
        assert self._token("--ls-cam-centre-y") == pytest.approx(expected_css_px)

    def test_the_padding_puts_that_centre_where_the_camera_is(self):
        """The row's CENTRE, not its top edge -- half its height is subtracted.

        Asserted on the formula because the alternative is a number: a literal
        padding that happens to be right today is exactly what stops being right
        the moment the row's height changes.
        """
        style = auth._LOCK_SCREEN_STYLE
        assert "var(--ls-cam-centre-y) - var(--ls-status-h) / 2" in style
        assert "min-height: var(--ls-status-h)" in style

    def test_the_card_cap_still_fits_the_panel(self):
        """436px of card inside a 540px viewport, less .lockscreen's padding."""
        viewport_css_px = 1080 / self.OUTPUT_SCALE
        side_padding = 10
        assert self._token("--ls-card-w") <= viewport_css_px - 2 * side_padding
