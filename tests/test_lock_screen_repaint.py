"""The lock screen's 15s repaint, EXECUTED rather than grepped for.

Jay, from the glass: "the agent islands flicker occasionally". They did, every
fifteen seconds. `paintActivity()` wiped `#ls-agents` with `textContent = ""`
and appended a freshly built island for every agent, so every island was a NEW
DOM node -- and `.ls-island` carries `ls-island-in`, a 520ms entrance animation
with staggered per-child delays. A new node replays it. Six agents, six
entrances, every poll, whether or not one byte of the payload had changed.

Measured in a real browser before the fix (chromium at 540x1200, the device's
own CSS viewport, six agents, nothing touched, one poll cycle): six of six
islands fired `animationstart` and the first island was no longer the same node.
After: zero `animationstart`, same node.

**These tests assert on NODE IDENTITY, not on rendered values.** "The names are
still right" passes on the broken code too -- it was always rebuilding the list
correctly, that was the whole problem. Identity is the only thing that
distinguishes a repaint that flickers from one that does not.

Like `test_lock_screen_gestures.py`, the real source runs under node against a
DOM stand-in, and `test_harness_observes_the_defect` puts the wipe back and
requires identity to break. Without that control a stub that quietly does
nothing would report every property below as satisfied.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from tinyagentos.routes.auth import _LOCK_SCREEN_SCRIPT as LOCK_SCRIPT

from test_lock_screen_gestures import _balanced, _function


def _var(name: str) -> str:
    """The source of a `var <name> = ...;` declaration."""
    head = f"var {name} = "
    assert head in LOCK_SCRIPT, f"{head!r} is gone from the lock screen script"
    i = LOCK_SCRIPT.index(head)
    return LOCK_SCRIPT[i : LOCK_SCRIPT.index(";", i) + 1]


#: A DOM only as real as island building and reconciliation need.
#:
#: It is deliberately a real tree -- children arrays, insertBefore, remove --
#: because the properties under test are about WHICH NODES SURVIVE and in what
#: order. A stub that returned plausible values without maintaining a tree could
#: not tell a reconcile from a rebuild, which is the one thing this file exists
#: to tell apart.
_DOM = r"""
var NODE_SEQ = 0;

function makeNode(tag) {
  var el = {
    tagName: tag,
    __id: ++NODE_SEQ,          // identity, which is what the assertions read
    className: "",
    children: [],
    parent: null,
    _attrs: {},
    style: { setProperty: function () {} },
    classList: { contains: function () { return false; } },
    innerHTML: "",
    _text: "",
    addEventListener: function () {},
    focus: function () {},
    setAttribute: function (k, v) { this._attrs[k] = String(v); },
    getAttribute: function (k) {
      return Object.prototype.hasOwnProperty.call(this._attrs, k) ? this._attrs[k] : null;
    },
    hasAttribute: function (k) {
      return Object.prototype.hasOwnProperty.call(this._attrs, k);
    },
    removeAttribute: function (k) { delete this._attrs[k]; },
    appendChild: function (c) {
      if (c.parent) c.parent.removeChild(c);
      c.parent = this; this.children.push(c); return c;
    },
    removeChild: function (c) {
      var i = this.children.indexOf(c);
      if (i !== -1) this.children.splice(i, 1);
      c.parent = null; return c;
    },
    remove: function () { if (this.parent) this.parent.removeChild(this); },
    insertBefore: function (c, ref) {
      if (c.parent) c.parent.removeChild(c);
      c.parent = this;
      var i = ref ? this.children.indexOf(ref) : -1;
      if (i === -1) this.children.push(c); else this.children.splice(i, 0, c);
      return c;
    },
    // Enough of a selector engine for `.ls-status`, which is what applyAgent
    // reaches for. Depth-first, class names only.
    querySelector: function (sel) {
      var want = sel.replace(/^\./, "");
      for (var i = 0; i < this.children.length; i++) {
        var c = this.children[i];
        if (String(c.className).split(/\s+/).indexOf(want) !== -1) return c;
        var deep = c.querySelector(sel);
        if (deep) return deep;
      }
      return null;
    },
    // `.cls` and `.cls[attr]` -- enough for the notification clock sweep,
    // which is the only querySelectorAll the repaint code performs.
    querySelectorAll: function (sel) {
      var m = /^\.([A-Za-z0-9_-]+)(?:\[([A-Za-z0-9_-]+)\])?$/.exec(sel);
      if (!m) throw new Error("harness cannot match selector: " + sel);
      var out = [];
      (function walk(node) {
        for (var i = 0; i < node.children.length; i++) {
          var c = node.children[i];
          var hasClass = String(c.className).split(/\s+/).indexOf(m[1]) !== -1;
          if (hasClass && (!m[2] || c.hasAttribute(m[2]))) out.push(c);
          walk(c);
        }
      })(this);
      return out;
    }
  };
  // Assigning textContent REMOVES EVERY CHILD -- that is the whole mechanism
  // of the wipe these tests exist to detect. Modelled as a plain string
  // property it did not, so `notifsEl.textContent = ""` cleared nothing and a
  // panel that failed to empty would have looked empty to the harness.
  Object.defineProperty(el, "textContent", {
    get: function () { return this._text; },
    set: function (v) {
      while (this.children.length) this.removeChild(this.children[0]);
      this._text = String(v);
    }
  });
  Object.defineProperty(el, "firstChild", {
    get: function () { return this.children.length ? this.children[0] : null; }
  });
  Object.defineProperty(el, "lastChild", {
    get: function () {
      return this.children.length ? this.children[this.children.length - 1] : null;
    }
  });
  Object.defineProperty(el, "nextSibling", {
    get: function () {
      if (!this.parent) return null;
      var i = this.parent.children.indexOf(this);
      return (i === -1 || i + 1 >= this.parent.children.length)
        ? null : this.parent.children[i + 1];
    }
  });
  return el;
}

var document = {
  createElement: makeNode,
  createElementNS: function (_ns, tag) { return makeNode(tag); },
  activeElement: null
};
var CSS = null;
var window = {};
"""

#: The island driver: the shared DOM, the island machinery, and one snapshot
#: of `#ls-agents` per payload.
_HARNESS = _DOM + r"""
var agentsEl = makeNode("div");

__SOURCE__

// How many ISLANDS were constructed -- not how many DOM nodes, which is a much
// larger and far less interesting number (an island is a dozen elements). The
// real `island()` is wrapped rather than edited, so what runs is still the
// shipped function.
var ISLANDS_BUILT = 0;
var __realIsland = island;
island = function (agent) { ISLANDS_BUILT += 1; return __realIsland(agent); };

// Each tick is one payload. After every one, record what the list looks like
// AND the identity of every node in it, so the test can compare across ticks.
var SCN = JSON.parse(process.env.LS_REPAINT);
var snapshots = [];
for (var t = 0; t < SCN.ticks.length; t++) {
  __PAINT__(SCN.ticks[t]);
  snapshots.push(agentsEl.children.map(function (el) {
    return {
      id: el.__id,
      agent: el.getAttribute("data-agent"),
      state: el.getAttribute("data-state"),
      attention: el.getAttribute("data-attention"),
      label: el.getAttribute("aria-label"),
      status: (el.querySelector(".ls-status") || {}).textContent,
      record: el.__agent ? el.__agent.name : null
    };
  }));
}
process.stdout.write(JSON.stringify({ snapshots: snapshots, built: ISLANDS_BUILT }));
"""

#: The repaint as it stood before the fix: wipe the container, rebuild each
#: island, append in order. Used only by the control.
_WIPE_AND_REBUILD = r"""
function wipeAndRebuild(agents) {
  agentsEl.textContent = "";
  for (var i = 0; i < agents.length; i++) {
    agentsEl.appendChild(island(agents[i]));
  }
}
"""


def _source(*, reconciled: bool = True) -> str:
    """The real island machinery, with either repaint strategy wired in."""
    parts = [
        _var("FRAMEWORKS"),
        _var("RESTING"),
        _function("hueFor"),
        _function("initials"),
        _function("islandIdentity"),
        _function("applyAgent"),
        _function("setAttrIfChanged"),
        _function("island"),
        # The shared reconcile helpers, named here rather than arriving by
        # accident: until `_balanced` learned about comments, `_function`
        # over-captured and swallowed these along with island(). The suite
        # passed for the wrong reason, which is the reason it is spelled out.
        _function("placeInOrder"),
        _function("partOf"),
        _function("setText"),
    ]
    if reconciled:
        parts.append(_function("reconcileIslands"))
    else:
        parts.append(_WIPE_AND_REBUILD)
    return "\n".join(parts)


def _paint(ticks: list, *, reconciled: bool = True) -> dict:
    """Run a sequence of payloads through the repaint and report each tick."""
    node = shutil.which("node")
    if node is None:  # pragma: no cover - depends on the runner image
        # FAIL, do not skip. A skipped test here reads as green to the shard
        # summary while proving nothing at all about the repaint.
        pytest.fail(
            "node is required to execute the lock screen repaint source, and was "
            "not found on PATH. These tests cannot be skipped: skipping them "
            "would report green while proving nothing about the flicker."
        )
    script = _HARNESS.replace("__SOURCE__", _source(reconciled=reconciled)).replace(
        "__PAINT__", "reconcileIslands" if reconciled else "wipeAndRebuild"
    )
    done = subprocess.run(
        [node, "-e", script],
        env={**os.environ, "LS_REPAINT": json.dumps({"ticks": ticks})},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert done.returncode == 0, f"node failed:\n{done.stderr}"
    return json.loads(done.stdout)


def _agent(name: str, **over) -> dict:
    base = {"name": name, "status": "running", "framework": "", "avatar": ""}
    base.update(over)
    return base


SIX = [
    _agent("taOS Agent", system=True),
    _agent("Scout", status="idle"),
    _agent("Ledger"),
    _agent("Relay"),
    _agent("Courier", status="idle"),
    _agent("Nightwatch"),
]


def _ids(snapshot) -> list:
    return [row["id"] for row in snapshot]


def _names(snapshot) -> list:
    return [row["agent"] for row in snapshot]


class TestIslandsSurviveTheRepaint:
    """Bug 2: the islands must not be rebuilt by a poll that changed nothing."""

    def test_an_unchanged_payload_keeps_every_island_node(self):
        """THE RED CASE, and Jay's report verbatim.

        Six agents, polled twice, nothing different between the polls. Every
        island must be the same DOM node afterwards. On the old code all six
        were new nodes and all six replayed their 520ms entrance -- which is
        what the flicker was.
        """
        out = _paint([SIX, SIX])
        assert _ids(out["snapshots"][0]) == _ids(out["snapshots"][1])

    def test_an_unchanged_payload_builds_no_new_islands_at_all(self):
        """Identity could be preserved by luck if nodes were reused from a pool.

        Counting construction says it outright: the second tick must not build
        anything. This is also what makes the assertion above cheap to trust --
        six islands built in total, not twelve.
        """
        out = _paint([SIX, SIX])
        assert out["built"] == len(SIX)

    def test_a_changed_status_is_written_into_the_SAME_island(self):
        """The fix must update in place, not swap the element for a new one.

        This is the assertion that would fail on a "fix" that compared payloads
        and skipped the repaint entirely: skipping keeps identity and drops the
        update, so identity and freshness are asserted together, in one tick.
        """
        second = [dict(a) for a in SIX]
        second[1] = _agent("Scout", status="thinking")
        out = _paint([SIX, second])
        before, after = out["snapshots"]
        assert _ids(before) == _ids(after), "the island was replaced, not updated"
        assert after[1]["status"] == "thinking"
        assert after[1]["state"] == "busy", "a working agent must read as busy"
        assert "thinking" in after[1]["label"], "the accessible name went stale"

    def test_the_record_the_handlers_read_is_refreshed_in_place(self):
        """`el.__agent` is what a press opens, and it is not visible.

        The island's handlers read the whole record off the element rather than
        looking it up by name. Reconciling without refreshing it would leave the
        screen correct and the SHEET stale -- opening an agent's conversation on
        fifteen-second-old state, which nothing on the glass would reveal.
        """
        second = [dict(a) for a in SIX]
        second[2] = _agent("Ledger", status="waiting")
        out = _paint([SIX, second])
        assert out["snapshots"][1][2]["record"] == "Ledger"
        assert out["snapshots"][1][2]["status"] == "waiting"

    def test_attention_is_cleared_when_it_goes_away(self):
        """Attributes that are ADDED must also be REMOVED.

        A reconcile writes over what it finds, so a flag set on one tick and
        absent from the next survives unless it is explicitly cleared. The wipe
        got this right for free -- it threw the element away -- so it is exactly
        the class of regression that switching to reconciliation introduces.
        """
        first = [dict(a) for a in SIX]
        first[3] = _agent("Relay", attention=True,
                          decision={"question": "deploy to prod?"})
        out = _paint([first, SIX])
        assert out["snapshots"][0][3]["attention"] == "1"
        assert out["snapshots"][1][3]["attention"] is None
        assert "needs a decision" not in out["snapshots"][1][3]["label"]

    def test_a_new_agent_is_added_without_disturbing_the_others(self):
        out = _paint([SIX, SIX + [_agent("Sentry")]])
        before, after = out["snapshots"]
        assert _names(after)[-1] == "Sentry"
        assert _ids(after)[: len(SIX)] == _ids(before), "the existing islands were rebuilt"
        assert out["built"] == len(SIX) + 1

    def test_a_departed_agent_is_removed_without_disturbing_the_others(self):
        fewer = [a for a in SIX if a["name"] != "Ledger"]
        out = _paint([SIX, fewer])
        before, after = out["snapshots"]
        assert "Ledger" not in _names(after)
        assert _ids(after) == [i for i, r in zip(_ids(before), before)
                               if r["agent"] != "Ledger"]

    def test_a_reordered_payload_moves_islands_without_rebuilding_them(self):
        """Order follows the payload, and a move is not a rebuild.

        `insertBefore` on a node already in position would re-insert it and
        restart its animation, so the reconcile has to check before it moves.
        Reordering is the case where that check is load-bearing.
        """
        flipped = list(reversed(SIX))
        out = _paint([SIX, flipped])
        before, after = out["snapshots"]
        assert _names(after) == [a["name"] for a in flipped]
        assert sorted(_ids(after)) == sorted(_ids(before)), "islands were rebuilt to reorder"
        assert out["built"] == len(SIX)

    def test_a_reconfigured_agent_IS_rebuilt(self):
        """The deliberate exception, so it is a decision and not an oversight.

        An avatar or a framework badge is built once and never mutated, so when
        one of those changes the element really is wrong and rebuilding it is
        the honest answer. It comes from configuration, not from a tick, so it
        does not flicker in practice.
        """
        second = [dict(a) for a in SIX]
        second[4] = _agent("Courier", status="idle", avatar="/static/courier.png")
        out = _paint([SIX, second])
        before, after = out["snapshots"]
        assert _ids(after)[4] != _ids(before)[4], "the reconfigured island was not rebuilt"
        assert _ids(after)[:4] == _ids(before)[:4], "its neighbours were rebuilt too"

    def test_harness_observes_the_defect(self):
        """THE CONTROL. Put the wipe back and identity must break.

        Every assertion above is about nodes surviving. If this stand-in ever
        stopped maintaining a real tree, "the nodes survived" and "nothing
        happened at all" would read identically and the whole file would pass
        while measuring nothing. Under the old wipe-and-rebuild, the same
        unchanged payload must produce six entirely new islands.
        """
        out = _paint([SIX, SIX], reconciled=False)
        before, after = out["snapshots"]
        assert _names(after) == _names(before), "the old code did render the right names"
        assert not set(_ids(after)) & set(_ids(before)), "no island should have survived"
        assert out["built"] == 2 * len(SIX)


# ---------------------------------------------------------------------------
# BUG 2b: the same disease in the pollers Jay reported next.
#
# "the system stats widget flickers too" -- said in the same breath as the
# islands, and it is the same defect one view over. `paintStats()` wiped
# `#ls-stats` and rebuilt the card, and `.ls-stat-card` carries the very same
# 520ms `ls-island-in` entrance. The difference is the cadence: the stats poll
# runs every THREE seconds, not fifteen.
#
# Measured in chromium at 540x1200 before the fix, stats view, nothing touched:
# two `ls-island-in` replays in 7.5s (2992ms apart -- the poll), and the card
# was a different, DETACHED node each time. `.ls-stat-fill` was new each time
# too, so its `transition: width 420ms` never ran and the meters snapped.
# ---------------------------------------------------------------------------

_STATS_HARNESS = _DOM + r"""
var statsEl = makeNode("div");
function syncFeedFade() {}

__SOURCE__

// Restores the wipe, and nothing else. partOf() can then find nothing, so
// every part is rebuilt -- which is precisely the old behaviour.
var __shippedPaintStats = paintStats;
function wipeAndPaintStats(d) {
  statsEl.textContent = "";
  __shippedPaintStats(d);
}

function snap(el) {
  return {
    id: el.__id,
    part: el.getAttribute("data-part"),
    cls: el.className,
    text: el.textContent,
    width: el.style && el.style.width ? el.style.width : null,
    kids: el.children.map(snap)
  };
}

var SCN = JSON.parse(process.env.LS_STATS);
var snapshots = [];
for (var t = 0; t < SCN.ticks.length; t++) {
  __PAINT__(SCN.ticks[t]);
  snapshots.push(statsEl.children.map(snap));
}
process.stdout.write(JSON.stringify({ snapshots: snapshots }));
"""


def _stats_source() -> str:
    return "\n".join([
        _function("placeInOrder"),
        _function("partOf"),
        _function("setText"),
        _function("statRow"),
        _function("statNote"),
        _function("gib"),
        _function("paintStats"),
    ])


def _paint_stats(ticks: list, *, reconciled: bool = True) -> dict:
    node = shutil.which("node")
    if node is None:  # pragma: no cover - depends on the runner image
        pytest.fail(
            "node is required to execute the lock screen stats repaint, and was "
            "not found on PATH. Skipping would report green while proving "
            "nothing about the flicker Jay reported."
        )
    script = _STATS_HARNESS.replace("__SOURCE__", _stats_source()).replace(
        "__PAINT__", "paintStats" if reconciled else "wipeAndPaintStats"
    )
    done = subprocess.run(
        [node, "-e", script],
        env={**os.environ, "LS_STATS": json.dumps({"ticks": ticks})},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert done.returncode == 0, f"node failed:\n{done.stderr}"
    return json.loads(done.stdout)


def _reading(cpu=41.0, mem_pct=63.0, dsps=("adsp", "cdsp"), gpu=True) -> dict:
    d = {
        "cpu_percent": cpu,
        "cpu_cores": 8,
        "memory": {"used_kb": 5033164, "total_kb": 7902168, "percent": mem_pct},
        "models": ["qwen2.5:3b"],
        "dsps": [{"name": n, "state": "running"} for n in dsps],
    }
    if gpu:
        d["gpu"] = {"freq_hz": 305000000, "max_freq_hz": 812000000,
                    "active_percent": 12.5}
    return d


def _find(rows, part):
    for row in rows:
        if row["part"] == part:
            return row
    return None


def _card(snapshot):
    return _find(snapshot, "card")


class TestStatsSurviveTheRepaint:
    """Node identity across a poll -- not "the numbers are right".

    The broken code rendered the right numbers too. It rendered them into a
    brand new card every three seconds, which is the whole complaint.
    """

    def test_an_unchanged_reading_keeps_the_stats_card(self):
        before, after = _paint_stats([_reading(), _reading()])["snapshots"]
        assert _card(after)["id"] == _card(before)["id"], (
            "the stats card was rebuilt, so it replays its 520ms entrance"
        )

    def test_a_CHANGED_reading_still_keeps_the_stats_card(self):
        """The poll exists to deliver new numbers; new numbers are the norm.

        A fix that only held still for an identical payload would flicker on
        every real device, where the CPU percentage moves every single tick.
        """
        before, after = _paint_stats([_reading(cpu=41.0), _reading(cpu=78.0)])["snapshots"]
        assert _card(after)["id"] == _card(before)["id"]
        cpu = _find(_card(after)["kids"], "cpu")
        assert cpu["kids"][0]["kids"][1]["text"] == "78%", "the new reading must land"

    def test_every_stat_row_keeps_its_node_across_a_changed_reading(self):
        before, after = _paint_stats([_reading(cpu=41.0), _reading(cpu=78.0)])["snapshots"]
        for part in ("cpu", "memory", "gpu"):
            assert _find(_card(after)["kids"], part)["id"] == \
                _find(_card(before)["kids"], part)["id"], f"{part} row was rebuilt"

    def test_the_meter_keeps_its_node_so_its_width_can_animate(self):
        """`.ls-stat-fill` has `transition: width 420ms`.

        A transition needs a FROM value, which only a surviving node has. A
        fresh node starts at its final width and the bar snaps there -- which
        is what every meter did before this fix.
        """
        before, after = _paint_stats([_reading(cpu=10.0), _reading(cpu=90.0)])["snapshots"]
        fill_before = _find(_card(before)["kids"], "cpu")["kids"][1]["kids"][0]
        fill_after = _find(_card(after)["kids"], "cpu")["kids"][1]["kids"][0]
        assert fill_after["id"] == fill_before["id"], "the meter was rebuilt"
        assert fill_before["width"] == "10%" and fill_after["width"] == "90%"

    def test_a_reading_that_stops_being_measured_loses_its_bar(self):
        """A meter left at its last value goes on reporting a dead measurement.

        This is the same trap as a check that passes because it measured
        nothing: an idle 0% and an unmeasured 0% must not look alike, and a
        STALE 63% must not look like a live one.
        """
        ticks = [_reading(mem_pct=63.0), _reading()]
        ticks[1]["memory"] = None
        before, after = _paint_stats(ticks)["snapshots"]
        assert len(_find(_card(before)["kids"], "memory")["kids"]) == 2, "bar expected"
        mem = _find(_card(after)["kids"], "memory")
        assert len(mem["kids"]) == 1, "the track must go when the number does"
        assert mem["kids"][0]["kids"][1]["text"] == "--"

    def test_chips_are_keyed_so_a_steady_accelerator_is_not_rebuilt(self):
        before, after = _paint_stats([_reading(), _reading()])["snapshots"]
        chips_before = _find(before, "chips")
        chips_after = _find(after, "chips")
        assert chips_after["id"] == chips_before["id"]
        assert [c["id"] for c in chips_after["kids"]] == \
            [c["id"] for c in chips_before["kids"]]
        assert [c["part"] for c in chips_after["kids"]] == ["adsp", "cdsp"]

    def test_accelerators_that_go_away_take_their_caption_with_them(self):
        before, after = _paint_stats(
            [_reading(dsps=("adsp", "cdsp")), _reading(dsps=())])["snapshots"]
        assert _find(before, "chips") is not None
        assert _find(after, "chips") is None, "chips must not outlive their DSPs"
        assert _find(after, "accel-note") is None, "nor may their caption"
        assert _find(after, "models") is not None, "the rest of the panel stays"

    def test_the_gpu_note_appears_and_disappears_without_rebuilding_the_card(self):
        ticks = [_reading(), _reading(gpu=False)]
        before, after = _paint_stats(ticks)["snapshots"]
        assert _find(_card(before)["kids"], "gpu-note") is not None
        assert _find(_card(after)["kids"], "gpu-note") is None
        assert _card(after)["id"] == _card(before)["id"]
        assert _find(_card(after)["kids"], "gpu")["kids"][0]["kids"][1]["text"] == "--"

    def test_the_shipped_source_no_longer_wipes_the_panel(self):
        """The control below is only a mutation while this is true.

        If the wipe ever comes back to `paintStats`, `wipeAndPaintStats` stops
        changing anything and the control would pass by doing nothing at all.
        """
        shipped = _function("paintStats")
        assert 'statsEl.textContent = ""' not in shipped, (
            "the wholesale wipe is back in paintStats, which makes the control "
            "below a no-op: it would 'prove' the defect is observable while "
            "testing identical code"
        )

    def test_harness_observes_the_defect(self):
        """Restore the wipe and every property above must break.

        Without this the suite could not tell a reconcile from a stub that
        quietly did nothing.
        """
        out = _paint_stats([_reading(), _reading()], reconciled=False)
        before, after = out["snapshots"]
        # It still renders correctly -- that was never the problem.
        assert [p["part"] for p in after] == [p["part"] for p in before]
        assert _card(after)["id"] != _card(before)["id"], (
            "the wipe must produce a new card, or it is not the old behaviour"
        )
        assert not {k["id"] for k in _card(after)["kids"]} & \
            {k["id"] for k in _card(before)["kids"]}, "no row should have survived"


# ---------------------------------------------------------------------------
# BUG 2b, second poller: the notification stacks.
#
# `.ls-notif-group` carries the same 520ms `ls-island-in` entrance as an
# island and a stats card, and `paintNotifications()` wiped `#ls-notifs` and
# rebuilt every stack. This poll runs every fifteen MINUTES, so it is the
# least often seen of the three -- but a stack is also a `role="button"` with
# a tabindex, so the wipe threw away keyboard focus as well as animating.
#
# A stack whose notifications genuinely changed SHOULD animate: that is new
# content arriving. One that did not change must not move at all.
# ---------------------------------------------------------------------------

_NOTIF_HARNESS = _DOM + r"""
var notifsEl = makeNode("div");
var screenEl = makeNode("div");
screenEl.setAttribute("data-sheet", "none");
function syncFeedFade() {}

__SOURCE__

var __shippedPaint = paintNotifications;
function wipeAndPaintNotifications(d) {
  notifsEl.textContent = "";
  __shippedPaint(d);
}

function snap(el) {
  return {
    id: el.__id,
    source: el.getAttribute("data-source"),
    identity: el.getAttribute("data-identity"),
    open: el.getAttribute("data-open"),
    kids: el.children.map(function (c) { return c.__id; })
  };
}

var SCN = JSON.parse(process.env.LS_NOTIFS);
var snapshots = [];
var clocks = [];
for (var t = 0; t < SCN.ticks.length; t++) {
  __PAINT__(SCN.ticks[t]);
  snapshots.push(notifsEl.children.map(snap));
  clocks.push(notifClocks.map(function (c) { return { id: c.el.__id, at: c.at }; }));
}
process.stdout.write(JSON.stringify({
  snapshots: snapshots, clocks: clocks, hidden: !!notifsEl.hidden
}));
"""


def _notif_source() -> str:
    return "\n".join([
        _var("NOTIF_GLYPHS"),
        _var("notifOpen"),
        _var("notifClocks"),
        _function("placeInOrder"),
        _function("partOf"),
        _function("setText"),
        _function("whenText"),
        _function("notifCard"),
        _function("notifGroup"),
        _function("notifIdentity"),
        _function("paintNotifications"),
    ])


def _paint_notifs(ticks: list, *, reconciled: bool = True) -> dict:
    node = shutil.which("node")
    if node is None:  # pragma: no cover - depends on the runner image
        pytest.fail(
            "node is required to execute the notification repaint, and was not "
            "found on PATH. Skipping would report green while proving nothing."
        )
    script = _NOTIF_HARNESS.replace("__SOURCE__", _notif_source()).replace(
        "__PAINT__", "paintNotifications" if reconciled else "wipeAndPaintNotifications"
    )
    done = subprocess.run(
        [node, "-e", script],
        env={**os.environ, "LS_NOTIFS": json.dumps({"ticks": ticks})},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert done.returncode == 0, f"node failed:\n{done.stderr}"
    return json.loads(done.stdout)


def _group(source, app, *items):
    return {
        "source": source,
        "app": app,
        "mono": app[:2],
        "glyph": "",
        "items": [
            {"at": 1789580000 - (i * 600), "title": t, "text": "body " + t}
            for i, t in enumerate(items)
        ],
    }


def _stacks(**over):
    base = [
        _group("mail", "Mail", "Invoice 4021", "Re: shipping"),
        _group("sms", "Messages", "On my way"),
    ]
    base[0].update(over.get("mail", {}))
    return {"groups": base}


def _by_source(snapshot, source):
    for row in snapshot:
        if row["source"] == source:
            return row
    return None


class TestNotificationStacksSurviveTheRepaint:

    def test_an_unchanged_payload_keeps_every_stack_node(self):
        before, after = _paint_notifs([_stacks(), _stacks()])["snapshots"]
        assert [r["id"] for r in after] == [r["id"] for r in before], (
            "a stack was rebuilt, so it replays its 520ms entrance and drops focus"
        )

    def test_an_unchanged_payload_keeps_the_CARDS_inside_a_stack(self):
        before, after = _paint_notifs([_stacks(), _stacks()])["snapshots"]
        assert _by_source(after, "mail")["kids"] == _by_source(before, "mail")["kids"]

    def test_a_stack_whose_notifications_CHANGED_is_rebuilt(self):
        """New content is exactly what the entrance animation is for.

        The property is not "never rebuild"; it is "rebuild only what changed".
        """
        first = _stacks()
        second = _stacks(mail={"items": _group(
            "mail", "Mail", "Invoice 4021", "Re: shipping", "New thing")["items"]})
        before, after = _paint_notifs([first, second])["snapshots"]
        assert _by_source(after, "mail")["id"] != _by_source(before, "mail")["id"]
        # ...and its untouched neighbour must NOT be dragged along with it.
        assert _by_source(after, "sms")["id"] == _by_source(before, "sms")["id"]

    def test_a_departed_stack_is_removed_without_disturbing_the_others(self):
        second = {"groups": [_group("sms", "Messages", "On my way")]}
        before, after = _paint_notifs([_stacks(), second])["snapshots"]
        assert _by_source(after, "mail") is None
        assert _by_source(after, "sms")["id"] == _by_source(before, "sms")["id"]

    def test_a_reordered_payload_moves_stacks_without_rebuilding_them(self):
        first = _stacks()
        second = {"groups": [first["groups"][1], first["groups"][0]]}
        before, after = _paint_notifs([first, second])["snapshots"]
        assert [r["source"] for r in after] == ["sms", "mail"]
        assert {r["id"] for r in after} == {r["id"] for r in before}

    def test_the_minute_labels_are_collected_from_what_is_on_screen(self):
        """The clock list must cover stacks the paint deliberately left alone.

        Before the fix it was whatever `notifCard` happened to push while
        building. Once a stack is NOT rebuilt nothing is pushed for it, so a
        list built that way would silently stop retouching its minutes -- and
        a clock frozen at "2h ago" reads exactly like a working one.
        """
        out = _paint_notifs([_stacks(), _stacks()])
        first_tick, second_tick = out["clocks"]
        assert len(second_tick) == 3, "three notifications, three clocks"
        assert [c["id"] for c in second_tick] == [c["id"] for c in first_tick]
        assert all(isinstance(c["at"], int) and c["at"] > 0 for c in second_tick)

    def test_an_empty_payload_clears_the_stack_and_its_clocks(self):
        out = _paint_notifs([_stacks(), {"groups": []}])
        assert out["snapshots"][1] == []
        assert out["clocks"][1] == []
        assert out["hidden"] is True

    def test_the_shipped_source_reconciles_rather_than_wipes(self):
        """Keeps the control below honest: if reconciliation is removed, this
        says so in one line instead of leaving the control silently comparing
        identical code against itself."""
        shipped = _function("paintNotifications")
        assert "placeInOrder(notifsEl, want)" in shipped

    def test_harness_observes_the_defect(self):
        out = _paint_notifs([_stacks(), _stacks()], reconciled=False)
        before, after = out["snapshots"]
        assert [r["source"] for r in after] == [r["source"] for r in before], (
            "the old code did render the right stacks"
        )
        assert not {r["id"] for r in after} & {r["id"] for r in before}, (
            "the wipe must rebuild every stack, or it is not the old behaviour"
        )
