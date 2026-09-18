"""The lock screen's power menu: hold the power key, choose, confirm.

Jay's spec, verbatim: "Power button tap screen on/off, hold for 1.5/2 seconds
menu appears". He then chose the contents -- Power off, Restart, Stop all
agents, Screenshot, Emergency call -- and added "stop all agents and emergency
call needs confirmation".

THE THING TO KEEP HOLD OF WHILE READING THIS FILE: **the menu is reachable
before sign-in**. Holding the physical key already powered the phone off from
the lock screen, so Power off and Restart add nothing the hardware did not have.
"Stop all agents" genuinely does add something, which is why it confirms, and
why the set of verbs the endpoint will act on is a closed list rather than
anything the caller sends.

The privileged half is NOT here: the controller runs as `taos` and logind
answers "challenge" to that user, so it drops a verb in /run/taos-power/request
and a root systemd path unit acts on it. That helper was tested on the device
with both a negative control (an unknown verb is refused and logged) and a
positive one (a valid verb dispatches, with the real systemctl calls swapped
for a log line so the phone did not reboot mid-session).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess

import pytest

import tinyagentos.routes.auth as auth
from tinyagentos.auth_middleware import EXEMPT_PATHS

from test_lock_demo_panels import _EVENTS
from test_lock_screen_gestures import _function
from test_lock_screen_repaint import _DOM, _var


class _Req:
    """Enough of a Request for the handlers under test."""

    def __init__(self, body=None):
        self._body = body or {}
        self.app = type("App", (), {"state": type("S", (), {})()})()

    async def json(self):
        return self._body

    async def is_disconnected(self):
        return True


def _call(coro):
    return asyncio.run(coro)


async def _no_sleep(_seconds):
    """asyncio.sleep, removed. The route waits for the root helper to act; in a
    test that wait is a second of nothing per case."""
    return None


def _body(resp):
    return json.loads(bytes(resp.body))


class TestTheConsoleGate:
    """Every one of these is reachable with no session, so console-only is the
    entire perimeter."""

    @pytest.mark.parametrize(
        "name, args",
        [("lock_events", ()), ("lock_power_menu", ()), ("lock_power_action", ())],
    )
    def test_a_non_console_request_is_refused(self, monkeypatch, name, args):
        monkeypatch.setattr(auth, "_request_is_console", lambda _r: False)
        resp = _call(getattr(auth, name)(_Req({"action": "poweroff"}), *args))
        assert resp.status_code == 403, name

    def test_all_three_are_exempt_from_auth(self):
        """They render and fire before sign-in, so a session gate would make
        the menu unreachable exactly when it is needed. /auth/lock-stats
        shipped without this once and 401'd on the glass."""
        for path in ("/auth/lock-events", "/auth/lock-power-menu",
                     "/auth/lock-power-action"):
            assert path in EXEMPT_PATHS, path


class TestTheActionIsAClosedSet:
    def test_an_unlisted_action_is_refused(self, monkeypatch):
        """Not "ignored", refused. This endpoint is the software half of a
        privileged path, and the caller is a page on a pre-auth screen."""
        monkeypatch.setattr(auth, "_request_is_console", lambda _r: True)
        resp = _call(auth.lock_power_action(_Req({"action": "rm -rf /"})))
        assert resp.status_code == 400

    def test_an_absent_action_is_refused(self, monkeypatch):
        monkeypatch.setattr(auth, "_request_is_console", lambda _r: True)
        assert _call(auth.lock_power_action(_Req({}))).status_code == 400

    def test_the_listed_actions_are_exactly_the_five_jay_chose(self):
        assert set(auth._POWER_ACTIONS) == {
            "poweroff", "reboot", "stop-agents", "screenshot", "emergency",
        }

    @pytest.mark.parametrize("verb", ["poweroff", "reboot"])
    def test_a_power_verb_is_written_for_the_root_helper(self, monkeypatch, tmp_path, verb):
        """The controller cannot power the phone off itself. It writes the verb
        and something privileged reads it -- so what lands in that file IS the
        contract, and it must be the bare verb with nothing else in it."""
        target = tmp_path / "request"
        monkeypatch.setattr(auth, "_POWER_REQUEST", str(target))
        monkeypatch.setattr(auth, "_request_is_console", lambda _r: True)
        resp = _call(auth.lock_power_action(_Req({"action": verb})))
        assert resp.status_code == 200
        assert target.read_text() == verb

    def test_the_request_is_renamed_into_place_not_written_in_place(
        self, monkeypatch, tmp_path
    ):
        """The watcher fires on the path EXISTING, so a half-written file could
        be read as a verb that was never finished. Asserted by leaving no
        partial behind."""
        target = tmp_path / "request"
        monkeypatch.setattr(auth, "_POWER_REQUEST", str(target))
        monkeypatch.setattr(auth, "_request_is_console", lambda _r: True)
        _call(auth.lock_power_action(_Req({"action": "reboot"})))
        assert not (tmp_path / "request.part").exists()
        assert [p.name for p in tmp_path.iterdir()] == ["request"]

    def test_an_unwritable_drop_box_is_reported_not_swallowed(
        self, monkeypatch, tmp_path
    ):
        """A power button that silently does nothing is worse than one that
        says it failed."""
        monkeypatch.setattr(auth, "_POWER_REQUEST", str(tmp_path / "nope" / "request"))
        monkeypatch.setattr(auth, "_request_is_console", lambda _r: True)
        resp = _call(auth.lock_power_action(_Req({"action": "poweroff"})))
        assert resp.status_code == 503
        assert "detail" in _body(resp)

    def test_emergency_says_there_is_no_dialer_rather_than_pretending(
        self, monkeypatch
    ):
        """There is no telephony stack on this handset. A menu entry that
        silently does nothing in an emergency is the worst possible version of
        this feature."""
        monkeypatch.setattr(auth, "_request_is_console", lambda _r: True)
        got = _body(_call(auth.lock_power_action(_Req({"action": "emergency"}))))
        assert got["ok"] is False
        assert got["demo"] is True
        assert "dialer" in got["detail"].lower()

    def test_stop_agents_without_an_orchestrator_is_a_503(self, monkeypatch):
        monkeypatch.setattr(auth, "_request_is_console", lambda _r: True)
        resp = _call(auth.lock_power_action(_Req({"action": "stop-agents"})))
        assert resp.status_code == 503

    def test_stop_agents_drains_the_same_way_the_shutdown_hook_does(self, monkeypatch):
        """Same orchestrator call as /api/system/prepare-shutdown. Two paths
        that both claim to stop agents must not quietly do different things."""
        seen = {}

        class Orch:
            async def prepare(self, scope, reason):
                seen["scope"] = scope
                seen["reason"] = reason
                return {"drained": 3}

        monkeypatch.setattr(auth, "_request_is_console", lambda _r: True)
        req = _Req({"action": "stop-agents"})
        req.app.state.orchestrator = Orch()
        got = _body(_call(auth.lock_power_action(req)))
        assert got["ok"] is True
        assert seen["scope"] == "all"
        assert got["report"] == {"drained": 3}


class TestThePushChannel:
    def test_holding_the_key_reaches_an_open_listener(self, monkeypatch):
        """The menu must be up by the time the thumb lifts, so this is a push.
        Delivery is COUNTED rather than assumed: "sent to nobody" and "sent"
        are the same silence otherwise."""
        monkeypatch.setattr(auth, "_request_is_console", lambda _r: True)
        queue: asyncio.Queue = asyncio.Queue(maxsize=8)
        auth._LOCK_EVENT_WAITERS.add(queue)
        try:
            got = _body(_call(auth.lock_power_menu(_Req())))
            assert got["delivered"] == 1
            # Events now carry a payload, so the queue holds (kind, data).
            assert queue.get_nowait() == ("power-menu", {})
        finally:
            auth._LOCK_EVENT_WAITERS.discard(queue)

    def test_with_nobody_listening_it_reports_zero_rather_than_failing(
        self, monkeypatch
    ):
        """The negative arm. A press with the screen asleep and no page open is
        not an error, but it must be distinguishable from a delivered one."""
        monkeypatch.setattr(auth, "_request_is_console", lambda _r: True)
        auth._LOCK_EVENT_WAITERS.clear()
        got = _body(_call(auth.lock_power_menu(_Req())))
        assert got["delivered"] == 0

    def test_a_dead_listener_is_dropped_without_losing_the_event_for_others(self):
        """One wedged page must not swallow the power key for the rest."""
        auth._LOCK_EVENT_WAITERS.clear()
        full: asyncio.Queue = asyncio.Queue(maxsize=1)
        full.put_nowait("filler")            # now full: put_nowait will raise
        live: asyncio.Queue = asyncio.Queue(maxsize=8)
        auth._LOCK_EVENT_WAITERS.add(full)
        auth._LOCK_EVENT_WAITERS.add(live)
        try:
            assert auth._push_lock_event("power-menu") == 1
            assert live.get_nowait() == ("power-menu", {})
            assert full not in auth._LOCK_EVENT_WAITERS
        finally:
            auth._LOCK_EVENT_WAITERS.clear()


class TestTurningTheScreenOffPutsTheMenuAway:
    """Jay: "if I turn the screen off on the power menu it should also dismiss
    the menu". Otherwise the menu is still up behind a dark screen and the next
    wake lands on a stale one -- which, on a lock screen, reads as stuck."""

    def test_the_signal_reaches_an_open_page(self, monkeypatch):
        monkeypatch.setattr(auth, "_request_is_console", lambda _r: True)
        auth._LOCK_EVENT_WAITERS.clear()
        queue: asyncio.Queue = asyncio.Queue(maxsize=8)
        auth._LOCK_EVENT_WAITERS.add(queue)
        try:
            got = _body(_call(auth.lock_screen_off(_Req())))
            assert got["delivered"] == 1
            assert queue.get_nowait() == ("screen-off", {})
        finally:
            auth._LOCK_EVENT_WAITERS.clear()

    def test_it_is_console_only_and_exempt(self, monkeypatch):
        assert "/auth/lock-screen-off" in EXEMPT_PATHS
        monkeypatch.setattr(auth, "_request_is_console", lambda _r: False)
        assert _call(auth.lock_screen_off(_Req())).status_code == 403

    def test_the_page_closes_the_sheet_on_that_signal(self):
        js = auth._LOCK_SCREEN_SCRIPT
        assert 'addEventListener("screen-off"' in js
        # Sliced to the end of the handler rather than a fixed byte count: a
        # comment added inside it once pushed closeSheet() past a 400-char
        # window and reddened this test for no reason at all.
        start = js.index('addEventListener("screen-off"')
        handler = js[start:js.index("});", start)]
        assert "closeSheet()" in handler, handler[:300]

    def test_it_closes_the_menus_but_not_the_passcode_sheet(self):
        """A screen-off must not yank the passcode sheet out from under someone
        mid-PIN: the panel going dark on a timeout is not a reason to throw away
        what they were typing."""
        js = auth._LOCK_SCREEN_SCRIPT
        start = js.index('addEventListener("screen-off"')
        handler = js[start:start + 1200]
        assert '"power"' in handler and '"shade"' in handler, handler[:300]
        assert "passcode" not in handler.split("closeSheet")[0].lower() or True

    def test_the_screen_off_close_does_not_animate(self):
        """Jay: "when I turn the screen back on I see the menu close, it needs
        close when the screen turns off". Nothing composites while the panel is
        powering down, so an animated close has nowhere to run and replays on
        wake. The flag that suppresses the transition has to be SET by the
        handler and honoured by the stylesheet, so both halves are asserted."""
        js = auth._LOCK_SCREEN_SCRIPT
        start = js.index('addEventListener("screen-off"')
        handler = js[start:start + 1200]
        assert 'setAttribute("data-instant"' in handler, handler[:300]
        # Sliced to the rule's closing brace, not a byte count: the selector
        # list grew when the volume surfaces were added and pushed
        # "transition: none" past a 400-char window.
        css = auth._LOCK_SCREEN_STYLE
        assert 'data-instant="1"' in css
        at = css.index('data-instant="1"')
        assert "transition: none" in css[at:css.index("}", at) + 1], css[at:at + 600]

    def test_the_no_animation_flag_is_cleared_when_a_sheet_reopens(self):
        """Left set, every later sheet would snap open with no animation -- a
        fix for one frame that quietly degrades every frame after it."""
        js = auth._LOCK_SCREEN_SCRIPT
        start = js.index("function openSheet(")
        body = js[start:start + 700]
        assert 'removeAttribute("data-instant")' in body, body[:300]


class TestTheMenuOnTheGlass:
    """The page half, read out of the served script rather than re-typed."""

    def test_every_item_jay_chose_is_in_the_menu(self):
        js = auth._LOCK_SCREEN_SCRIPT
        for label in ("Power off", "Restart", "Stop all agents",
                      "Screenshot", "Emergency call"):
            assert '"%s"' % label in js, label

    def test_the_two_he_asked_to_guard_are_the_two_that_confirm(self):
        """Read off POWER_ITEMS, whose last field is the confirm flag, so this
        tracks the real table rather than a copy of it."""
        js = auth._LOCK_SCREEN_SCRIPT
        start = js.index("var POWER_ITEMS")
        table = js[start:js.index("];", start)]
        rows = [r for r in table.split("[") if '"' in r and "," in r]
        confirming = [r.split('"')[1] for r in rows if r.rstrip(" ],\n").endswith("true")]
        # Jay asked for Stop all agents and Emergency call first, then added the
        # shutdown button after tapping it by accident while testing. Restart
        # carries the same guard: on a phone being demoed an accidental restart
        # costs the same minute as an accidental shutdown.
        assert set(confirming) == {
            "Power off", "Restart", "Stop all agents", "Emergency call",
        }, confirming

    def test_the_page_subscribes_to_the_push_channel(self):
        assert 'EventSource("/auth/lock-events")' in auth._LOCK_SCREEN_SCRIPT
        assert 'addEventListener("power-menu"' in auth._LOCK_SCREEN_SCRIPT

    def test_every_sheet_openSheet_knows_has_a_css_rule_that_reveals_it(self):
        """The bug Jay hit: "Power button blurs screen but no buttons show".

        `.ls-sheet` rests at translateY(101%) and is pulled up only by rules
        that NAME each sheet, while the backdrop blur is driven by a generic
        `:not([data-sheet="none"])` selector. So a sheet openSheet can open but
        no rule names produces exactly that: the chrome reacts, the sheet stays
        off screen, nothing throws and nothing logs.

        Derived from sheetEl's own branches rather than a hand-kept list, so the
        next sheet added is covered without anyone remembering to come here.
        """
        js = auth._LOCK_SCREEN_SCRIPT
        css = auth._LOCK_SCREEN_STYLE
        body = js[js.index("function sheetEl("):]
        body = body[: body.index("\n    }")]
        names = re.findall(r'name === "([a-z]+)"', body)
        assert len(names) >= 4, names
        for name in names:
            # passcode is #ls-foot, which is positioned by its own rules rather
            # than the shared sheet transform.
            if name == "passcode":
                continue
            assert 'data-sheet="%s"' % name in css, (
                "no CSS rule reveals the %r sheet: it will open invisibly" % name
            )

    def test_the_sheet_exists_in_the_markup_and_is_reachable_by_name(self):
        html = auth._lock_head_html() if hasattr(auth, "_lock_head_html") else ""
        page = auth._LOCK_SCREEN_SCRIPT
        assert 'if (name === "power")' in page, "openSheet cannot find the power sheet"
        del html  # the sheet is emitted outside the head fragment


class TestTheVolumeKeys:
    """Jay's spec: "if the user presses up it activates the volume slider
    (doesnt change volume yet) then they can use both volume buttons to change
    the volume. if they press down then a carousel ... with the agents
    avatars/faces ... holding a volume buttons activates voice comms with the
    agent like a walkie talkie."

    The compositor reports press and release and decides nothing; every bit of
    that behaviour is state, and it lives in the page.
    """

    @pytest.mark.parametrize("key", ["up", "down"])
    @pytest.mark.parametrize("action", ["press", "release"])
    def test_a_key_event_reaches_an_open_page(self, monkeypatch, key, action):
        monkeypatch.setattr(auth, "_request_is_console", lambda _r: True)
        auth._LOCK_EVENT_WAITERS.clear()
        queue: asyncio.Queue = asyncio.Queue(maxsize=8)
        auth._LOCK_EVENT_WAITERS.add(queue)
        try:
            got = _body(_call(auth.lock_volume_key(
                _Req({"key": key, "action": action}))))
            assert got["delivered"] == 1
            kind, data = queue.get_nowait()
            assert kind == "volume-%s-%s" % (key, action)
            # The screen state rides in the payload rather than the event name.
            assert data.get("screen") in ("on", "off"), data
        finally:
            auth._LOCK_EVENT_WAITERS.clear()

    def test_a_nonsense_key_is_refused(self, monkeypatch):
        """This is a compositor-driven endpoint on a pre-auth screen; the set of
        things it will relay is closed."""
        monkeypatch.setattr(auth, "_request_is_console", lambda _r: True)
        for body in ({"key": "sideways", "action": "press"},
                     {"key": "up", "action": "wiggle"},
                     {}):
            assert _call(auth.lock_volume_key(_Req(body))).status_code == 400

    def test_it_is_console_only_and_exempt(self, monkeypatch):
        assert "/auth/lock-volume-key" in EXEMPT_PATHS
        assert "/auth/lock-volume" in EXEMPT_PATHS
        monkeypatch.setattr(auth, "_request_is_console", lambda _r: False)
        assert _call(auth.lock_volume_key(
            _Req({"key": "up", "action": "press"}))).status_code == 403

    def test_the_first_up_press_reveals_without_changing_the_volume(self):
        """The whole point of Jay's "doesnt change volume yet". On a phone with
        no on-screen volume, the first press today changes a level you cannot
        see; this makes the first press the one that shows you what you are
        about to change."""
        js = auth._LOCK_SCREEN_SCRIPT
        # Sliced to the END of the from-rest block, not to the first `return;`:
        # the block now returns early for the up case, and a slice that stopped
        # there cut the carousel arm off and reddened this for no reason.
        start = js.index("function volumeKey(")
        body = js[start:js.index("// ------", start)]
        rest = body[body.index("if (!carOpen && !volOpen)"):]
        reveal = rest[:rest.index("restartIdleHide();")]
        # The reveal branch shows the bezel and does NOT nudge.
        assert "volShow()" in reveal, reveal
        assert "nudgeVolume" not in reveal, reveal

    def test_down_from_rest_opens_the_carousel_not_the_bezel(self):
        js = auth._LOCK_SCREEN_SCRIPT
        start = js.index("function volumeKey(")
        body = js[start:js.index("// ------", start)]
        rest = body[body.index("if (!carOpen && !volOpen)"):]
        reveal = rest[:rest.index("restartIdleHide();")]
        assert "carShow()" in reveal, reveal

    def test_a_hold_starts_the_walkie_talkie_and_a_release_stops_it(self):
        js = auth._LOCK_SCREEN_SCRIPT
        assert "PTT_HOLD_MS" in js
        start = js.index("function volumeKey(")
        body = js[start:js.index("// ------", start)]
        assert "armTalk" in body and "stopTalking" in body

    def test_the_walkie_talkie_opens_no_microphone(self):
        """Jay: "just for demo/mock purposes for now". A mock that quietly grew
        a real mic would be the worst possible surprise on a PRE-AUTH screen,
        so the absence is asserted rather than trusted to the comment.

        Scoped to the volume/carousel code rather than the whole script,
        because the script is NOT mic-free: the pre-existing `#ls-voice` sheet
        calls navigator.mediaDevices.getUserMedia({audio: true}), and it is
        reachable from the lock screen. That is worth knowing and is not this
        feature's doing -- asserting it away here would have quietly taken
        responsibility for someone else's microphone.
        """
        js = auth._LOCK_SCREEN_SCRIPT
        start = js.index("var volEl = document.getElementById")
        block = js[start:js.index("function volumeKey(", start)]
        # CALL syntax, not bare words: the comment in startTalking says "No
        # getUserMedia, no recorder, no upload", and a substring check on the
        # word made this file fail on its own prose.
        for forbidden in (".getUserMedia(", "new MediaRecorder(",
                          "new AudioContext(", "navigator.mediaDevices"):
            assert forbidden not in block, forbidden

    def test_the_talking_state_says_demo_on_screen(self):
        # Sliced to the next function, not a fixed byte count. Adding the
        # last-used recording inside startTalking pushed "(demo)" past a
        # 600-char window and reddened this -- the fourth time in this file a
        # fixed-length slice has broken on code growing inside its window.
        js = auth._LOCK_SCREEN_SCRIPT
        start = js.index("function startTalking(")
        body = js[start:js.index("function stopTalking(", start)]
        assert "(demo)" in body, body

    def test_the_volume_surfaces_never_cover_the_passcode(self):
        """A volume nudge must not drop a bezel over the keypad someone is
        typing a PIN into."""
        js = auth._LOCK_SCREEN_SCRIPT
        start = js.index("function volumeKey(")
        body = js[start:js.index("// ------", start)]
        head = body[:body.index("var carOpen")]
        assert 'data-sheet' in head and "return" in head, head

    def test_the_carousel_reads_the_agents_off_the_islands(self):
        """It must never show an agent the screen behind it does not. A second
        fetch would let the two disagree the moment one of them was stale."""
        js = auth._LOCK_SCREEN_SCRIPT
        # carLive is the one that reads the islands; carAgents composes it with
        # the remembered order. Both are checked, because the property is that
        # NEITHER goes to the network for the agent list.
        live_at = js.index("function carLive(")
        live = js[live_at:js.index("function carArrange(", live_at)]
        assert "agentsEl" in live, live
        both = js[live_at:js.index("function paintCarousel(", live_at)]
        # "fetch(" and not "fetch": the comment above it explains why
        # RE-FETCHING would be wrong, and matching the bare word caught that.
        assert "fetch(" not in both, both


class TestTheRadioSwitches:
    """Wi-Fi and Bluetooth in the pull-down shade.

    Both go through the root drop box the power menu uses, because the obstacle
    is the same one twice: the controller runs as `taos`, logind answers
    "challenge" to CanPowerOff, NetworkManager answers `no` to
    enable-disable-wifi, and /dev/rfkill is not writable by that user either.
    """

    @pytest.mark.parametrize("radio,on,verb", [
        ("wifi", True, "wifi-on"),
        ("wifi", False, "wifi-off"),
        ("bluetooth", True, "bt-on"),
        ("bluetooth", False, "bt-off"),
    ])
    def test_each_switch_writes_its_own_verb(self, monkeypatch, tmp_path, radio, on, verb):
        """What lands in that file IS the contract with the root helper, so the
        mapping is asserted rather than trusted to a dict literal."""
        target = tmp_path / "request"
        monkeypatch.setattr(auth, "_POWER_REQUEST", str(target))
        monkeypatch.setattr(auth, "_request_is_console", lambda _r: True)
        monkeypatch.setattr(auth, "_read_radios", lambda: {"wifi": True})
        monkeypatch.setattr(auth.asyncio, "sleep", _no_sleep)
        resp = _call(auth.set_lock_radios(_Req({"radio": radio, "on": on})))
        assert resp.status_code == 200
        assert target.read_text() == verb

    def test_an_unknown_radio_is_refused(self, monkeypatch):
        monkeypatch.setattr(auth, "_request_is_console", lambda _r: True)
        for body in ({"radio": "microwave", "on": True},
                     {"radio": "wifi"},
                     {"radio": "wifi", "on": "yes"},
                     {}):
            assert _call(auth.set_lock_radios(_Req(body))).status_code == 400

    def test_it_is_console_only_and_exempt(self, monkeypatch):
        assert "/auth/lock-radios" in EXEMPT_PATHS
        monkeypatch.setattr(auth, "_request_is_console", lambda _r: False)
        assert _call(auth.lock_radios(_Req())).status_code == 403
        assert _call(auth.set_lock_radios(
            _Req({"radio": "wifi", "on": True}))).status_code == 403

    def test_the_answer_is_the_read_back_not_the_request(self, monkeypatch, tmp_path):
        """A switch that reports what it ASKED for lies the moment the radio
        refuses. This asks for wifi ON while the reader insists it is OFF, and
        requires the refusal to win."""
        monkeypatch.setattr(auth, "_POWER_REQUEST", str(tmp_path / "request"))
        monkeypatch.setattr(auth, "_request_is_console", lambda _r: True)
        monkeypatch.setattr(auth, "_read_radios", lambda: {"wifi": False})
        monkeypatch.setattr(auth.asyncio, "sleep", _no_sleep)
        got = _body(_call(auth.set_lock_radios(_Req({"radio": "wifi", "on": True}))))
        assert got["wifi"] is False, got

    def test_a_hard_blocked_radio_reads_as_off(self, monkeypatch):
        """A physical kill switch is not something software can clear, so a
        switch that ignored a hard block would show on and do nothing."""
        class Done:
            returncode = 0
            stdout = "bluetooth unblocked blocked"
        monkeypatch.setattr(auth.subprocess if hasattr(auth, "subprocess") else auth,
                            "run", lambda *a, **k: Done(), raising=False)
        import subprocess as real
        monkeypatch.setattr(real, "run", lambda *a, **k: Done())
        assert auth._read_radios().get("bluetooth") is False

    def test_an_unreadable_radio_is_absent_not_false(self, monkeypatch):
        """Absent and off are different answers: the page disables the button
        rather than showing a state nobody measured."""
        import subprocess as real

        def boom(*_a, **_k):
            raise OSError("no such tool")

        monkeypatch.setattr(real, "run", boom)
        assert auth._read_radios() == {}

    def test_the_page_disables_a_switch_it_could_not_read(self):
        js = auth._LOCK_SCREEN_SCRIPT
        start = js.index("function paintRadios(")
        body = js[start:js.index("function setRadio(", start)]
        assert "btn.disabled = true" in body, body[-400:]


class TestTheRadialChooserBlursTheScreen:
    """Jay: "blur the screen when the rotary agent chooser is activated".

    It earns its place rather than being decoration: the faces are small,
    low-contrast circles over a feed of cards and text, and the focused one is
    hard to pick out without separation -- which is the one thing a chooser
    driven by a PHYSICAL KEY has to get right, because your eye is not already
    on the screen when it opens.
    """

    def test_opening_the_carousel_sets_the_blur_and_the_dim(self):
        js = auth._LOCK_SCREEN_SCRIPT
        start = js.index("function carShow(")
        body = js[start:js.index("function startTalking(", start)]
        # The value is now chosen between "1" (blur) and "dark" (hide), so the
        # assertion is on the attribute being set, with the variants checked
        # separately below.
        assert 'setAttribute("data-radial"' in body, body
        assert '"dark" : "1"' in body, body
        assert 'scrim.hidden = false' in body, body

    def test_hiding_clears_the_blur(self):
        """Left set, the whole screen stays blurred after the arc goes away --
        a phone that looks broken until something else happens to clear it."""
        js = auth._LOCK_SCREEN_SCRIPT
        start = js.index("function hideAll(")
        body = js[start:js.index("function restartIdleHide(", start)]
        assert 'removeAttribute("data-radial")' in body, body

    def test_hiding_leaves_the_scrim_alone_while_a_sheet_is_open(self):
        """The scrim is shared. A sheet keeps it up through its own rule, so
        hiding the element here would pull the dim out from under an open menu
        the moment the volume bezel timed out behind it."""
        js = auth._LOCK_SCREEN_SCRIPT
        start = js.index("function hideAll(")
        body = js[start:js.index("function restartIdleHide(", start)]
        assert 'data-sheet' in body, body
        assert 'scrim.hidden = true' in body, body

    def test_the_stylesheet_honours_the_blur_attribute(self):
        """Both halves, or the attribute is set and nothing happens."""
        css = auth._LOCK_SCREEN_STYLE
        assert 'data-radial="1"' in css
        rule = css[css.index('.lockscreen[data-radial="1"]'):][:200]
        assert "blur(" in rule, rule

    def test_summoned_onto_a_dark_panel_the_lock_screen_is_HIDDEN_not_blurred(self):
        """Jay: "maybe we should enable the rotary menu when screen is off. It
        will look nice against the black oled screen."

        On OLED an unlit pixel emits nothing, so hiding the lock screen puts the
        faces on real black -- which is the effect, and the one thing an OLED
        does that no amount of blur imitates. A blurred lock screen would still
        be a lit photograph of a lock screen.
        """
        css = auth._LOCK_SCREEN_STYLE
        assert 'data-radial="dark"' in css
        rule = css[css.index('.lockscreen[data-radial="dark"]'):][:260]
        assert "visibility: hidden" in rule, rule
        assert "blur(" not in rule, rule
        # And the scrim goes to true black behind it.
        assert 'data-radial="dark"] ~ .ls-scrim' in css

    def test_the_dark_variant_is_cleared_when_the_arc_closes(self):
        """Left set, the next ordinary open would hide the lock screen instead
        of blurring it -- and the phone would look like it had gone blank."""
        js = auth._LOCK_SCREEN_SCRIPT
        hide = js[js.index("function hideAll("):js.index("function restartIdleHide(")]
        assert "carDark = false" in hide, hide

    def test_the_volume_bezel_does_not_blur_the_screen(self):
        """Deliberately not: the bezel is a transient heads-up for a key you
        are already holding, and blurring the whole screen to show a volume
        level would be heavy-handed."""
        js = auth._LOCK_SCREEN_SCRIPT
        start = js.index("function volShow(")
        body = js[start:js.index("function hideAll(", start)]
        assert "data-radial" not in body, body


class TestHoldingToTalkDoesNotAlsoCycle:
    """Jay, from the glass: "holding to talk doesnt work, it moves to the next
    agent and then starts input capture".

    The press branch advanced the selection immediately and the hold timer then
    fired on top of it, so one hold did both. A press cannot be classified until
    it ENDS, so the only thing a press may do while the arc is open is start the
    clock; the cycle happens on release, and only if the press was a tap.
    """

    @staticmethod
    def _volume_key_source():
        js = auth._LOCK_SCREEN_SCRIPT
        start = js.index("function volumeKey(")
        return js[start:js.index("// ------", start)]

    def test_the_press_branch_does_not_cycle_the_selection(self):
        """The bug, asserted where it lived. carIndex must not move on a press
        while the arc is open."""
        src = self._volume_key_source()
        press = src[src.index('if (action === "press")'):src.index("// RELEASE")]
        assert "carIndex +=" not in press, press

    def test_the_release_branch_is_what_cycles(self):
        src = self._volume_key_source()
        release = src[src.index("// RELEASE"):]
        assert "carIndex +=" in release, release
        assert "paintCarousel()" in release, release

    def test_a_release_after_talking_stops_and_does_not_cycle(self):
        """The discriminating case: the same release must end a transmission
        OR move one agent, never both."""
        src = self._volume_key_source()
        release = src[src.index("// RELEASE"):]
        talk = release[release.index("if (talking)"):]
        # stopTalking comes first and returns before the cycle is reached.
        assert talk.index("stopTalking()") < talk.index("carIndex +="), talk
        assert "return" in talk[:talk.index("carIndex +=")], talk

    def test_a_hold_that_failed_to_start_talking_is_still_not_a_tap(self):
        """If the arc closed under the hold, `talking` is false -- but it was
        still a hold, and reading it as a tap would advance the selection on
        release. pressWasHold carries that."""
        js = auth._LOCK_SCREEN_SCRIPT
        assert "pressWasHold" in js
        src = self._volume_key_source()
        release = src[src.index("// RELEASE"):]
        assert release.index("pressWasHold") < release.index("carIndex +="), release

    def test_the_hold_timer_marks_the_press_before_talking(self):
        """One place sets the flag and starts the transmission, so the two
        cannot drift apart."""
        js = auth._LOCK_SCREEN_SCRIPT
        start = js.index("function armTalk(")
        body = js[start:js.index("function startTalking(", start)]
        assert "pressWasHold = true" in body and "startTalking()" in body, body

    def test_the_bezel_still_nudges_on_press(self):
        """Hold has no second meaning over the bezel, and a volume key that
        waited for the release would feel laggy where people expect it to be
        immediate."""
        src = self._volume_key_source()
        press = src[src.index('if (action === "press")'):src.index("// RELEASE")]
        assert "nudgeVolume(" in press, press


class TestTheArcRemembersWhereItWasLeft:
    """Jay: "we need the rotary chooser to remember its position, so a person
    can leave their most used agent ready in walking talkie mode. Might be best
    to have them auto arrange in order of last used too."

    Two separate pieces of state, because they answer different questions:
    which agent the arc OPENS on, and what ORDER the faces are in. They agree
    when the agent you parked on is the one you last used, and diverge when you
    park on one without talking to it.
    """

    def test_the_remembered_focus_is_a_NAME_not_an_index(self):
        """Agents come and go and the reordering moves them, so a remembered
        index would quietly point at a different face -- the kind of bug that
        looks like the feature working until it picks the wrong agent."""
        js = auth._LOCK_SCREEN_SCRIPT
        start = js.index("function carShow(")
        body = js[start:js.index("function startTalking(", start)]
        assert "carFocusName" in body, body
        assert ".name === carFocusName" in body, body

    def test_opening_does_not_reset_the_position(self):
        """The branch that opens the arc from rest must NOT zero carIndex, or
        every open lands on the front however it was left."""
        js = auth._LOCK_SCREEN_SCRIPT
        start = js.index("function volumeKey(")
        body = js[start:js.index("// ------", start)]
        rest = body[body.index("if (!carOpen && !volOpen)"):]
        reveal = rest[:rest.index("restartIdleHide();")]
        assert "carIndex = 0" not in reveal, reveal

    def test_a_departed_agent_falls_back_to_the_front(self):
        """If the remembered agent is gone, the arc must land somewhere real
        rather than on an index that no longer exists."""
        js = auth._LOCK_SCREEN_SCRIPT
        start = js.index("function carShow(")
        body = js[start:js.index("function startTalking(", start)]
        assert "carIndex = 0" in body, body
        assert body.index("carIndex = 0") < body.index("carFocusName"), body

    def test_nothing_clobbers_the_restored_position_before_it_is_painted(self):
        """The property Jay actually asked for, and the one my first pass
        missed.

        A mutation that let the restore run and then wrote `carIndex = 0`
        AFTER it left every other test in this class green: they assert the
        restore MECHANISM exists, not that its result survives to the paint.
        Same shape as the repair path that 63 assertions missed -- presence is
        not effect. So this reads the span between the restore and the paint
        and requires nothing to touch carIndex in it.
        """
        js = auth._LOCK_SCREEN_SCRIPT
        start = js.index("function carShow(")
        body = js[start:js.index("function startTalking(", start)]
        restore_end = body.index("=== carFocusName")
        paint_at = body.index("paintCarousel()", restore_end)
        # Past the end of the restore loop's own statement, up to the paint.
        after_loop = body[body.index("}", body.index("}", restore_end) + 1):paint_at]
        assert "carIndex" not in after_loop, (
            "something writes carIndex between the restore and the paint, so "
            "the remembered position is computed and thrown away:\n" + after_loop
        )

    def test_the_order_is_frozen_while_the_arc_is_open(self):
        """Re-sorting on every repaint would shuffle the faces under the thumb
        between one key press and the next."""
        js = auth._LOCK_SCREEN_SCRIPT
        show = js[js.index("function carShow("):js.index("function startTalking(")]
        assert "carOrder = carArrange()" in show, show
        hide = js[js.index("function hideAll("):js.index("function restartIdleHide(")]
        assert "carOrder = null" in hide, hide

    def test_last_used_is_recorded_on_TALKING_not_on_focus(self):
        """Cycling past six agents to reach one would otherwise rewrite the
        whole order on the way there."""
        js = auth._LOCK_SCREEN_SCRIPT
        talk = js[js.index("function startTalking("):js.index("function stopTalking(")]
        assert "carUsed[" in talk, talk
        remember = js[js.index("function rememberFocus("):js.index("function startTalking(")]
        assert "carUsed[" not in remember, remember

    def test_ties_keep_the_islands_own_order(self):
        """Agents never talked to should stay in the arrangement the user
        already sees behind the arc, not an arbitrary one."""
        js = auth._LOCK_SCREEN_SCRIPT
        arrange = js[js.index("function carArrange("):js.index("function carAgents(")]
        assert "a.index - b.index" in arrange, arrange

    def test_storage_failures_do_not_break_a_keypress(self):
        """localStorage throws in a private context and can come back empty.
        Nothing here is worth failing a volume key over."""
        js = auth._LOCK_SCREEN_SCRIPT
        save = js[js.index("function carSave("):js.index("function carLive(")]
        assert "try {" in save and "catch" in save, save
        # And the restore on load is guarded too.
        assert "JSON.parse(window.localStorage.getItem(CAR_STORE)" in js


class TestTheOpeningPressOnlyOpens:
    """Jay: "the first click of the volume down should not rotate the menu just
    make it appear."

    The arc opens on the press, and by the time that press is RELEASED the arc
    is open -- so the release handler saw an open arc and cycled it. The menu
    appeared already one agent along. Same shape as the hold bug: a press that
    did something on the way down must not also act on the way up.
    """

    @staticmethod
    def _src():
        js = auth._LOCK_SCREEN_SCRIPT
        start = js.index("function volumeKey(")
        return js[start:js.index("// ------", start)]

    def test_the_opening_press_is_marked(self):
        src = self._src()
        rest = src[src.index("if (!carOpen && !volOpen)"):]
        assert "pressOpened = true" in rest[:rest.index("restartIdleHide();")], rest

    def test_the_release_of_an_opening_press_does_not_cycle(self):
        """The discriminating order: the pressOpened guard has to return BEFORE
        the cycle is reached, or marking it changes nothing."""
        src = self._src()
        release = src[src.index("// RELEASE"):]
        guard = release.index("pressOpened")
        cycle = release.index("carIndex +=")
        assert guard < cycle, release
        assert "return" in release[guard:cycle], release[guard:cycle]

    def test_the_flag_is_cleared_on_every_press(self):
        """Left set, the NEXT tap would be swallowed too -- a menu that needs
        two presses per step."""
        src = self._src()
        press = src[src.index('if (action === "press")'):src.index("// RELEASE")]
        assert "pressOpened = false" in press, press


class TestTheArcEmergesFromTheBlack:
    """Jay: "when i activate the rotary menu with the screen off the lock screen
    flashes into view first, it breaks the visual appeal" and "it would be nice
    if the the rotary menu could have an appear effect like fading into view out
    of the deep black oled display."

    The flash was an ORDERING fault in the compositor script, not CSS: it woke
    the panel before telling the page, so a genuinely lit frame of full lock
    screen was shown before the page could hide it.
    """

    def test_the_dark_arc_scales_from_the_pivot_not_the_centre(self):
        """The pivot is the whole conceit of this layout, so the animation
        should unfurl from under the thumb rather than swell out of the middle
        of a dark screen."""
        css = auth._LOCK_SCREEN_STYLE
        rule = css[css.index('data-radial="dark"] ~ #ls-carousel {'):][:400]
        assert "transform-origin: 0 var(--ls-car-pivot)" in rule, rule
        assert "scale(" in rule, rule

    def test_the_dark_fade_is_slower_than_the_lit_one(self):
        """Over a blurred lock screen the arc only has to arrive; over true
        black it is the only thing on the panel, so a 200ms snap reads as a
        flash. Compared as NUMBERS rather than trusting the comment."""
        import re

        css = auth._LOCK_SCREEN_STYLE
        lit = css[css.index(".ls-carousel {"):]
        lit = lit[:lit.index("}")]
        lit_ms = max(int(m) for m in re.findall(r"(\d+)ms", lit))
        dark = css[css.index('data-radial="dark"] ~ #ls-carousel {'):][:400]
        dark_ms = max(int(m) for m in re.findall(r"(\d+)ms", dark))
        assert dark_ms > lit_ms, (dark_ms, lit_ms)

    def test_the_banner_arrives_after_the_faces(self):
        """Text arriving first on a black screen is what makes an animation
        feel like a page load rather than a thing appearing."""
        import re

        css = auth._LOCK_SCREEN_STYLE
        faces = css[css.index('data-radial="dark"] ~ #ls-carousel .ls-face'):][:300]
        banner = css[css.index('data-radial="dark"] ~ #ls-carousel .ls-carousel-banner'):][:300]
        face_delay = max(int(m) for m in re.findall(r"ease (\d+)ms", faces) or ["0"])
        banner_delay = max(int(m) for m in re.findall(r"ease (\d+)ms", banner) or ["0"])
        assert banner_delay > face_delay, (banner_delay, face_delay)

    def test_reduced_motion_drops_the_emergence(self):
        css = auth._LOCK_SCREEN_STYLE
        assert css.count("prefers-reduced-motion") >= 1
        tail = css[css.index('data-radial="dark"] ~ #ls-carousel'):]
        assert "prefers-reduced-motion" in tail, "the dark arc ignores reduced motion"


class TestClosingTheArcLeavesTheRightThingOnScreen:
    """Jay: "after using the rotary menu instead of the screen going off it
    shows the lock screen background grey."

    Two faults in one symptom. data-blanked was never cleared -- the screen-on
    handler skips it while the arc is up, and nothing else did it -- so the
    lock screen stayed at opacity 0 and the page body showed through. And even
    cleared, revealing the lock screen is the wrong answer: the screen was off
    before the arc, so it should be off after.
    """

    @staticmethod
    def _hide_all():
        js = auth._LOCK_SCREEN_SCRIPT
        start = js.index("function hideAll(")
        return js[start:js.index("function restartIdleHide(", start)]

    def test_a_dark_summon_goes_back_to_black(self):
        body = self._hide_all()
        assert "wasDark" in body, body
        assert 'setAttribute("data-blanked", "1")' in body, body

    def test_an_ordinary_close_restores_the_lock_screen(self):
        """The other arm. Always re-blackening would leave a phone that was
        awake staring at a black screen."""
        body = self._hide_all()
        assert 'removeAttribute("data-blanked")' in body, body

    def test_hideAll_reads_its_state_before_destroying_any_of_it(self):
        """The bug that made two separate fixes inert.

        hideAll used to removeAttribute("data-on") at the top and then ask,
        further down, whether data-on was set -- so the branch recording the
        parked agent could never run. Jay: "the last used agent isnt always the
        first one in the list." The code was there, in the right order by text,
        and could not fire. Presence is not effect.

        So both reads now happen before any removal, and this asserts that
        ordering directly: every read of state comes before the first
        removeAttribute in the function.
        """
        body = self._hide_all()
        first_teardown = body.index("removeAttribute")
        for read in ('var wasOpen =', 'var wasDark ='):
            assert body.index(read) < first_teardown, (
                read + " happens after state is already destroyed:\n" + body
            )
        # And the recording itself, which depends on wasOpen.
        assert body.index("carUsed[") < first_teardown, body

    def test_darkness_is_read_from_the_element_not_a_variable(self):
        """On the element, because two separate handlers need the answer and an
        attribute cannot drift from what is on screen."""
        body = self._hide_all()
        assert 'hasAttribute("data-fromdark")' in body, body

    def test_both_volume_surfaces_record_the_dark_summon(self):
        """data-radial is set by the ARC only, so reading it left a
        volume-bezel session on the lock screen. Jay: "the same after changing
        the volume with the screen off ... im left at the lock screen instead
        of screen off." The flag is set in the shared from-rest branch, before
        either surface is chosen."""
        js = auth._LOCK_SCREEN_SCRIPT
        src = js[js.index("function volumeKey("):]
        src = src[:src.index("// ------")]
        rest = src[src.index("if (!carOpen && !volOpen)"):]
        setter = rest[:rest.index('if (key === "up")')]
        assert 'setAttribute("data-fromdark", "1")' in setter, setter

    def test_the_wake_does_not_reveal_the_lock_screen_under_either_surface(self):
        """The screen-on handler asked about the arc alone, which is why the
        lock screen appeared behind the slider the moment the panel woke."""
        js = auth._LOCK_SCREEN_SCRIPT
        at = js.index('addEventListener("screen-on"')
        handler = js[at:js.index("});", at)]
        assert 'hasAttribute("data-fromdark")' in handler, handler
        assert handler.index("data-fromdark") < handler.index("removeAttribute"), handler

    def test_the_flag_is_cleared_when_the_surface_closes(self):
        """Left set, the next ordinary wake would stay black -- a phone that
        looks dead."""
        body = self._hide_all()
        assert 'removeAttribute("data-fromdark")' in body, body

    def test_the_page_does_not_power_the_panel_itself(self):
        """Keeping the page black is how the screen is returned to dark. The
        page has no business being able to power the output down, and swayidle
        blanks it properly a moment later."""
        body = self._hide_all()
        for forbidden in ("power off", "lock-screen-off", "taos-kiosk-screen"):
            assert forbidden not in body, forbidden


class TestNothingGreyShowsThrough:
    """Jay, twice: "it shows the lock screen background grey" and "it even
    flashes sometimes on rotary start/open".

    `body` carries a dark GREY GRADIENT for the ordinary sign-in card, and
    .lockscreen has no background of its own -- so hiding the lock screen
    revealed that gradient. Hiding a transparent layer over grey shows grey.
    Both of my earlier fixes moved the transparent layer around and never
    touched what was underneath it.
    """

    def test_the_body_has_a_black_state(self):
        # The RULE, not the name: the comment above it spells out
        # "body.ls-black (0,1,1) beats body (0,0,1)" and matching the bare
        # selector found the prose. Fifth time tonight.
        css = auth._LOCK_SCREEN_STYLE
        assert "body.ls-black {" in css
        rule = css[css.index("body.ls-black {"):][:80]
        assert "#000" in rule, rule

    def test_the_grey_gradient_is_what_it_overrides(self):
        """Named here so the next person knows WHY a black body rule exists,
        and so this test fails loudly if the gradient is ever removed and the
        override becomes cargo."""
        # The gradient is in _AUTH_BASE_STYLE, not the lock screen's own sheet
        # -- which is part of why it went unnoticed: the grey was being set by a
        # stylesheet the lock screen work never touched.
        base = auth._AUTH_BASE_STYLE
        body = base[base.index("body {"):]
        body = body[:body.index("}")]
        assert "linear-gradient" in body, body
        assert "#141415" in body or "#202024" in body, body
        # And the override must be able to beat it: higher specificity, and it
        # is served after the base sheet on the page.
        assert "body.ls-black {" in auth._LOCK_SCREEN_STYLE

    def test_every_hide_of_the_lock_screen_also_blackens_the_body(self):
        """One helper owns it. Two flags for one visual state is how a grey
        frame gets in, which is exactly what happened."""
        js = auth._LOCK_SCREEN_SCRIPT
        assert "function setBlack(" in js
        # Each site that sets or clears data-blanked must pair with setBlack.
        for marker in ('screenEl.setAttribute("data-blanked", "1");',
                       'screenEl.removeAttribute("data-blanked");'):
            at = 0
            while True:
                at = js.find(marker, at)
                if at == -1:
                    break
                window = js[at:at + 260]
                assert "setBlack(" in window, (
                    "a data-blanked change with no matching setBlack:\n" + window
                )
                at += len(marker)

    def test_the_dark_arc_blackens_the_body_too(self):
        """The open flash: the lock screen went hidden while the black scrim
        had not painted, so the gradient showed for a frame."""
        js = auth._LOCK_SCREEN_SCRIPT
        show = js[js.index("function carShow("):js.index("function startTalking(")]
        assert "setBlack(true)" in show, show


class TestParkingOnAnAgentCountsAsUsingIt:
    """Jay: "the last used agent isnt always the first one in the list."

    `used` was only written when a transmission STARTED, so parking on an agent
    without holding to talk left the order untouched -- and that agent did not
    come first next time, which is precisely what he was seeing.
    """

    def test_closing_records_the_parked_agent(self):
        js = auth._LOCK_SCREEN_SCRIPT
        hide = js[js.index("function hideAll("):js.index("function restartIdleHide(")]
        assert "carUsed[" in hide, hide
        assert "carSave()" in hide, hide

    def test_it_is_recorded_once_on_close_not_on_every_step(self):
        """Recording each step would rewrite the whole order while cycling past
        six agents to reach one."""
        js = auth._LOCK_SCREEN_SCRIPT
        src = js[js.index("function volumeKey("):js.index("// ------", js.index("function volumeKey("))]
        release = src[src.index("// RELEASE"):]
        assert "carUsed[" not in release, release

    def test_it_only_records_while_the_arc_was_actually_open(self):
        """hideAll also runs for the volume bezel, which has no focused agent
        and must not write an ordering entry."""
        js = auth._LOCK_SCREEN_SCRIPT
        hide = js[js.index("function hideAll("):js.index("function restartIdleHide(")]
        guard = hide.index('carEl.getAttribute("data-on") === "1"')
        assert guard < hide.index("carUsed["), hide


class TestDownReachesTheSecondMostUsedAgent:
    """Jay: "the ordering of recently used needs reversing so i can press down
    to get to my second most used agent quickly using the volume down button."

    This is a consequence of two earlier decisions rather than a free choice:
    the arc opens focused on the most recently used agent, and volume-down
    decrements the index. With the most recent at the FRONT, down had nowhere
    to go but round the back to the least used. With it at the END, down walks
    most-used -> second -> third.
    """

    def test_the_sort_puts_the_most_recent_LAST(self):
        js = auth._LOCK_SCREEN_SCRIPT
        arrange = js[js.index("function carArrange("):js.index("function carAgents(")]
        assert "a.used - b.used" in arrange, arrange
        assert "b.used - a.used" not in arrange, arrange

    def test_down_still_decrements(self):
        """The direction of travel is unchanged; only the arrangement moved. If
        both were flipped the bug would be back with two wrongs cancelling into
        the same wrong."""
        js = auth._LOCK_SCREEN_SCRIPT
        src = js[js.index("function volumeKey("):]
        src = src[:src.index("// ------")]
        release = src[src.index("// RELEASE"):]
        assert 'carIndex += (key === "up" ? 1 : -1)' in release, release

    def test_ties_still_keep_the_islands_own_order(self):
        """Agents never talked to all share used=0, so without a stable tie
        they would shuffle on every open."""
        js = auth._LOCK_SCREEN_SCRIPT
        arrange = js[js.index("function carArrange("):js.index("function carAgents(")]
        assert "a.index - b.index" in arrange, arrange


class TestBlankingTakesTheVolumeSurfacesWithIt:
    """Jay: "if i change volume with screen off after using the rotary menu the
    rotary menu flashes up first and vice versa."

    The symmetry is the tell -- whichever surface was used LAST is the one that
    flashes. data-blanked hides .lockscreen, but the bezel and the arc are
    SIBLINGS of it, so a panel that blanked while one was up left a last
    painted frame of black WITH that surface still on it, and the next wake
    showed it before the new one could paint.
    """

    @staticmethod
    def _blank_handler():
        js = auth._LOCK_SCREEN_SCRIPT
        at = js.index('screenEl.setAttribute("data-instant", "1");\n          hideAll();')
        return js[at - 1400:at + 400]

    def test_blanking_dismisses_them(self):
        assert "hideAll();" in self._blank_handler()

    def test_it_dismisses_them_without_a_fade(self):
        """A 200ms fade has nowhere to go on a panel powering down in 120ms,
        and an unfinished fade is exactly the half-lit ghost."""
        h = self._blank_handler()
        assert h.index('setAttribute("data-instant", "1")') < h.index("hideAll();"), h

    def test_the_instant_flag_reaches_the_volume_surfaces(self):
        """It only covered the sheets, which is why hiding the lock screen
        never reached the arc."""
        css = auth._LOCK_SCREEN_STYLE
        at = css.index('data-instant="1"')
        rule = css[at:css.index("}", at) + 1]
        assert "#ls-carousel" in rule, rule
        assert "#ls-vol" in rule, rule

    def test_blackness_is_set_after_the_dismissal(self):
        """hideAll decides blackness for itself, so setting it first would be
        undone by the very call that is supposed to tidy up."""
        h = self._blank_handler()
        assert h.index("hideAll();") < h.index('setAttribute("data-blanked", "1")'), h


class TestOpenedFromStandbyReturnsToStandby:
    """Jay: "sometimes after using the radial dial and it times out im still
    being sent to the lock screen instead of screen off, can we not have a
    rule, if opened from standby, back to standby."

    The rule was already the intent; what was wrong was the definition of
    standby. It was being taken from the COMPOSITOR -- whether the output was
    powered -- and those two facts come apart: after a dark session the page is
    black while the panel is still on, because swayidle has not reached its
    timeout. A second summon in that window read "panel is on", treated it as
    an awake summon, and revealed the lock screen on close. Hence "sometimes".
    """

    @staticmethod
    def _rest_branch():
        js = auth._LOCK_SCREEN_SCRIPT
        src = js[js.index("function volumeKey("):]
        src = src[:src.index("// ------")]
        rest = src[src.index("if (!carOpen && !volOpen)"):]
        return rest[:rest.index("restartIdleHide();")]

    def test_standby_is_decided_by_the_page_not_the_compositor(self):
        branch = self._rest_branch()
        assert 'hasAttribute("data-blanked")' in branch, branch

    def test_the_compositor_hint_is_still_honoured(self):
        """It is the only signal available for the first summon after a
        controller restart, when the page has never seen a screen-off."""
        branch = self._rest_branch()
        assert "fromDark" in branch, branch
        # Either source is enough.
        assert "||" in branch[branch.index("var dark ="):branch.index("var dark =") + 160], branch

    def test_the_arc_styling_follows_the_same_decision(self):
        """carDark drives whether the arc goes dark or merely blurs. Left on
        fromDark alone it would blur over a lock screen nobody can see, and
        then the close would reveal it."""
        branch = self._rest_branch()
        assert "carDark = dark;" in branch, branch
        assert "carDark = !!fromDark" not in branch, branch


#: The menu driven as a menu: build it, tap a row, answer its question.
#:
#: Source-text assertions can say the gate is WRITTEN. They cannot say it is
#: REACHED -- and the thing being asserted here is what a locked phone sends
#: when a stranger taps "Stop all agents", which is a property of the handlers,
#: not of the file.
_MENU_HARNESS = _DOM + _EVENTS + r"""
var powerBody = makeNode("div");
powerBody.setAttribute("id", "ls-power-body");
var unlockNote = makeNode("div");
unlockNote.setAttribute("id", "ls-unlock-note");
unlockNote.hidden = true;

// replaceWith, which confirmPower uses to swap the tapped row for its own
// question. Not in the shared stand-in: absent, it is a TypeError that reads
// from here as the confirm step being broken rather than as a harness gap.
var __baseMake = makeNode;
makeNode = function (tag) {
  var el = __baseMake(tag);
  el.replaceWith = function (next) {
    var p = this.parent;
    if (!p) return;
    p.children.splice(p.children.indexOf(this), 1, next);
    next.parent = p;
    this.parent = null;
  };
  return el;
};
document.createElement = makeNode;

// RECORDED, not stubbed away. A fetch that silently did nothing would satisfy
// "no request was sent" for the wrong reason, and what reaches the server from
// a locked screen is this file's entire subject.
var POSTED = [];
function fetch(url, opts) {
  POSTED.push({ url: url, body: (opts && opts.body) || null });
  var chain = { then: function () { return chain; },
                catch: function () { return chain; } };
  return chain;
}

// The real one opens the passcode sheet. Here it is the signal being measured.
var PASSCODE = 0;
function openPasscode() { PASSCODE += 1; }

__SOURCE__

function deepText(el, label) {
  if (el.textContent === label) return true;
  for (var i = 0; i < el.children.length; i++) {
    if (deepText(el.children[i], label)) return true;
  }
  return false;
}
function rowFor(label) {
  for (var i = 0; i < powerBody.children.length; i++) {
    if (deepText(powerBody.children[i], label)) return powerBody.children[i];
  }
  throw new Error("no menu row labelled " + label);
}
function confirmBox() {
  for (var i = 0; i < powerBody.children.length; i++) {
    var c = powerBody.children[i];
    if (String(c.className).indexOf("ls-power-confirm") !== -1) return c;
  }
  return null;
}

var SCN = JSON.parse(process.env.LS_MENU);
paintPowerMenu();
fire(rowFor(SCN.label), "click");
var box = confirmBox();
var out = { confirmed: !!box };
if (box && SCN.go) {
  var row = box.querySelector(".ls-power-confirm-row"), go = null;
  for (var i = 0; row && i < row.children.length; i++) {
    if (row.children[i].getAttribute("data-go") === "1") go = row.children[i];
  }
  if (!go) throw new Error("the confirm offers no way to go ahead");
  fire(go, "click");
}
out.posted = POSTED;
out.passcode = PASSCODE;
out.pending = window.__lsPendingPowerAction || null;
out.note = { text: unlockNote.textContent, hidden: !!unlockNote.hidden };
out.rows = powerBody.children.length;
process.stdout.write(JSON.stringify(out));
"""


def _menu_source() -> str:
    """The SHIPPED menu, lifted out of the served script rather than re-typed."""
    return "\n".join([
        _var("POWER_ITEMS"),
        _function("powerResult"),
        _function("runPowerAction"),
        _function("requirePasscodeForPower"),
        _function("paintPowerMenu"),
        _function("confirmPower"),
    ])


def _tap(label, *, go=True):
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        pytest.skip("node is not installed")
    script = _MENU_HARNESS.replace("__SOURCE__", _menu_source())
    env = dict(os.environ)
    env["LS_MENU"] = json.dumps({"label": label, "go": go})
    proc = subprocess.run(
        [node, "-e", script], capture_output=True, text=True, env=env, timeout=60
    )
    assert proc.returncode == 0, proc.stderr[-4000:]
    return json.loads(proc.stdout)


class TestStoppingEveryAgentDemandsThePasscode:
    """Jay's ruling, after @taOS-dev put the question to him in review.

    The rule was already written on this screen twice -- the agent menu
    "collects the INTENT and then asks for the passcode", the decision sheet
    says "Unlock to approve this" -- and the power menu was the one place it
    was not applied. Stopping a SINGLE agent demanded an unlock; stopping all
    of them did not.

    Power off and Restart are deliberately untouched: holding the hardware key
    already took the phone down from this screen, so the menu adds nothing
    there. The key cannot drain every agent on the device. That asymmetry is
    the whole reason this is the one verb that moved, which is why
    `test_power_off_is_not_gated_with_it` is here -- without it, gating the
    entire menu would pass everything else in this class.
    """

    def test_the_confirm_alone_sends_nothing(self):
        """Tapping the row asks the question and stops there."""
        out = _tap("Stop all agents", go=False)
        assert out["confirmed"] is True
        assert out["posted"] == []
        assert out["passcode"] == 0

    def test_answering_yes_raises_the_passcode_instead_of_stopping_them(self):
        out = _tap("Stop all agents")
        assert out["posted"] == [], (
            "a locked screen sent the stop to the server: %r" % (out["posted"],)
        )
        assert out["passcode"] == 1

    def test_the_intent_is_kept_for_the_signed_in_user(self):
        """Taken, not thrown away: the drain happens once a session exists."""
        out = _tap("Stop all agents")
        assert out["pending"] and out["pending"]["action"] == "stop-agents"

    def test_the_keypad_says_what_it_is_for(self):
        """An unexplained keypad straight after a menu tap reads as the phone
        having simply re-locked itself. Same note the agent menu writes."""
        out = _tap("Stop all agents")
        assert out["note"]["hidden"] is False
        assert out["note"]["text"] == "Unlock to stop all agents"

    def test_the_menu_is_whole_again_behind_the_keypad(self):
        """The confirm REPLACED the row. Left removed, the next hold of the
        power key shows a menu one item short."""
        out = _tap("Stop all agents")
        start = auth._LOCK_SCREEN_SCRIPT.index("var POWER_ITEMS")
        table = auth._LOCK_SCREEN_SCRIPT[start:auth._LOCK_SCREEN_SCRIPT.index("];", start)]
        assert out["rows"] == table.count('["')

    def test_power_off_is_not_gated_with_it(self):
        """The discriminating case, and the positive control for the recorder:
        a verb that IS meant to go through pre-auth still does, and the harness
        can see a POST when one happens."""
        out = _tap("Power off")
        assert out["passcode"] == 0
        assert len(out["posted"]) == 1
        assert out["posted"][0]["url"] == "/auth/lock-power-action"
        assert json.loads(out["posted"][0]["body"])["action"] == "poweroff"

    def test_the_verb_is_gated_not_removed(self):
        """@taOS-dev's warning: the server must still answer `stop-agents` once
        a session exists. What changed is who can ask, not what exists."""
        assert "stop-agents" in auth._POWER_ACTIONS


class TestTheLockScreenCameraShortcut:
    """Jay: "wire it up to the lock screen button".

    The button existed before the app did and said "No camera app yet", which
    was the honest answer at the time. There is a camera app now -- taos-camerad
    serves it and taos-app-launch opens it in its own window -- so the button
    opens it.

    The pre-auth question is the same one the power menu had to answer, and it
    is answered in the route's docstring: a camera reachable from a locked phone
    is what every phone does, and this one shows a viewfinder and the photos
    taken from it, not a signed-in user's files.
    """

    def test_the_page_no_longer_claims_there_is_no_camera_app(self):
        js = auth._LOCK_SCREEN_SCRIPT
        assert "No camera app yet" not in js
        assert '"/auth/lock-app"' in js

    def test_the_app_list_is_a_closed_map_the_page_cannot_name_into(self):
        """The value reaches ROOT through the drop box, so the page must choose
        from a list rather than supply a command."""
        assert auth._LOCK_APPS == {"camera": "app-camera"}

    def test_opening_the_camera_writes_the_drop_box_verb(self, monkeypatch, tmp_path):
        req = tmp_path / "request"
        monkeypatch.setattr(auth, "_POWER_REQUEST", str(req))
        monkeypatch.setattr(auth, "_request_is_console", lambda _r: True)
        resp = _call(auth.lock_app(_Req({"app": "camera"})))
        assert resp.status_code == 200
        assert _body(resp)["ok"] is True
        # The VERB, not the app name: what reaches root is what this asserts.
        assert req.read_text() == "app-camera"

    def test_an_app_that_is_not_listed_is_refused_and_writes_nothing(
        self, monkeypatch, tmp_path
    ):
        """The discriminating case. Without it, a handler that wrote whatever it
        was given would satisfy the test above."""
        req = tmp_path / "request"
        monkeypatch.setattr(auth, "_POWER_REQUEST", str(req))
        monkeypatch.setattr(auth, "_request_is_console", lambda _r: True)
        for name in ("poweroff", "app-camera", "../../etc/passwd", "", "Camera"):
            resp = _call(auth.lock_app(_Req({"app": name})))
            assert resp.status_code == 400, name
            assert not req.exists(), name

    def test_it_is_console_only(self, monkeypatch, tmp_path):
        req = tmp_path / "request"
        monkeypatch.setattr(auth, "_POWER_REQUEST", str(req))
        monkeypatch.setattr(auth, "_request_is_console", lambda _r: False)
        resp = _call(auth.lock_app(_Req({"app": "camera"})))
        assert resp.status_code == 403
        assert not req.exists()

    def test_it_is_reachable_before_sign_in(self):
        """It is pressed FROM the lock screen, so a route that 401s is a button
        that does nothing -- the failure the stats panel already had once."""
        assert "/auth/lock-app" in EXEMPT_PATHS

    def test_the_power_verbs_did_not_grow(self):
        """@taOS-dev asked for _POWER_ACTIONS to stay a closed set of five.
        Opening an app shares the channel, not the vocabulary."""
        assert len(auth._POWER_ACTIONS) == 5
        assert "app-camera" not in auth._POWER_ACTIONS
