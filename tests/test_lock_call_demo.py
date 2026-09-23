"""The lock screen's incoming-call demo: Mary rings, the PA takes a message.

Jay: an incoming call lands in the lock screen's live zone with Answer, Decline,
Send to voicemail and the headline button, **Send to PA**. The PA then talks to
the caller in a live transcript, with **Take over** and **End call** visible the
whole time, and when it finishes a notification says **New event added · Call
Mary · 5:30 pm**.

THE THING TO KEEP HOLD OF: **the lock screen renders before sign-in.** So the
caller, the script and the calendar event are fixed strings in the controller,
behind their own flag, and nothing here reaches a phone line, a contact list or
a calendar. The tests below assert the absence of those paths as well as the
presence of the demo.

The hostile cases come FIRST, on purpose: every earlier miss on this screen was
a happy-path-only suite. Flag off, off-console, malformed bodies, unknown
actions and every wrong-state transition are pinned before the timeline is.

The timeline itself runs on an INJECTED clock. The server has no background
thread -- the PA's progress is a pure function of the clock -- so a test can
stand at any instant of the thirty-second script without sleeping through it.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import tinyagentos.routes.auth as auth
from tinyagentos.auth_middleware import EXEMPT_PATHS


class _Req:
    """Enough of a Request for the handlers under test."""

    def __init__(self, body=None, *, raw_error=False):
        self._body = body
        self._raw_error = raw_error

    async def json(self):
        if self._raw_error:
            # What starlette raises for a body that is not JSON at all.
            raise json.JSONDecodeError("Expecting value", "not json", 0)
        return self._body


class _Clock:
    """A monotonic clock the test moves by hand."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance_ms(self, ms):
        self.t += ms / 1000.0


def _call(coro):
    return asyncio.run(coro)


def _body(resp):
    return json.loads(bytes(resp.body))


ROUTES = [
    ("lock_call", None),
    ("lock_call_ring", None),
    ("lock_call_reset", None),
    ("lock_call_action", {"action": "pa"}),
    ("lock_call_dismiss", None),
]


@pytest.fixture
def clock(monkeypatch):
    """A fresh call on a hand-driven clock, console request, flag ON."""
    clk = _Clock()
    monkeypatch.setattr(auth, "_LOCK_CALL", auth._LockCall(clock=clk, wall=lambda: 1.7e9))
    monkeypatch.setattr(auth, "_request_is_console", lambda _r: True)
    monkeypatch.setenv("TAOS_LOCK_DEMO_CALL", "1")
    return clk


def _act(action):
    resp = _call(auth.lock_call_action(_Req({"action": action})))
    return resp.status_code, _body(resp)


def _get():
    return _body(_call(auth.lock_call(_Req())))


def _ring():
    resp = _call(auth.lock_call_ring(_Req()))
    return resp.status_code, _body(resp)


# ------------------------------------------------------------- hostile first


class TestTheGate:
    @pytest.mark.parametrize("name, body", ROUTES)
    def test_flag_off_is_404_on_every_route(self, monkeypatch, name, body):
        """Off means ABSENT, not "empty": the page reads 404 as "no feature"
        and stops polling."""
        monkeypatch.setattr(auth, "_request_is_console", lambda _r: True)
        monkeypatch.delenv("TAOS_LOCK_DEMO_CALL", raising=False)
        resp = _call(getattr(auth, name)(_Req(body)))
        assert resp.status_code == 404, name

    @pytest.mark.parametrize("name, body", ROUTES)
    def test_off_console_is_403_even_with_the_flag_on(self, monkeypatch, name, body):
        """Console-only is the whole perimeter of a pre-auth route. Checked
        BEFORE the flag, so a remote caller cannot even learn whether the demo
        is on."""
        monkeypatch.setattr(auth, "_request_is_console", lambda _r: False)
        monkeypatch.setenv("TAOS_LOCK_DEMO_CALL", "1")
        resp = _call(getattr(auth, name)(_Req(body)))
        assert resp.status_code == 403, name

    @pytest.mark.parametrize("name, body", ROUTES)
    def test_off_console_with_the_flag_off_is_still_403(self, monkeypatch, name, body):
        monkeypatch.setattr(auth, "_request_is_console", lambda _r: False)
        monkeypatch.delenv("TAOS_LOCK_DEMO_CALL", raising=False)
        assert _call(getattr(auth, name)(_Req(body))).status_code == 403

    def test_the_call_flag_is_independent_of_the_other_demo_flags(self, monkeypatch):
        """Its own switch, both ways: the master flag does not turn it on, and
        its absence does not stop the call from working."""
        monkeypatch.setenv("TAOS_LOCK_DEMO_AGENTS", "a,b")
        monkeypatch.setenv("TAOS_LOCK_DEMO_NOTIFICATIONS", "1")
        monkeypatch.delenv("TAOS_LOCK_DEMO_CALL", raising=False)
        assert auth._call_demo_enabled() is False
        monkeypatch.delenv("TAOS_LOCK_DEMO_AGENTS", raising=False)
        monkeypatch.setenv("TAOS_LOCK_DEMO_CALL", "1")
        assert auth._call_demo_enabled() is True

    def test_every_route_is_exempt_from_the_session_gate(self):
        """Fetched before sign-in. /auth/lock-stats once shipped without this
        and 401'd on the glass with no error anywhere."""
        for path in ("/auth/lock-call", "/auth/lock-call/ring",
                     "/auth/lock-call/reset", "/auth/lock-call/action",
                     "/auth/lock-call/dismiss"):
            assert path in EXEMPT_PATHS, path


class TestMalformedBodies:
    def test_a_body_that_is_not_json_is_400(self, clock):
        _ring()
        resp = _call(auth.lock_call_action(_Req(raw_error=True)))
        assert resp.status_code == 400

    @pytest.mark.parametrize("body", [
        None, [], ["pa"], "pa", 7, {}, {"action": None}, {"action": 3},
        {"action": ["pa"]}, {"verb": "pa"},
    ])
    def test_a_body_without_a_string_action_is_400(self, clock, body):
        _ring()
        resp = _call(auth.lock_call_action(_Req(body)))
        assert resp.status_code == 400, body
        # And nothing moved: a refused request must not half-apply.
        assert _get()["state"] == "ringing"

    @pytest.mark.parametrize("action", [
        "", "PA", "hangup", "answer ", "reboot", "pa;end", "../pa",
        pytest.param("a" * 5000, id="5000-chars"),
    ])
    def test_an_unknown_action_is_400_not_409(self, clock, action):
        """Unknown is a malformed request, whatever the state. "answer " is
        stripped and so IS known -- the one entry that must not be refused."""
        _ring()
        status, _ = _act(action)
        if action == "answer ":
            assert status == 200
        else:
            assert status == 400, action

    def test_an_unknown_action_is_400_even_while_idle(self, clock):
        """400 outranks 409: an unknown verb is wrong in every state."""
        assert _act("hangup")[0] == 400


class TestWrongStateTransitions:
    """Every action at every moment it does not belong is a 409, and leaves the
    call exactly where it was."""

    IDLE_OR_ENDED_REFUSE = ["answer", "decline", "voicemail", "pa", "takeover", "end"]

    @pytest.mark.parametrize("action", IDLE_OR_ENDED_REFUSE)
    def test_nothing_is_allowed_while_idle(self, clock, action):
        status, body = _act(action)
        assert status == 409, action
        assert body["state"] == "idle"
        assert _get()["state"] == "idle"

    @pytest.mark.parametrize("action", ["takeover", "end"])
    def test_ringing_refuses_the_in_call_actions(self, clock, action):
        _ring()
        assert _act(action)[0] == 409
        assert _get()["state"] == "ringing"

    @pytest.mark.parametrize("action", ["answer", "decline", "voicemail", "pa"])
    def test_the_pa_phase_refuses_the_ringing_actions(self, clock, action):
        _ring(); _act("pa")
        assert _act(action)[0] == 409
        assert _get()["state"] == "pa"

    @pytest.mark.parametrize("action", ["answer", "decline", "voicemail", "pa", "takeover"])
    def test_a_live_call_only_ends(self, clock, action):
        _ring(); _act("answer")
        assert _act(action)[0] == 409
        assert _get()["state"] == "live"

    @pytest.mark.parametrize("action", IDLE_OR_ENDED_REFUSE)
    def test_an_ended_call_refuses_everything(self, clock, action):
        _ring(); _act("decline")
        assert _act(action)[0] == 409
        snap = _get()
        assert (snap["state"], snap["outcome"]) == ("ended", "declined")

    def test_a_takeover_after_the_pa_finished_does_not_revive_the_call(self, clock):
        """The lazy step is taken BEFORE the action is judged. Without that, a
        take-over tapped a moment after the last line would flip a finished
        call back to live and lose the event it had just added."""
        _ring(); _act("pa")
        clock.advance_ms(auth._LOCK_CALL._script_ms + 10)
        status, body = _act("takeover")
        assert status == 409
        assert body["state"] == "ended"
        assert _get()["notification"] is not None


class TestRinging:
    def test_ringing_while_ringing_is_idempotent(self, clock):
        """Defined, not accidental: a second press of the demo button returns
        the SAME call, not a new one and not an error."""
        s1, b1 = _ring()
        s2, b2 = _ring()
        assert (s1, s2) == (201, 200)
        assert b1["call_id"] == b2["call_id"]
        assert b2["state"] == "ringing"

    @pytest.mark.parametrize("path", [["pa"], ["answer"]])
    def test_ringing_during_a_call_is_refused(self, clock, path):
        _ring()
        for a in path:
            _act(a)
        status, body = _ring()
        assert status == 409
        assert body["state"] == ("pa" if path[0] == "pa" else "live")
        assert _get()["state"] == body["state"]

    def test_ringing_after_a_finished_call_is_a_new_call(self, clock):
        _, first = _ring()
        _act("voicemail")
        status, second = _ring()
        assert status == 201
        assert second["call_id"] == first["call_id"] + 1
        assert second["transcript"] == [] and second["outcome"] is None

    def test_the_caller_is_scripted_and_fictional(self, clock):
        """07700 900xxx is Ofcom's drama range and can never reach a real
        subscriber. Asserted on the payload so a later "real caller id" cannot
        slip in unnoticed."""
        _, body = _ring()
        assert body["demo"] is True
        assert body["caller"]["name"] == "Mary"
        assert body["caller"]["label"] == "mobile"
        assert body["caller"]["number"].startswith("07700 900")
        assert set(body["caller"]) == {"name", "label", "number"}

    def test_the_ring_is_pushed_to_an_open_page(self, clock):
        auth._LOCK_EVENT_WAITERS.clear()
        queue: asyncio.Queue = asyncio.Queue(maxsize=8)
        auth._LOCK_EVENT_WAITERS.add(queue)
        try:
            _, body = _ring()
            assert body["delivered"] == 1
            # A signal only -- the stream carries no content.
            assert queue.get_nowait() == ("call", {"state": "ringing"})
        finally:
            auth._LOCK_EVENT_WAITERS.clear()

    def test_idle_carries_no_caller(self, clock):
        snap = _get()
        assert snap["state"] == "idle" and snap["caller"] is None


class TestTheOutcomes:
    @pytest.mark.parametrize("action, outcome", [
        ("decline", "declined"), ("voicemail", "voicemail"),
    ])
    def test_ringing_ends_with_its_outcome(self, clock, action, outcome):
        _ring()
        status, body = _act(action)
        assert status == 200
        assert (body["state"], body["outcome"]) == ("ended", outcome)
        assert body["notification"] is None

    def test_answering_goes_live_with_no_transcript(self, clock):
        _ring()
        status, body = _act("answer")
        assert (status, body["state"], body["transcript"]) == (200, "live", [])
        assert body["taken_over"] is False

    def test_ending_a_live_call(self, clock):
        _ring(); _act("answer")
        status, body = _act("end")
        assert (body["state"], body["outcome"]) == ("ended", "ended-by-user")
        assert body["notification"] is None

    def test_reset_returns_to_idle_from_anywhere(self, clock):
        _ring(); _act("pa")
        body = _body(_call(auth.lock_call_reset(_Req())))
        assert body["state"] == "idle"
        assert _get()["transcript"] == []


# ------------------------------------------------------------- the timeline


def _line_at(index):
    return auth._call_timeline()[index]


class TestTheScript:
    def test_the_opening_line_is_jays_verbatim(self):
        assert auth._CALL_SCRIPT[0] == (
            "pa",
            "Hi, and thank you for calling Jason's phone. This is his personal "
            "assistant speaking — can I take a message?",
        )

    def test_the_turns_alternate_pa_first(self):
        whos = [who for who, _ in auth._CALL_SCRIPT]
        assert whos == ["pa", "caller"] * 3

    def test_a_longer_line_lasts_longer(self):
        """Durations come from word count, not a table: editing a line cannot
        leave its timing describing the old one."""
        tl = auth._call_timeline()
        by_words = sorted(tl, key=lambda l: len(l["text"].split()))
        assert by_words[0]["dur_ms"] < by_words[-1]["dur_ms"]
        for line in tl:
            words = len(line["text"].split())
            assert line["dur_ms"] == max(auth._CALL_MIN_LINE_MS,
                                         words * auth._CALL_MS_PER_WORD)

    def test_lines_never_overlap(self):
        tl = auth._call_timeline()
        for a, b in zip(tl, tl[1:]):
            assert a["at_ms"] + a["dur_ms"] < b["at_ms"]

    def test_the_script_runs_under_half_a_minute(self):
        """Long enough to watch and to take over; short enough for a demo."""
        total = auth._LockCall(clock=_Clock())._script_ms
        assert 20_000 <= total <= 28_000

    def test_the_voices_are_about_a_fifth_faster_than_before(self):
        """Jay, from the glass: "the agent needs to appear to be talking
        slightly faster". It was 360ms a word, so the PA's opening line took
        7200ms. Both voices share the pace, so the caller's lines speed up too
        and the exchange keeps its rhythm."""
        tl = auth._call_timeline()
        assert tl[0]["who"] == "pa" and tl[0]["dur_ms"] == 6000
        assert auth._CALL_MS_PER_WORD <= 360 / 1.2
        caller = [l for l in tl if l["who"] == "caller"][0]
        assert caller["dur_ms"] == len(caller["text"].split()) * auth._CALL_MS_PER_WORD
        # The gaps were trimmed as well, but not to nothing: a turn still needs
        # a breath, or the transcript reads as one voice.
        assert 300 <= auth._CALL_GAP_MS < 650
        for a, b in zip(tl, tl[1:]):
            assert b["at_ms"] - (a["at_ms"] + a["dur_ms"]) == auth._CALL_GAP_MS


class TestTheTimeline:
    def test_nothing_is_said_during_the_pick_up(self, clock):
        _ring(); _act("pa")
        clock.advance_ms(_line_at(0)["at_ms"] - 1)
        snap = _get()
        assert snap["transcript"] == [] and snap["speaking"] is None

    def test_the_first_line_starts_on_time_and_progresses(self, clock):
        _ring(); _act("pa")
        first = _line_at(0)
        clock.advance_ms(first["at_ms"])
        snap = _get()
        assert [l["who"] for l in snap["transcript"]] == ["pa"]
        assert snap["speaking"] == {"who": "pa", "line": 0, "progress": 0.0}
        clock.advance_ms(first["dur_ms"] / 2)
        assert _get()["speaking"]["progress"] == pytest.approx(0.5, abs=0.01)

    def test_the_gap_between_lines_has_no_speaker(self, clock):
        _ring(); _act("pa")
        first = _line_at(0)
        clock.advance_ms(first["at_ms"] + first["dur_ms"] + 1)
        snap = _get()
        assert snap["speaking"] is None
        assert len(snap["transcript"]) == 1

    def test_each_line_appears_at_its_offset_and_not_before(self, clock):
        _ring(); _act("pa")
        tl = auth._call_timeline()
        start = clock.t
        for i, line in enumerate(tl):
            clock.t = start + (line["at_ms"] - 1) / 1000.0
            assert len(_get()["transcript"]) == i, f"line {i} early"
            clock.t = start + (line["at_ms"] + 1) / 1000.0
            snap = _get()
            assert len(snap["transcript"]) == i + 1, f"line {i} late"
            assert snap["speaking"]["who"] == line["who"]
            assert snap["transcript"][-1]["text"] == line["text"]

    def test_the_call_timer_counts_from_the_pick_up(self, clock):
        _ring()
        clock.advance_ms(4000)          # ringing time is not call time
        _act("pa")
        clock.advance_ms(2500)
        assert _get()["elapsed_ms"] == 2500


class TestTheNotification:
    def test_it_appears_only_once_the_pa_has_finished(self, clock):
        _ring(); _act("pa")
        total = auth._LOCK_CALL._script_ms
        clock.advance_ms(total - 1)
        snap = _get()
        assert snap["state"] == "pa" and snap["notification"] is None
        clock.advance_ms(2)
        snap = _get()
        assert (snap["state"], snap["outcome"]) == ("ended", "pa-done")
        note = snap["notification"]
        assert note["kind"] == "reminder"
        assert note["title"] == "Reminder added"
        assert note["body"] == ("Reminder to call Mary at 5:30 pm added to your "
                                "calendar. I'll remind you closer to the time.")
        assert (note["event"], note["time"], note["from"]) == ("Call Mary", "5:30 pm", "Your PA")
        assert note["demo"] is True

    def test_the_finished_transcript_is_whole(self, clock):
        _ring(); _act("pa")
        clock.advance_ms(auth._LOCK_CALL._script_ms + 5000)
        snap = _get()
        assert [l["text"] for l in snap["transcript"]] == [t for _, t in auth._CALL_SCRIPT]
        assert all("upto" not in l for l in snap["transcript"])

    def test_it_is_not_a_notification_stack(self, clock, monkeypatch):
        """The reminder is a card in Alerts with a Dismiss button, driven by
        the call snapshot. Served as a notification stack as well it would be
        drawn twice, and the stack copy could not be dismissed."""
        monkeypatch.setenv("TAOS_LOCK_DEMO_AGENTS", "a")
        monkeypatch.setenv("TAOS_LOCK_DEMO_NOTIFICATIONS", "1")
        _ring(); _act("pa")
        clock.advance_ms(auth._LOCK_CALL._script_ms + 1)
        assert _get()["notification"] is not None
        groups = _body(_call(auth.lock_notifications(_Req())))["groups"]
        assert len(groups) == len(auth._DEMO_NOTIFICATIONS)
        assert all(g["source"] != "calendar" for g in groups)

    @pytest.mark.parametrize("line", [0, 2, 4])
    def test_a_take_over_suppresses_it(self, clock, line):
        """Taken over before the PA confirmed the time -- or even mid-way
        through the confirming line itself -- nothing was agreed, so nothing is
        added. Line 4 is the confirmation."""
        _ring(); _act("pa")
        l = _line_at(line)
        clock.advance_ms(l["at_ms"] + l["dur_ms"] / 2)
        status, body = _act("takeover")
        assert (status, body["state"], body["taken_over"]) == (200, "live", True)
        clock.advance_ms(auth._LOCK_CALL._script_ms * 2)
        snap = _get()
        assert snap["state"] == "live" and snap["notification"] is None
        _act("end")
        assert _get()["notification"] is None

    def test_the_taken_over_transcript_freezes_where_it_was_cut(self, clock):
        _ring(); _act("pa")
        l = _line_at(2)
        clock.advance_ms(l["at_ms"] + l["dur_ms"] / 4)
        _act("takeover")
        clock.advance_ms(60_000)
        snap = _get()
        assert len(snap["transcript"]) == 3
        assert snap["transcript"][-1]["upto"] == pytest.approx(0.25, abs=0.01)
        assert snap["speaking"] is None

    def test_ending_during_the_pa_suppresses_it(self, clock):
        _ring(); _act("pa")
        clock.advance_ms(_line_at(3)["at_ms"])
        status, body = _act("end")
        assert (body["state"], body["outcome"]) == ("ended", "ended-by-user")
        clock.advance_ms(auth._LOCK_CALL._script_ms)
        assert _get()["notification"] is None

    def test_reset_clears_it(self, clock):
        _ring(); _act("pa")
        clock.advance_ms(auth._LOCK_CALL._script_ms + 1)
        assert _get()["notification"] is not None
        _call(auth.lock_call_reset(_Req()))
        assert _get()["notification"] is None

    def test_a_new_call_keeps_the_event_the_last_one_added(self, clock):
        """"For the rest of the process lifetime (until reset)"."""
        _ring(); _act("pa")
        clock.advance_ms(auth._LOCK_CALL._script_ms + 1)
        _get()
        _ring()
        assert _get()["notification"] is not None


# ------------------------------------------------------------- dismissing it


def _dismiss():
    resp = _call(auth.lock_call_dismiss(_Req()))
    return resp.status_code, _body(resp)


def _finish_a_pa_call(clock):
    _ring(); _act("pa")
    clock.advance_ms(auth._LOCK_CALL._script_ms + 1)
    assert _get()["notification"] is not None


class TestDismissingTheReminder:
    """Jay: the reminder goes in Alerts "with a dismiss button". Hostile
    cases first."""

    def test_dismissing_nothing_is_409(self, clock):
        status, body = _dismiss()
        assert status == 409
        assert body["error"] == "nothing to dismiss"

    @pytest.mark.parametrize("path", [[], ["ring"], ["ring", "pa"], ["ring", "decline"]])
    def test_dismissing_nothing_is_409_in_every_state(self, clock, path):
        for step in path:
            _ring() if step == "ring" else _act(step)
        state = _get()["state"]
        assert _dismiss()[0] == 409
        assert _get()["state"] == state          # and nothing moved

    def test_off_console_is_403_and_leaves_the_reminder(self, clock, monkeypatch):
        _finish_a_pa_call(clock)
        monkeypatch.setattr(auth, "_request_is_console", lambda _r: False)
        assert _dismiss()[0] == 403
        monkeypatch.setattr(auth, "_request_is_console", lambda _r: True)
        assert _get()["notification"] is not None

    def test_flag_off_is_404_and_leaves_the_reminder(self, clock, monkeypatch):
        _finish_a_pa_call(clock)
        monkeypatch.delenv("TAOS_LOCK_DEMO_CALL", raising=False)
        assert _dismiss()[0] == 404
        monkeypatch.setenv("TAOS_LOCK_DEMO_CALL", "1")
        assert _get()["notification"] is not None

    def test_a_second_dismiss_is_409(self, clock):
        """A double tap is visible as such, not silently a success twice."""
        _finish_a_pa_call(clock)
        assert _dismiss()[0] == 200
        assert _dismiss()[0] == 409

    def test_dismiss_then_poll_it_stays_gone(self, clock):
        """Cleared on the SERVER: the page polls every 2s, and a dismissal kept
        only in the page would come back on the next one."""
        _finish_a_pa_call(clock)
        status, body = _dismiss()
        assert status == 200 and body["notification"] is None
        for _ in range(3):
            clock.advance_ms(2000)
            assert _get()["notification"] is None

    def test_dismissing_leaves_the_call_itself_alone(self, clock):
        _finish_a_pa_call(clock)
        before = _get()
        _dismiss()
        after = _get()
        assert (after["state"], after["outcome"], after["call_id"]) == (
            before["state"], before["outcome"], before["call_id"])
        assert after["transcript"] == before["transcript"]

    def test_reset_after_dismiss(self, clock):
        _finish_a_pa_call(clock)
        _dismiss()
        body = _body(_call(auth.lock_call_reset(_Req())))
        assert body["state"] == "idle" and body["notification"] is None
        assert _dismiss()[0] == 409

    def test_the_dismissal_is_pushed_to_an_open_page(self, clock):
        _finish_a_pa_call(clock)
        auth._LOCK_EVENT_WAITERS.clear()
        queue: asyncio.Queue = asyncio.Queue(maxsize=8)
        auth._LOCK_EVENT_WAITERS.add(queue)
        try:
            _, body = _dismiss()
            assert body["delivered"] == 1
            assert queue.get_nowait() == ("call", {"state": "ended"})
        finally:
            auth._LOCK_EVENT_WAITERS.clear()

    def test_a_later_finished_call_sets_a_new_one(self, clock):
        _finish_a_pa_call(clock)
        _dismiss()
        _finish_a_pa_call(clock)
        assert _get()["notification"]["title"] == "Reminder added"


def test_the_pa_says_jason_with_a_capital_j():
    """Jay: "make sure the PA calls me Jason and not jason". The opening line
    is his, verbatim; nothing on the way to the screen may lowercase it."""
    from tinyagentos.routes import auth as _auth
    opening = _auth._CALL_SCRIPT[0][1]
    assert "Jason's phone" in opening
    assert "jason" not in opening
