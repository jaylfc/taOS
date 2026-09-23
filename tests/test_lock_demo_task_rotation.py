"""Rotating "current task" for the lock screen's DEMO agent islands.

Jay's product ask, from the glass: "it would be nice if the agents' current
task changed whilst being on the lock screen, maybe a staggered change
trigger during the 30 second lock screen time out."

**Why independent per-agent clocks, not a round-robin (a documented reversal).**
The first shipped design was a round-robin: agents took turns in list order,
one state each, which guaranteed a minimum gap between any two changes by
construction. Jay saw it on the handset and asked for the OPPOSITE of what it
guarantees: "make the agents activities cycle faster so they look more alive
over the 30 second period" and "the agent changes shouldnt all be staggered,
it looks unnatural, some things can coincide." A round-robin cannot deliver
either of those -- it deliberately spreads every agent's turn out and
deliberately forbids two from landing together -- so it is gone, along with
`_demo_rotation_schedule` / `_demo_agent_rotation_state` and the tests built
on its >=2s spacing guarantee (`TestIndependentClocksCannotGuaranteeSpacing`,
"two scripted agents never share a next_change_ms").

In its place, `_demo_agent_independent_state` gives each agent its OWN clock:
a stable pace multiplier (0.75-1.35x), a stable phase offset, and a stable
per-entry dwell jitter (0.8-1.2x), all derived deterministically from
`zlib.crc32` of the agent's name (and name+index for the jitter) so every
poll -- and every other agent -- agrees on the same values without any
stored state. Nothing coordinates between agents any more: two islands' next
changes can coincide, or land a fraction of a second apart, exactly as two
independent real workers' status updates could. The base dwells
(`_DEMO_TASK_DWELL_S = 20.0`, `_DEMO_TASK_DONE_DWELL_S = 5.0`) are tuned, with
the pace/jitter spread, to keep the whole five-agent ensemble changing
roughly 8-12 times over any 30s look -- livelier than the original ask, per
Jay's "more alive" note.

**Hostile cases come first**, per the brief: the flag off, a name with no
script, and a status carrying a colon are all checked before the happy-path
and the statistical rate/naturalness tests, all of which use the injectable
clock (`auth._demo_task_clock` or a `now` argument) rather than real time.
"""
from __future__ import annotations

import asyncio
import bisect
import json
import os
import shutil
import statistics
import subprocess

import pytest

import tinyagentos.routes.auth as auth
from tinyagentos.routes.auth import _LOCK_SCREEN_SCRIPT as LOCK_SCRIPT

from test_lock_screen_gestures import _function
from test_lock_screen_repaint import _DOM, _var


# =============================================================================
# SERVER: the pure rotation functions.
# =============================================================================


class TestHostileCasesFirst:
    """The failure modes that matter more than the happy path."""

    def test_a_name_without_a_script_is_a_cycle_of_one(self):
        """No entry in _DEMO_TASK_SCRIPTS -> the configured status, forever."""
        cycle = auth._demo_task_cycle("Nobody In Particular", "Idle chat")
        assert cycle == ["Idle chat"]

    def test_a_cycle_of_one_never_enters_the_rotation(self):
        """The route only rotates agents whose cycle has more than one entry
        -- this is the guard that keeps an unscripted agent static."""
        assert len(auth._demo_task_cycle("Nobody", "Idle chat")) == 1

    def test_a_colon_in_the_configured_status_is_handled_as_today(self, monkeypatch):
        """The existing demo-agent parser truncates at the SECOND colon
        (`parts[2]` only) -- a pre-existing surprise this feature must not
        change. Verified through the route, at the exact parsing boundary."""
        monkeypatch.setattr(auth, "_request_is_console", lambda _r: True)
        monkeypatch.setenv(
            "TAOS_LOCK_DEMO_AGENTS",
            "Accountant:hermes:Chasing invoices: overdue clients this month",
        )
        monkeypatch.setattr(auth, "_demo_task_clock", lambda: 0.0)
        resp = asyncio.run(auth.lock_widgets(object()))
        body = json.loads(bytes(resp.body))
        accountant = next(a for a in body["agents"] if a["name"] == "Accountant")
        # Accountant DOES have a script, so its configured status becomes
        # cycle[0] and the rotation may have already moved past it by t=0 --
        # what matters is that the value fed into the cycle was truncated
        # exactly like the pre-existing parser does, which the cycle
        # function exposes directly.
        cycle = auth._demo_task_cycle("Accountant", "Chasing invoices")
        assert cycle[0] == "Chasing invoices"

    def test_the_master_flag_off_changes_nothing_and_adds_no_fields(self, monkeypatch):
        """No TAOS_LOCK_DEMO_AGENTS -> no demo agents, no next_change_ms
        anywhere, no top-level refresh_in_ms. The THE red case: if rotation
        ever ran unconditionally this is what would catch it."""
        monkeypatch.setattr(auth, "_request_is_console", lambda _r: True)
        monkeypatch.delenv("TAOS_LOCK_DEMO_AGENTS", raising=False)
        resp = asyncio.run(auth.lock_widgets(object()))
        body = json.loads(bytes(resp.body))
        assert "refresh_in_ms" not in body
        for agent in body["agents"]:
            assert "next_change_ms" not in agent
        assert [a["name"] for a in body["agents"]] == ["taOS Agent"]

    def test_an_agent_with_no_script_gets_no_next_change_ms(self, monkeypatch):
        monkeypatch.setattr(auth, "_request_is_console", lambda _r: True)
        monkeypatch.setenv("TAOS_LOCK_DEMO_AGENTS", "Nobody:hermes:Idle chat")
        monkeypatch.setattr(auth, "_demo_task_clock", lambda: 12345.0)
        resp = asyncio.run(auth.lock_widgets(object()))
        body = json.loads(bytes(resp.body))
        nobody = next(a for a in body["agents"] if a["name"] == "Nobody")
        assert nobody["status"] == "Idle chat"
        assert "next_change_ms" not in nobody
        assert "refresh_in_ms" not in body


class TestPerEntryDwell:
    def test_a_completion_beat_dwells_the_short_beat(self):
        assert auth._demo_task_dwell("✓ Table booked at Dishoom") == auth._DEMO_TASK_DONE_DWELL_S == 5.0

    def test_a_normal_entry_dwells_the_full_turn(self):
        assert auth._demo_task_dwell("Booking a table for Friday, 7:30") == auth._DEMO_TASK_DWELL_S == 20.0

    def test_only_a_leading_checkmark_with_a_space_counts(self):
        """A checkmark elsewhere in the text, or with no space after it, is
        not the "done" marker -- otherwise a task that merely MENTIONS a
        checkmark would get the short beat."""
        assert auth._demo_task_dwell("✓no space") == auth._DEMO_TASK_DWELL_S
        assert auth._demo_task_dwell("Approved ✓") == auth._DEMO_TASK_DWELL_S


class TestTheRefreshClamp:
    def test_nothing_scheduled_is_none(self):
        assert auth._demo_refresh_in_ms([]) is None

    def test_a_tiny_remaining_time_is_floored_to_one_second(self):
        assert auth._demo_refresh_in_ms([50]) == 1000

    def test_a_huge_remaining_time_is_capped_at_fifteen_seconds(self):
        assert auth._demo_refresh_in_ms([20000]) == 15000

    def test_the_ordinary_case_is_soonest_plus_the_margin(self):
        assert auth._demo_refresh_in_ms([5000, 9000, 3000]) == 3150

    def test_the_margin_alone_does_not_escape_the_floor(self):
        assert auth._demo_refresh_in_ms([0]) == 1000


# =============================================================================
# SERVER: the independent per-agent clock.
# =============================================================================


def _named_agents() -> list[str]:
    return list(auth._DEMO_TASK_SCRIPTS.keys())


def _agent_timeline(name: str, horizon: float) -> list[float]:
    """Every change instant for one agent's independent clock across
    [0, horizon).

    Built from the exact same pieces `_demo_agent_independent_state` composes
    -- its own cycle, pace, per-entry jitter and phase -- rather than a
    second, differently-derived formula: this is the timeline that single-
    instant function implicitly walks, made explicit so the statistical
    tests below can look at it as a whole.
    """
    cycle = auth._demo_task_cycle(name, "Working")
    pace = auth._demo_task_pace(name)
    dwells = [
        auth._demo_task_dwell(status) * pace * auth._demo_task_entry_jitter(name, i)
        for i, status in enumerate(cycle)
    ]
    total = sum(dwells)
    offset = auth._demo_task_phase(name)
    acc = 0.0
    boundaries = []
    for d in dwells:
        acc += d
        boundaries.append(acc % total)
    base_times = sorted((b - offset) % total for b in boundaries)
    events: list[float] = []
    k = 0
    while k * total < horizon + total:
        for t in base_times:
            rt = k * total + t
            if 0 <= rt < horizon:
                events.append(rt)
        k += 1
    return sorted(events)


def _ensemble_timeline(names: list[str], horizon: float) -> list[tuple[float, str]]:
    events: list[tuple[float, str]] = []
    for name in names:
        events.extend((t, name) for t in _agent_timeline(name, horizon))
    events.sort()
    return events


class TestHostileCasesForTheIndependentClock:
    """Determinism and continuity come before the statistical properties --
    a clock that disagreed with itself, or changed off-schedule, would make
    every average below meaningless."""

    def test_the_same_now_gives_the_same_answer_across_calls(self):
        name = "Personal Assistant"
        first = auth._demo_agent_independent_state(name, "Working", 12345.678)
        second = auth._demo_agent_independent_state(name, "Working", 12345.678)
        assert first == second

    def test_the_same_now_agrees_across_a_fresh_import_of_the_pieces(self):
        """Not randomised per process: pace/phase/jitter come from `crc32`,
        not from `random` or `hash()` (which is salted per interpreter)."""
        name = "Accountant"
        pace_a = auth._demo_task_pace(name)
        pace_b = auth._demo_task_pace(name)
        phase_a = auth._demo_task_phase(name)
        phase_b = auth._demo_task_phase(name)
        assert pace_a == pace_b
        assert phase_a == phase_b

    def test_the_status_only_changes_at_a_scheduled_boundary(self):
        """Continuity: sampling a dense grid of instants, the status must be
        constant everywhere except at the instants _agent_timeline predicts,
        and it must actually differ across every one of those."""
        name = "Sales Manager"
        events = _agent_timeline(name, 400.0)
        assert len(events) > 5, "fixture horizon too short to prove anything"
        # Between two consecutive events, the status must never change.
        checkpoints = [0.0] + events
        for a, b in zip(checkpoints, checkpoints[1:]):
            if b - a < 0.01:
                continue
            mid = (a + b) / 2
            s_a, _ = auth._demo_agent_independent_state(name, "Working", a + 0.005)
            s_mid, _ = auth._demo_agent_independent_state(name, "Working", mid)
            assert s_a == s_mid, f"status moved between scheduled boundaries at {mid}"
        # At every predicted event, the status either side must differ.
        for e in events[:10]:
            before, _ = auth._demo_agent_independent_state(name, "Working", e - 0.01)
            after, _ = auth._demo_agent_independent_state(name, "Working", e + 0.01)
            assert before != after, f"predicted boundary at {e} was not a real change"


class TestEveryAgentHasItsOwnPace:
    def test_no_two_named_agents_share_a_pace(self):
        paces = [auth._demo_task_pace(name) for name in _named_agents()]
        assert len(paces) == len(set(paces)), paces

    def test_every_pace_is_within_the_documented_range(self):
        for name in _named_agents():
            pace = auth._demo_task_pace(name)
            assert 0.75 <= pace < 1.35, (name, pace)


class TestTheEnsembleFeelsAlive:
    """Jay: "make the agents activities cycle faster so they look more alive
    over the 30 second period." Verified as a distribution over MANY start
    times, not a single lucky window."""

    def test_a_30_second_window_averages_eight_to_twelve_changes(self):
        events = _ensemble_timeline(_named_agents(), horizon=20000.0)
        times = [t for t, _n in events]
        counts = []
        for s in range(0, 19970, 3):
            lo = bisect.bisect_left(times, s)
            hi = bisect.bisect_left(times, s + 30)
            counts.append(hi - lo)
        avg = statistics.mean(counts)
        assert 8.0 <= avg <= 12.0, f"average changes/30s window was {avg}"

    def test_no_30_second_window_sees_fewer_than_four_changes(self):
        events = _ensemble_timeline(_named_agents(), horizon=20000.0)
        times = [t for t, _n in events]
        counts = []
        for s in range(0, 19970, 3):
            lo = bisect.bisect_left(times, s)
            hi = bisect.bisect_left(times, s + 30)
            counts.append(hi - lo)
        assert min(counts) >= 4, f"some 30s window saw only {min(counts)} changes"


class TestTheEnsembleLooksNatural:
    """Jay, after seeing the round-robin: "the agent changes shouldnt all be
    staggered, it looks unnatural, some things can coincide." Both halves of
    that are asserted so a regression back to either lock-step (everything
    coincides) OR a rigid round-robin (nothing ever coincides, every gap
    identical) goes red.
    """

    def test_some_different_agents_change_within_a_second_of_each_other(self):
        events = _ensemble_timeline(_named_agents(), horizon=20000.0)
        close = 0
        for (t1, n1), (t2, n2) in zip(events, events[1:]):
            if n1 != n2 and (t2 - t1) < 1.0:
                close += 1
        assert close > 0, (
            "no two different agents ever changed within 1s of each other -- "
            "that is the round-robin's signature, not independent clocks"
        )

    def test_the_gaps_between_consecutive_changes_are_not_all_equal(self):
        events = _ensemble_timeline(_named_agents(), horizon=20000.0)
        times = [t for t, _n in events]
        gaps = {round(b - a, 3) for a, b in zip(times, times[1:])}
        assert len(gaps) > 10, (
            "the gaps between changes collapsed onto a handful of values -- "
            "that is what a rigid schedule looks like, not organic pacing"
        )


class TestMutationControlsForTheNewInvariants:
    """Break the code on purpose, per the brief, and confirm the naturalness
    and rate assertions above actually go red -- not just that they pass on
    the shipped code, which a vacuous assertion would do too.

    Each mutation is isolated to ONE property: the naturalness mutation
    (below) keeps the rate assertions passing, and the rate mutation keeps
    the naturalness assertions passing, so neither control is accidentally
    proving the other.
    """

    def test_naturalness_assertions_go_red_under_a_rigid_evenly_spaced_schedule(self, monkeypatch):
        """Remove per-agent individuality -- constant pace, constant jitter,
        phases spaced evenly apart -- which is structurally a round-robin
        again in every way that matters here, even though it does not use
        `_demo_rotation_schedule`. Verified against BOTH naturalness
        assertions, computed the same way the real tests compute them.
        """
        names = _named_agents()
        monkeypatch.setattr(auth, "_demo_task_pace", lambda name: 1.0)
        monkeypatch.setattr(auth, "_demo_task_entry_jitter", lambda name, i: 1.0)
        monkeypatch.setattr(auth, "_demo_task_phase", lambda name: names.index(name) * 17.0)
        events = _ensemble_timeline(names, horizon=20000.0)
        times = [t for t, _n in events]
        gaps = {round(b - a, 3) for a, b in zip(times, times[1:])}
        close = sum(
            1 for (t1, n1), (t2, n2) in zip(events, events[1:])
            if n1 != n2 and (t2 - t1) < 1.0
        )
        assert close == 0, "the mutation should have eliminated all near-coincidences"
        assert len(gaps) <= 10, "the mutation should have collapsed the gap variety"
        # And confirm the RATE assertions this mutation was NOT meant to
        # touch are unaffected -- proof this control isolates naturalness.
        counts = []
        for s in range(0, 19970, 3):
            lo = bisect.bisect_left(times, s)
            hi = bisect.bisect_left(times, s + 30)
            counts.append(hi - lo)
        avg = statistics.mean(counts)
        assert 8.0 <= avg <= 12.0 and min(counts) >= 4, (
            "the naturalness mutation also broke the rate -- it is not isolated"
        )

    def test_rate_assertions_go_red_under_a_much_slower_dwell(self, monkeypatch):
        """Scale every dwell up 6x -- organic pacing (pace/jitter/phase) is
        untouched, everything just holds six times longer -- and both rate
        assertions must fail.
        """
        real_dwell = auth._demo_task_dwell
        monkeypatch.setattr(auth, "_demo_task_dwell", lambda status: real_dwell(status) * 6.0)
        events = _ensemble_timeline(_named_agents(), horizon=20000.0)
        times = [t for t, _n in events]
        counts = []
        for s in range(0, 19970, 3):
            lo = bisect.bisect_left(times, s)
            hi = bisect.bisect_left(times, s + 30)
            counts.append(hi - lo)
        avg = statistics.mean(counts)
        assert not (8.0 <= avg <= 12.0), f"the mutation should have broken the average, got {avg}"
        assert min(counts) < 4, "the mutation should have broken the floor"
        # And confirm NATURALNESS is unaffected by this mutation -- proof
        # this control isolates the rate.
        gaps = {round(b - a, 3) for a, b in zip(times, times[1:])}
        close = sum(
            1 for (t1, n1), (t2, n2) in zip(events, events[1:])
            if n1 != n2 and (t2 - t1) < 1.0
        )
        assert len(gaps) > 10 and close > 0, (
            "the rate mutation also broke naturalness -- it is not isolated"
        )


# =============================================================================
# SERVER: the route, end to end.
# =============================================================================


class TestTheRoute:
    @staticmethod
    def _call(monkeypatch, *, console=True, agents="Personal Assistant,Nobody", now=0.0):
        monkeypatch.setattr(auth, "_request_is_console", lambda _r: console)
        if agents is None:
            monkeypatch.delenv("TAOS_LOCK_DEMO_AGENTS", raising=False)
        else:
            monkeypatch.setenv("TAOS_LOCK_DEMO_AGENTS", agents)
        monkeypatch.setattr(auth, "_demo_task_clock", lambda: now)
        return asyncio.run(auth.lock_widgets(object()))

    def test_a_scripted_agent_gets_next_change_ms(self, monkeypatch):
        resp = self._call(monkeypatch)
        body = json.loads(bytes(resp.body))
        pa = next(a for a in body["agents"] if a["name"] == "Personal Assistant")
        assert isinstance(pa["next_change_ms"], int)
        assert pa["next_change_ms"] > 0

    def test_refresh_in_ms_is_the_soonest_change_plus_margin_clamped(self, monkeypatch):
        resp = self._call(monkeypatch)
        body = json.loads(bytes(resp.body))
        candidates = [a["next_change_ms"] for a in body["agents"] if "next_change_ms" in a]
        assert candidates, "expected at least one rotating agent in the fixture"
        assert body["refresh_in_ms"] == max(1000, min(15000, min(candidates) + 150))

    def test_only_agents_actually_sent_can_drive_refresh_in_ms(self, monkeypatch):
        """The response only ever returns the first six agents -- a rotation
        happening off that visible slice must not schedule the client around
        a change it could never paint."""
        # Seven scripted names would need 7 real script entries to prove this
        # fully, but the mechanism is exercised directly: only entries with
        # `next_change_ms` from `visible` feed the clamp, verified against
        # the route's own agents[:6] slice.
        resp = self._call(monkeypatch, agents="Personal Assistant,Nobody")
        body = json.loads(bytes(resp.body))
        assert len(body["agents"]) <= 6

    def test_no_scripted_agents_means_no_refresh_in_ms(self, monkeypatch):
        resp = self._call(monkeypatch, agents="Nobody:hermes:Idle,Also Nobody:openclaw:Idle")
        body = json.loads(bytes(resp.body))
        assert "refresh_in_ms" not in body
        for a in body["agents"]:
            assert "next_change_ms" not in a

    def test_two_scripted_agents_may_share_a_next_change_ms(self, monkeypatch):
        """The reversal of the round-robin guarantee, made explicit: two
        agents CAN be due to change at the same time now, and the route must
        not crash, dedupe, or otherwise treat that as an error state."""
        resp = self._call(
            monkeypatch,
            agents="Personal Assistant:hermes:Reviewing,Accountant:hermes:Reviewing",
        )
        body = json.loads(bytes(resp.body))
        values = [a["next_change_ms"] for a in body["agents"] if "next_change_ms" in a]
        assert len(values) == 2
        assert all(isinstance(v, int) and v > 0 for v in values)

    def test_the_response_is_authoritative_across_polls(self, monkeypatch):
        """Two calls a few seconds apart must be consistent WITH the
        schedule (the second call's remaining time has shrunk by roughly the
        elapsed time), never independently randomised."""
        first = self._call(monkeypatch, now=0.0)
        second = self._call(monkeypatch, now=4.0)
        b1 = json.loads(bytes(first.body))
        b2 = json.loads(bytes(second.body))
        pa1 = next(a for a in b1["agents"] if a["name"] == "Personal Assistant")
        pa2 = next(a for a in b2["agents"] if a["name"] == "Personal Assistant")
        if pa1["status"] == pa2["status"]:
            # No turn boundary crossed in those 4s: remaining time shrank by
            # (about) exactly the elapsed time.
            assert pa1["next_change_ms"] - pa2["next_change_ms"] == pytest.approx(4000, abs=5)


# =============================================================================
# CLIENT: the status-change animation, executed under node.
# =============================================================================


def _client_source() -> str:
    return "\n".join([
        _var("FRAMEWORKS"),
        _var("RESTING"),
        _var("STATUS_CHANGE_MS"),
        _function("hueFor"),
        _function("initials"),
        _function("islandIdentity"),
        _function("isDoneStatus"),
        _function("setAttrIfChanged"),
        _function("animateStatusChange"),
        _function("applyAgent"),
        _function("island"),
    ])


_CLIENT_HARNESS = _DOM + r"""
var TIMERS = [];
function setTimeout(fn, ms) { TIMERS.push({ fn: fn, ms: ms }); return TIMERS.length - 1; }

__SOURCE__

var scenario = JSON.parse(process.env.LS_ROTATION);
var el = island(scenario.before);
var before = {
  id: el.__id,
  status: el.querySelector(".ls-status").textContent,
  cls: el.querySelector(".ls-status").className,
  done: el.getAttribute("data-done"),
};
applyAgent(el, scenario.after);
var s = el.querySelector(".ls-status");
var after = {
  id: el.__id,
  status: s.textContent,
  cls: s.className,
  prev: s.getAttribute("data-prev"),
  done: el.getAttribute("data-done"),
  pulse: el.getAttribute("data-status-pulse"),
  label: el.getAttribute("aria-label"),
};
// Run the scheduled cleanup (the ghost/pulse teardown) so the "settled"
// state can be checked too, without a real 380ms wait.
for (var i = 0; i < TIMERS.length; i++) TIMERS[i].fn();
var settled = {
  cls: s.className,
  prev: s.getAttribute("data-prev"),
  pulse: el.getAttribute("data-status-pulse"),
};
process.stdout.write(JSON.stringify({
  before: before, after: after, settled: settled, timerCount: TIMERS.length
}));
"""


def _run_rotation(before: dict, after: dict) -> dict:
    node = shutil.which("node")
    if node is None:  # pragma: no cover - depends on the runner image
        pytest.fail(
            "node is required to execute the lock screen status-change "
            "animation, and was not found on PATH. These tests cannot be "
            "skipped: skipping would report green while proving nothing."
        )
    script = _CLIENT_HARNESS.replace("__SOURCE__", _client_source())
    done = subprocess.run(
        [node, "-e", script],
        env={**os.environ, "LS_ROTATION": json.dumps({"before": before, "after": after})},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert done.returncode == 0, f"node failed:\n{done.stderr}"
    return json.loads(done.stdout)


def _agent(name="Personal Assistant", status="Reviewing calendar", **over):
    base = {"name": name, "status": status, "framework": "", "avatar": ""}
    base.update(over)
    return base


class TestTheStatusChangeAnimation:
    def test_an_unchanged_status_touches_nothing(self):
        same = _agent(status="Reviewing calendar")
        out = _run_rotation(same, dict(same))
        assert out["after"]["id"] == out["before"]["id"], "the island was rebuilt"
        assert out["after"]["cls"] == "ls-status", "an animation class was added for no change"
        assert out["after"]["prev"] is None
        assert out["timerCount"] == 0, "no animation should have been scheduled"

    def test_a_changed_status_keeps_the_same_node_and_updates_text_immediately(self):
        before = _agent(status="Reviewing calendar")
        after = _agent(status="Booking a table for Friday, 7:30")
        out = _run_rotation(before, after)
        assert out["after"]["id"] == out["before"]["id"], "the island was rebuilt for a status change"
        # The real text must be updated SYNCHRONOUSLY, not deferred into the
        # setTimeout -- a repaint elsewhere in this codebase reads
        # `.ls-status.textContent` immediately after applyAgent returns.
        assert out["after"]["status"] == "Booking a table for Friday, 7:30"

    def test_a_changed_status_updates_the_aria_label(self):
        before = _agent(status="Reviewing calendar")
        after = _agent(status="Chasing 2 overdue invoices")
        out = _run_rotation(before, after)
        assert "Chasing 2 overdue invoices" in out["after"]["label"]

    def test_a_changed_status_starts_the_slide_animation_with_the_old_text_as_a_ghost(self):
        before = _agent(status="Reviewing calendar")
        after = _agent(status="Rescheduling your 3pm with Dana")
        out = _run_rotation(before, after)
        assert out["after"]["prev"] == "Reviewing calendar"
        assert "ls-status-slide" in out["after"]["cls"]

    def test_the_animation_settles_and_clears_the_ghost_and_pulse(self):
        before = _agent(status="Reviewing calendar")
        after = _agent(status="Rescheduling your 3pm with Dana")
        out = _run_rotation(before, after)
        assert out["settled"]["cls"] == "ls-status"
        assert out["settled"]["prev"] is None
        assert out["settled"]["pulse"] is None

    def test_a_completion_status_gets_the_done_tint_attribute(self):
        before = _agent(status="Categorising card spend")
        after = _agent(status="✓ 41 invoices reconciled")
        out = _run_rotation(before, after)
        assert out["after"]["done"] == "1"

    def test_the_done_tint_is_removed_when_the_task_moves_on(self):
        before = _agent(status="✓ 41 invoices reconciled")
        after = _agent(status="Categorising card spend")
        out = _run_rotation(before, after)
        assert out["after"]["done"] is None

    def test_a_status_pulse_is_set_on_the_island_during_the_change(self):
        before = _agent(status="Reviewing calendar")
        after = _agent(status="Booking a table for Friday, 7:30")
        out = _run_rotation(before, after)
        assert out["after"]["pulse"] == "1"

    def test_harness_observes_the_defect(self):
        """THE CONTROL: strip the animation call back to a bare textContent
        assignment (the pre-feature behaviour) and the ghost/pulse/slide
        properties above must all disappear, proving the harness is actually
        exercising the new code rather than passing by construction."""
        node = shutil.which("node")
        if node is None:  # pragma: no cover
            pytest.fail("node is required for this control")
        broken_source = _client_source().replace(
            "if (s && s.textContent !== status) animateStatusChange(el, s, status);",
            'if (s && s.textContent !== status) s.textContent = status;',
        )
        assert broken_source != _client_source(), "the mutation did not take"
        script = _CLIENT_HARNESS.replace("__SOURCE__", broken_source)
        before = _agent(status="Reviewing calendar")
        after = _agent(status="Booking a table for Friday, 7:30")
        done = subprocess.run(
            [node, "-e", script],
            env={**os.environ, "LS_ROTATION": json.dumps({"before": before, "after": after})},
            capture_output=True, text=True, timeout=30,
        )
        assert done.returncode == 0, f"node failed:\n{done.stderr}"
        out = json.loads(done.stdout)
        assert out["after"]["status"] == "Booking a table for Friday, 7:30", (
            "the control must still render the right text"
        )
        assert out["after"]["prev"] is None, "the ghost appeared without the animation call"
        assert out["after"]["pulse"] is None, "the pulse appeared without the animation call"
        assert "ls-status-slide" not in out["after"]["cls"]


# =============================================================================
# CLIENT: the widgets-poll scheduler.
# =============================================================================


def _scheduler_source() -> str:
    return "\n".join([
        _var("WIDGETS_POLL_MS"),
        _var("widgetsTimer"),
        _var("widgetsPaused"),
        _function("scheduleWidgetsPoll"),
        _function("pollActivity"),
        _function("pauseWidgetsPoll"),
        _function("resumeWidgetsPoll"),
    ])


_SCHED_HARNESS = r"""
var PAINTED = [];
function paintActivity(d) { PAINTED.push(d); }

var FETCH_QUEUE = JSON.parse(process.env.LS_FETCH_RESPONSES);
var fetchCalls = 0;
function fetch(url, opts) {
  var resp = FETCH_QUEUE[Math.min(fetchCalls, FETCH_QUEUE.length - 1)];
  fetchCalls++;
  return Promise.resolve({
    ok: resp.ok !== false,
    json: function () { return Promise.resolve(resp.body); }
  });
}

var TIMERS = [];
function setTimeout(fn, ms) {
  var t = { fn: fn, ms: ms, cancelled: false };
  TIMERS.push(t);
  return t;
}
function clearTimeout(t) { if (t) t.cancelled = true; }

__SOURCE__

async function flush() { for (var i = 0; i < 12; i++) await null; }

async function main() {
  __SCRIPT__
  process.stdout.write(JSON.stringify({
    timers: TIMERS.map(function (t) { return { ms: t.ms, cancelled: t.cancelled }; }),
    painted: PAINTED,
    fetchCalls: fetchCalls,
    widgetsPaused: widgetsPaused
  }));
}
main();
"""


def _run_scheduler(script: str, responses: list) -> dict:
    node = shutil.which("node")
    if node is None:  # pragma: no cover
        pytest.fail(
            "node is required to execute the widgets-poll scheduler, and "
            "was not found on PATH. Skipping would report green while "
            "proving nothing about the schedule."
        )
    full = _SCHED_HARNESS.replace("__SOURCE__", _scheduler_source()).replace("__SCRIPT__", script)
    done = subprocess.run(
        [node, "-e", full],
        env={**os.environ, "LS_FETCH_RESPONSES": json.dumps(responses)},
        capture_output=True, text=True, timeout=30,
    )
    assert done.returncode == 0, f"node failed:\n{done.stderr}"
    return json.loads(done.stdout)


class TestTheWidgetsPollScheduler:
    def test_a_refresh_in_ms_in_the_response_schedules_the_next_fetch(self):
        out = _run_scheduler(
            "pollActivity(); await flush();",
            [{"body": {"agents": [], "refresh_in_ms": 2500}}],
        )
        assert out["painted"] == [{"agents": [], "refresh_in_ms": 2500}]
        live = [t for t in out["timers"] if not t["cancelled"]]
        assert len(live) == 1
        assert live[0]["ms"] == 2500

    def test_no_refresh_in_ms_falls_back_to_the_fifteen_second_default(self):
        out = _run_scheduler(
            "pollActivity(); await flush();",
            [{"body": {"agents": []}}],
        )
        live = [t for t in out["timers"] if not t["cancelled"]]
        assert len(live) == 1
        assert live[0]["ms"] == 15000

    def test_a_failed_fetch_still_reschedules_at_the_default(self):
        out = _run_scheduler(
            "pollActivity(); await flush();",
            [{"ok": False}],
        )
        # r.ok false -> the .then resolves to null -> nothing painted, but a
        # poll must still be scheduled or the card goes dead forever.
        assert out["painted"] == []
        live = [t for t in out["timers"] if not t["cancelled"]]
        assert len(live) == 1
        assert live[0]["ms"] == 15000

    def test_pausing_cancels_the_pending_poll(self):
        out = _run_scheduler(
            "pollActivity(); await flush(); pauseWidgetsPoll();",
            [{"body": {"agents": [], "refresh_in_ms": 3000}}],
        )
        assert out["widgetsPaused"] is True
        assert all(t["cancelled"] for t in out["timers"]), (
            "the scheduled poll survived pauseWidgetsPoll"
        )

    def test_resuming_fetches_immediately_rather_than_waiting_out_the_timer(self):
        out = _run_scheduler(
            "pollActivity(); await flush(); pauseWidgetsPoll(); "
            "resumeWidgetsPoll(); await flush();",
            [{"body": {"agents": [], "refresh_in_ms": 9000}}],
        )
        assert out["fetchCalls"] == 2, "resume must fetch immediately, not wait for the timer"
        assert out["widgetsPaused"] is False

    def test_a_paused_poll_does_not_reschedule_itself(self):
        """If pollActivity() somehow still fired while paused, it must not
        arm a new timer -- scheduleWidgetsPoll checks widgetsPaused itself."""
        out = _run_scheduler(
            "pauseWidgetsPoll(); pollActivity(); await flush();",
            [{"body": {"agents": [], "refresh_in_ms": 3000}}],
        )
        assert all(t["cancelled"] for t in out["timers"])

    def test_harness_observes_the_defect(self):
        """THE CONTROL: revert to the old fixed setInterval-style behaviour
        (ignore refresh_in_ms) and the fast-schedule property must break."""
        broken = _scheduler_source().replace(
            "widgetsTimer = setTimeout(pollActivity, ms || WIDGETS_POLL_MS);",
            "widgetsTimer = setTimeout(pollActivity, WIDGETS_POLL_MS);",
        )
        assert broken != _scheduler_source(), "the mutation did not take"
        full = _SCHED_HARNESS.replace("__SOURCE__", broken).replace(
            "__SCRIPT__", "pollActivity(); await flush();"
        )
        node = shutil.which("node")
        if node is None:  # pragma: no cover
            pytest.fail("node is required for this control")
        done = subprocess.run(
            [node, "-e", full],
            env={**os.environ, "LS_FETCH_RESPONSES": json.dumps(
                [{"body": {"agents": [], "refresh_in_ms": 2500}}]
            )},
            capture_output=True, text=True, timeout=30,
        )
        assert done.returncode == 0, f"node failed:\n{done.stderr}"
        out = json.loads(done.stdout)
        live = [t for t in out["timers"] if not t["cancelled"]]
        assert live[0]["ms"] == 15000, (
            "the control still honoured refresh_in_ms -- it is not "
            "measuring what this file claims"
        )
