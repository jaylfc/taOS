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

from tests.test_lock_screen_gestures import _balanced, _function


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
_HARNESS = r"""
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
    textContent: "",
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
    }
  };
  Object.defineProperty(el, "firstChild", {
    get: function () { return this.children.length ? this.children[0] : null; }
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
  agentsEl.children.length = 0;
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
