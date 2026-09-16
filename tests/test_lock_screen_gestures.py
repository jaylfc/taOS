"""The lock screen's touch gestures, EXECUTED rather than grepped for.

tsk-6bjsvg: a long upward drag to read to the end of the notification feed was
being read as swipe-up-to-unlock, throwing the reader into the PIN keypad. The
unlock gesture is bound to `document.body`, so the feed had no say in it.

These tests run the real gesture source out of `_LOCK_SCREEN_SCRIPT` under node
against a small DOM stand-in. That is more machinery than asserting on the
script text, and it is here for one reason: a string assertion for the fix
passes whether or not the fix WORKS. It cannot tell a touchstart latch from a
touchend one, and the touchend version is the plausible wrong answer -- the
finger leaves the feed during exactly the drag we mean to exclude.

Because a stub DOM can pass everything by doing nothing, `test_harness_observes_
the_defect` re-runs the failing scenario against the gesture source with the fix
STRIPPED BACK OUT and requires it to unlock. If the stub ever goes inert, that
control fails first and says so.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

import pytest

from tinyagentos.routes.auth import _LOCK_SCREEN_SCRIPT as LOCK_SCRIPT


def _balanced(src: str, start: int, opener: str, closer: str) -> str:
    """Return src[start:] through the balanced close of the first `opener`.

    Quote-aware, because a brace inside a string literal would otherwise end the
    span early and hand the caller a slice that happens to parse.

    COMMENT-aware for the same reason, and it is not hypothetical: the comment
    ``// The App Store's own artwork`` inside ``island()`` opened an apostrophe
    that never closed, so every brace after it was read as string content and
    ``_function("island")`` quietly returned 12kB -- the whole of island(),
    reconcileIslands() AND paintActivity(). It still parsed, and every test
    still passed, because the extra functions were the real ones. A harness
    that hands back three times what it was asked for is not measuring what it
    claims to; the next divergence would not be so harmless.
    """
    i = src.index(opener, start)
    depth, quote = 0, ""
    while i < len(src):
        ch = src[i]
        nxt = src[i + 1] if i + 1 < len(src) else ""
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch == "/" and nxt == "/":
            i = src.find("\n", i)
            if i == -1:
                break
            continue
        elif ch == "/" and nxt == "*":
            end = src.find("*/", i + 2)
            if end == -1:
                break
            i = end + 2
            continue
        elif ch in "\"'":
            quote = ch
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
        i += 1
    raise AssertionError(f"unbalanced {opener!r} from offset {start}")


def _function(name: str) -> str:
    """The source of `function <name>(...) { ... }`, braces balanced."""
    head = f"function {name}("
    assert head in LOCK_SCRIPT, f"{head!r} is gone from the lock screen script"
    return _balanced(LOCK_SCRIPT, LOCK_SCRIPT.index(head), "{", "}")


def _unlock_wiring() -> str:
    """The source of the `swipe(document.body, openPasscode, ...)` call."""
    head = "swipe(document.body, openPasscode,"
    assert head in LOCK_SCRIPT, "the unlock swipe is no longer bound to the body"
    start = LOCK_SCRIPT.index(head)
    return _balanced(LOCK_SCRIPT, start, "(", ")") + ";"


#: A DOM only as real as these gestures need, plus the scenario driver.
#:
#: Touch events are fabricated and handed straight to the listeners the source
#: registered. That is the point of the exercise: the assertions are about what
#: the registered handlers DO, not about what the script says.
_HARNESS = r"""
function makeEl(opts) {
  opts = opts || {};
  return {
    _sel: opts.sel || [],
    _parent: opts.parent || null,
    _attrs: opts.attrs || {},
    _h: {},
    scrollHeight: opts.scrollHeight || 0,
    clientHeight: opts.clientHeight || 0,
    scrollTop: 0,
    addEventListener: function (t, fn) { (this._h[t] = this._h[t] || []).push(fn); },
    setAttribute: function (k, v) { this._attrs[k] = v; },
    getAttribute: function (k) {
      return Object.prototype.hasOwnProperty.call(this._attrs, k) ? this._attrs[k] : null;
    },
    closest: function (sel) {
      var n = this;
      while (n) { if (n._sel.indexOf(sel) !== -1) return n; n = n._parent; }
      return null;
    },
    fire: function (t, ev) { (this._h[t] || []).slice().forEach(function (fn) { fn(ev); }); }
  };
}

var SCN = JSON.parse(process.env.LS_SCENARIO);

var body   = makeEl({ sel: ["body"] });
var screenEl = makeEl({ attrs: { "data-sheet": SCN.sheet } });
var feedEl = makeEl({
  sel: [".ls-feed"], parent: body,
  scrollHeight: SCN.feedScrollHeight, clientHeight: SCN.feedClientHeight
});
// WHERE the feed is scrolled, which is the whole of tsk-36i6ed. A real browser
// clamps this to [0, scrollHeight - clientHeight]; scenarios stay inside that.
feedEl.scrollTop = SCN.feedScrollTop;
// A notification card inside the feed -- what the finger actually lands on.
var card = makeEl({ sel: [".ls-note"], parent: feedEl });
var document = { body: body };

var unlocked = 0;
function openPasscode() { unlocked += 1; }

__GESTURE_SOURCE__

var targets = { body: body, card: card, feed: feedEl };
var startT = targets[SCN.startOn], endT = targets[SCN.endOn];

body.fire("touchstart", { target: startT, touches: [{ clientX: SCN.x0, clientY: SCN.y0 }] });
// One intermediate move, so `moved` latches the way a real drag sets it.
body.fire("touchmove", {
  target: startT,
  touches: [{ clientX: (SCN.x0 + SCN.x1) / 2, clientY: (SCN.y0 + SCN.y1) / 2 }]
});
// The feed may move UNDER the finger -- that is what a read-to-the-end drag
// does. Whatever the veto decided at touchstart has to stand.
if (SCN.scrollDuringDrag !== null) feedEl.scrollTop = SCN.scrollDuringDrag;
body.fire("touchend", { target: endT, changedTouches: [{ clientX: SCN.x1, clientY: SCN.y1 }] });

process.stdout.write(JSON.stringify({ unlocked: unlocked }));
"""


def _gesture_source(
    *, with_fix: bool = True, ignore_scroll: bool = False, ignore_dead_feed: bool = False
) -> str:
    """The real source of the gesture machinery, optionally de-fixed.

    `with_fix=False` rebuilds the wiring as it stood before tsk-6bjsvg -- the
    unlock swipe bound to the body with no origin veto -- so a test can prove
    the harness is able to observe the bug at all.

    `ignore_scroll=True` is the tsk-36i6ed mutation: it makes the veto ask
    whether the feed overflows and NOT where it is scrolled, which is precisely
    what the merged #3102 code did. It is the plausible wrong answer, so a suite
    that cannot fail under it has not separated the two cases at all.
    """
    room = _function("feedScrollRoom")
    if ignore_scroll:
        mutated = room.replace(" - feedEl.scrollTop", "")
        assert mutated != room, (
            "the mutation changed nothing -- feedScrollRoom() no longer reads "
            "scrollTop the way this mutation assumes, so it proves nothing"
        )
        room = mutated
    wiring = _unlock_wiring()
    if ignore_dead_feed:
        # The plausible wrong answer for Jay's glass bug: keep asking how much
        # room is left and drop the question of whether the feed can scroll at
        # all. That is the code as it stood when he reported it.
        mutated = wiring.replace(" || !feedOverflows()", "")
        assert mutated != wiring, (
            "the mutation changed nothing -- the veto no longer asks "
            "!feedOverflows() the way this mutation assumes, so it proves nothing"
        )
        wiring = mutated
    if not with_fix:
        # Drop the 5th argument (the veto) and nothing else.
        guard_end = wiring.rindex("}, function (ev) {")
        wiring = wiring[: guard_end + 1] + ");"
        assert "closest" not in wiring, "de-fixed wiring still carries the veto"
    return "\n".join([_function("feedOverflows"), room, _function("swipe"), wiring])


def _drive(
    scenario: dict,
    *,
    with_fix: bool = True,
    ignore_scroll: bool = False,
    ignore_dead_feed: bool = False,
) -> int:
    """Run one gesture and return how many times unlock was triggered."""
    node = shutil.which("node")
    if node is None:  # pragma: no cover - depends on the runner image
        # FAIL, do not skip. These tests execute the gesture source; without node
        # they measure nothing, and a skip reads as satisfied to both the shard
        # summary and GitHub's required-check logic. A suite that can quietly
        # evaporate is indistinguishable from one that ran and found no defect.
        # CI provisions node in the shards job -- if it is missing, that is the bug.
        pytest.fail(
            "node is required to execute the lock screen gesture source, and was "
            "not found on PATH. These tests cannot be skipped: skipping them would "
            "report green while proving nothing about the unlock gesture."
        )
    script = _HARNESS.replace(
        "__GESTURE_SOURCE__",
        _gesture_source(
            with_fix=with_fix,
            ignore_scroll=ignore_scroll,
            ignore_dead_feed=ignore_dead_feed,
        ),
    )
    done = subprocess.run(
        [node, "-e", script],
        env={**os.environ, "LS_SCENARIO": json.dumps(scenario)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert done.returncode == 0, f"node failed:\n{done.stderr}"
    return json.loads(done.stdout)["unlocked"]


def _scenario(**over) -> dict:
    """A 200px upward drag on the resting screen, over an overflowing feed.

    The feed is 900 tall in a 300 viewport, so its scrollTop runs 0..600 and
    600 is its end.
    """
    base = {
        "sheet": "none",
        "feedScrollHeight": 900,
        "feedClientHeight": 300,
        "feedScrollTop": 0,
        "scrollDuringDrag": None,
        "startOn": "card",
        "endOn": "card",
        "x0": 160,
        "y0": 620,
        "x1": 160,
        "y1": 420,
    }
    base.update(over)
    return base


class TestUnlockSwipeOrigin:
    """tsk-6bjsvg: reading the feed must not unlock the phone."""

    def test_drag_from_the_feed_does_not_open_the_keypad(self):
        """THE RED CASE. A long upward drag begun in a scrollable feed.

        This is Jay's bug verbatim: one long drag to reach the end of the
        notifications, which is where it bites, because that is where short
        flicks give way to a single sustained pull.
        """
        assert _drive(_scenario()) == 0

    def test_drag_that_leaves_the_feed_is_judged_by_where_it_began(self):
        """The subtle one: the finger ENDS outside the feed.

        A 200px drag is most of this screen, so the finger routinely leaves the
        feed before lifting. An implementation that asks `touchend.target`
        instead of latching at touchstart passes the test above and still ships
        the bug -- so the two scenarios differ only in where the touch ended.
        """
        assert _drive(_scenario(endOn="body")) == 0

    def test_drag_anywhere_else_still_unlocks(self):
        """The positive case, without which a veto could pass by never unlocking."""
        assert _drive(_scenario(startOn="body", endOn="body")) == 1

    def test_a_feed_with_nothing_to_scroll_does_not_unlock_from_a_card(self):
        """REVERSED DELIBERATELY. This test used to assert the opposite.

        The old rule said a feed that cannot move is not being read, so a drag
        starting on it should unlock. That was reasoned from a device with one
        agent and no notifications. MEASURED on the real device instead --
        540x1200, sway scale 2.0, the six agents the demo phone actually shows
        -- `#ls-feed` reports scrollHeight 394 and clientHeight 394. The feed is
        content-sized: it does not overflow, and it never did. So this was not a
        corner case for a brand-new phone, it was the ORDINARY state of the
        screen, and every drag that began on an agent island opened the keypad.
        That is the bug Jay reported from the glass.

        The unlock gesture is not lost: the feed is 394px of a 1200px screen and
        the rest of the glass still unlocks, which is what
        `test_a_non_scrolling_feed_does_not_kill_the_gesture_elsewhere` holds to.
        """
        assert _drive(_scenario(feedScrollHeight=300)) == 0

    def test_a_non_scrolling_feed_does_not_kill_the_gesture_elsewhere(self):
        """The cost of the reversal above, bounded.

        Vetoing a non-scrolling feed is only acceptable while the unlock swipe
        still works everywhere else. Without this, the fix for Jay's bug could
        be "nothing unlocks any more" and the suite would not notice.
        """
        assert _drive(_scenario(feedScrollHeight=300, startOn="body", endOn="body")) == 1

    def test_the_veto_does_not_reach_past_the_resting_screen(self):
        """With a sheet open the unlock swipe was already disarmed; keep it so."""
        assert _drive(_scenario(sheet="chat", startOn="body", endOn="body")) == 0

    def test_harness_observes_the_defect(self):
        """THE CONTROL. Strip the fix and the red scenario must unlock.

        Without this, every assertion above could be passing because the DOM
        stub quietly does nothing -- "no unlock" and "no gesture ran at all"
        read identically. This is the one assertion that fails if the harness
        stops measuring.
        """
        assert _drive(_scenario(), with_fix=False) == 1


class TestUnlockAtTheEndOfTheFeed:
    """tsk-36i6ed: the hole #3102 left behind.

    #3102 asked whether the feed OVERFLOWS. It never asked where the feed was
    SCROLLED -- and swipe-up is both the unlock gesture and the gesture that
    scrolls the feed toward its end. At the bottom of a long feed an upward drag
    cannot scroll (nothing left to reach) and was vetoed anyway, so nothing
    happened at all, over most of the glass, in the plainest flow there is: read
    to the end of your notifications, then swipe up to unlock.

    Cases 1 and 2 below are the SAME overflowing feed and the SAME drag. They
    differ in one number, `feedScrollTop`, and nothing else -- so an
    implementation that ignores scroll position cannot satisfy both.
    """

    def test_1_mid_feed_still_does_not_unlock(self):
        """CASE 1. Scrolled to the top of a 600px range: still being read."""
        assert _drive(_scenario(feedScrollTop=0)) == 0

    def test_2_at_the_end_of_the_feed_unlocks(self):
        """CASE 2. Same feed, same drag, scrolled to its end. Jay's bug."""
        assert _drive(_scenario(feedScrollTop=600)) == 1

    def test_one_screenful_from_the_end_still_does_not_unlock(self):
        """Room left is room left. 300 of 600 is mid-feed by any reading."""
        assert _drive(_scenario(feedScrollTop=300)) == 0

    def test_a_fractional_pixel_short_of_the_end_counts_as_the_end(self):
        """The DPR case, and the reason this is a tolerance and not `==`.

        On a 2.75x panel these three numbers are fractional and their difference
        lands near zero without reaching it. A fix written as an equality passes
        every whole-pixel scenario above and still strands the user on the glass.
        """
        assert _drive(_scenario(feedScrollTop=599.6)) == 1

    def test_a_feed_with_nothing_to_scroll_is_not_treated_as_at_its_end(self):
        """The distinction the room measurement ALONE cannot draw.

        A feed scrolled to its end and a feed that never scrolled both report
        zero room, and they are opposite situations. At the end of a long feed
        the drag that got there is finished and an upward swipe means unlock
        (tsk-36i6ed). On a feed that cannot scroll, an upward drag on a card is
        not the end of anything. Telling them apart needs a second reading --
        whether the feed overflows at all -- which is why `feedOverflows()` is
        back in the veto.

        It is back as a DISJUNCT, not the conjunct removed with tsk-36i6ed. That
        one could not change the answer, because the browser clamps scrollTop to
        0 on a feed that cannot scroll; this one decides this case by itself and
        decides no other. `test_the_suite_fails_the_mutation_that_ignores_a_
        dead_feed` is what holds it to that.
        """
        assert _drive(_scenario(feedScrollHeight=300, feedScrollTop=0)) == 0

    def test_the_end_of_the_feed_is_judged_from_where_the_touch_LANDED(self):
        """The latch, measured rather than asserted on structure.

        Reaching the end of the feed is the last part of the very drag that
        gets there, so scroll position at touchEND is the plausible wrong
        answer: it would unlock the phone at the end of the read itself, which
        is the #3102 bug wearing a different hat. The harness scrolls the feed
        to its end DURING the drag; the gesture must still be judged by the 0
        it started at.
        """
        assert _drive(_scenario(feedScrollTop=0, scrollDuringDrag=600)) == 0

    def test_the_suite_fails_the_mutation_that_ignores_scroll_position(self):
        """THE MUTATION CONTROL, and the acceptance criterion @taOS-dev set.

        Strip `- feedEl.scrollTop` out of the measurement and the veto is back
        to asking only about overflow -- merged #3102 exactly. Case 2 must go
        RED under it. If it stays green, these scenarios do not separate scroll
        position from overflow and the suite is wrong even when it is all green.
        """
        assert _drive(_scenario(feedScrollTop=600), ignore_scroll=True) == 0
        # ...while case 1, which the mutation gets right by accident, is unmoved.
        # Naming this keeps the control honest: the mutation must break the case
        # that discriminates, not simply break everything.
        assert _drive(_scenario(feedScrollTop=0), ignore_scroll=True) == 0

    def test_the_suite_fails_the_mutation_that_ignores_a_dead_feed(self):
        """THE MUTATION CONTROL for Jay's glass bug.

        Drop `|| !feedOverflows()` and the veto is back to asking only how much
        room is left -- the code exactly as it was when Jay reported that
        swiping an agent island opened the keypad. The non-scrolling case must
        go RED under it.

        The two assertions after it are the point. A mutation that breaks
        everything proves nothing: it would show only that the suite notices
        change, not that these scenarios separate "cannot scroll" from "scrolled
        to the end". Both of those stay exactly as they are under the mutation,
        so the one case that moves is the one that discriminates.
        """
        assert _drive(_scenario(feedScrollHeight=300), ignore_dead_feed=True) == 1
        assert _drive(_scenario(feedScrollTop=600), ignore_dead_feed=True) == 1
        assert _drive(_scenario(feedScrollTop=0), ignore_dead_feed=True) == 0


class TestGestureLatching:
    """Properties of `swipe()` itself that the scenarios above rely on."""

    def test_the_veto_is_latched_at_touchstart(self):
        """Asserted on structure because it is a claim about WHEN, not what.

        `test_drag_that_leaves_the_feed_...` is the behavioural proof; this
        names the mechanism so a future reader does not "simplify" the latch
        into a touchend lookup and find only a scenario failing for a reason
        the code does not explain.
        """
        latch = "vetoed = !!(veto && veto(ev))"
        assert latch in LOCK_SCRIPT, "the veto is no longer latched from the event"
        touchstart = LOCK_SCRIPT.index('surface.addEventListener("touchstart"')
        touchend = LOCK_SCRIPT.index('surface.addEventListener("touchend"', touchstart)
        assert touchstart < LOCK_SCRIPT.index(latch) < touchend

    def test_overflow_is_measured_in_exactly_one_place(self):
        """Unchanged in intent. `feedOverflows()` is still the only definition of
        "this feed is taller than its viewport".

        What changed is that it is no longer the veto's question. tsk-36i6ed
        needed a SECOND one -- how far the feed can still travel -- and asking
        both would have left an arm that cannot fail: a feed that does not
        overflow has `scrollTop` clamped to 0 by the browser, so it always reads
        as already at its end, and the overflow conjunct could never change the
        answer. The fade still asks it, and it stays defined once.
        """
        assert LOCK_SCRIPT.count("function feedOverflows(") == 1
        # Callers, not occurrences: the definition line contains the call text.
        assert LOCK_SCRIPT.count("feedOverflows()") - 1 >= 1

    def test_the_feed_is_measured_only_inside_its_two_helpers(self):
        """The fade and the veto must not drift apart about the same feed.

        There are two questions, and tsk-36i6ed happened because only the first
        was being asked: `feedOverflows()` -- is it taller than its viewport --
        and `feedScrollRoom()` -- how far can it still travel. Each is defined
        once. This asserts nothing ELSE reads the feed's geometry, because a
        third, inline copy with its own tolerance is how they drift.
        """
        helpers = _function("feedOverflows") + _function("feedScrollRoom")
        assert helpers.count("feedEl.scrollHeight") == 2
        assert helpers.count("feedEl.clientHeight") == 2
        elsewhere = LOCK_SCRIPT.replace(helpers[: len(_function("feedOverflows"))], "")
        elsewhere = elsewhere.replace(_function("feedScrollRoom"), "")
        assert "feedEl.scrollHeight" not in elsewhere
        assert "feedEl.clientHeight" not in elsewhere
        # scrollTop is READ outside them exactly once, for the top edge's fade,
        # which is the one edge neither helper answers. Writes are not
        # measurements and stay allowed: the view switcher resets the offset
        # when it swaps panels, and counting that as a rogue measurement would
        # make this check fail for doing the right thing.
        reads = re.findall(r"feedEl\.scrollTop(?!\s*=(?!=))", elsewhere)
        assert len(reads) == 1, f"feedEl.scrollTop is read {len(reads)} times outside the helpers"
        assert "var atTop" in elsewhere
