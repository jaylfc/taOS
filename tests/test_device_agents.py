"""Device agents: a physical board on the lock screen while it is plugged in.

Jay's taOSusb demo. The contract with @taOS-dev is TAOSUSB-DEMO.md (bus
4475/4480); these are the phone half's end of it.

THE PROPERTY THAT MATTERS MOST HERE IS NOT A FEATURE. This surface renders
BEFORE sign-in and carries a command path to real hardware, so most of what
follows asserts what it REFUSES: without the flag, without the token, and for
a board that is not currently live.
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

import tinyagentos.routes.auth as auth
from tinyagentos.auth_middleware import EXEMPT_PATHS, EXEMPT_PREFIXES


class _Headers(dict):
    """Case-insensitive, like Starlette's.

    A plain dict here made every authorised call come back 401, which reads
    exactly like a broken token check -- the code asks for "authorization"
    and the test supplies "Authorization".
    """

    def get(self, key, default=None):
        for k, v in self.items():
            if k.lower() == key.lower():
                return v
        return default


class _Client:
    def __init__(self, host):
        self.host = host


class _Req:
    def __init__(self, body=None, headers=None, peer="10.0.0.5"):
        self._body = body or {}
        self.headers = _Headers(headers or {})
        # The address the heartbeat arrived FROM. The route pins the board's
        # advertised url to this, so a stub without it would let every test
        # pass against a url no real board could have sent.
        self.client = _Client(peer)
        self.app = type("App", (), {"state": type("S", (), {})()})()

    async def json(self):
        return self._body


def _call(coro):
    return asyncio.run(coro)


def _body(resp):
    return json.loads(bytes(resp.body))


@pytest.fixture(autouse=True)
def _clean_state():
    """Device state is in memory and global; a test that leaked a live board
    into the next one would make its refusals pass for the wrong reason."""
    with auth._DEVICE_LOCK:
        auth._DEVICE_AGENTS.clear()
        auth._DEVICE_THREADS.clear()
        auth._DEVICE_SEEN.clear()
    yield
    with auth._DEVICE_LOCK:
        auth._DEVICE_AGENTS.clear()
        auth._DEVICE_THREADS.clear()
        auth._DEVICE_SEEN.clear()


@pytest.fixture
def armed(monkeypatch, tmp_path):
    """Flag on, token file present, console request."""
    token = tmp_path / "pair.token"
    token.write_text("0123456789abcdef", encoding="utf-8")
    monkeypatch.setenv("TAOS_LOCK_DEMO_DEVICE_AGENTS", "1")
    monkeypatch.setenv("TAOS_DEVICE_AGENT_TOKEN_FILE", str(token))
    monkeypatch.setattr(auth, "_request_is_console", lambda _r: True)
    return {"Authorization": "Bearer 0123456789abcdef"}


def _beat(headers, **over):
    body = {"slug": "taosusb", "name": "taOSusb", "framework": "picoclaw",
            "url": "http://10.0.0.5:8787", "link": "usb"}
    body.update(over)
    return _call(auth.device_agent_heartbeat(_Req(body, headers)))


class TestItIsOffUnlessItIsTurnedOn:
    def test_the_flag_alone_governs_the_heartbeat(self, monkeypatch, tmp_path):
        """Not TAOS_LOCK_DEMO_AGENTS. Turning the SCRIPTED demo on must never
        turn a real command path to hardware on as a side effect."""
        monkeypatch.setenv("TAOS_LOCK_DEMO_AGENTS", "Demo")
        monkeypatch.delenv("TAOS_LOCK_DEMO_DEVICE_AGENTS", raising=False)
        resp = _beat({"Authorization": "Bearer x"})
        assert resp.status_code == 404

    def test_a_missing_token_file_refuses_rather_than_opens(
        self, monkeypatch, tmp_path
    ):
        """A misconfigured phone that accepted unauthenticated heartbeats
        would look exactly like a working one until someone else's board
        appeared on the screen."""
        monkeypatch.setenv("TAOS_LOCK_DEMO_DEVICE_AGENTS", "1")
        monkeypatch.setenv("TAOS_DEVICE_AGENT_TOKEN_FILE", str(tmp_path / "gone"))
        assert _beat({"Authorization": "Bearer anything"}).status_code == 401

    def test_the_wrong_token_is_refused(self, armed):
        assert _beat({"Authorization": "Bearer nope"}).status_code == 401

    def test_no_token_at_all_is_refused(self, armed):
        assert _beat({}).status_code == 401


class TestTheSlugIsRejectedNotSanitised:
    """The slug lands in a URL path AND is the key of a thread, so two boards
    that differ only in case must not quietly become one conversation."""

    @pytest.mark.parametrize("bad", [
        "taOSusb", "taos usb", "../etc/passwd", "-lead", "", "a" * 33, "taos_usb",
    ])
    def test_a_slug_outside_the_pattern_is_refused(self, armed, bad):
        assert _beat(armed, slug=bad).status_code == 400

    def test_the_agreed_slug_is_accepted(self, armed):
        assert _beat(armed).status_code == 200


class TestPresenceIsHeartbeatShaped:
    def test_a_live_board_appears_with_its_own_key_namespace(self, armed):
        """A board and a TAOS_LOCK_DEMO_AGENTS placeholder of the same name
        must never fight over one island: the lock screen reconciles by key."""
        _beat(armed)
        island = auth._device_island(auth._device_live()[0])
        assert island["key"] == "device:taosusb"
        assert island["device"] is True and island["demo"] is True

    def test_a_board_older_than_the_liveness_window_is_gone(self, armed):
        _beat(armed)
        with auth._DEVICE_LOCK:
            auth._DEVICE_AGENTS["taosusb"]["last_seen"] -= (
                auth._DEVICE_LIVENESS_SECS + 1
            )
        assert auth._device_live() == []

    def test_the_window_is_two_missed_beats(self):
        """5s beat, 8s window. Tighter and a single late beat unplugs the
        board on screen; looser and the shot list's ~15s cannot be met."""
        assert auth._DEVICE_LIVENESS_SECS == 8.0

    def test_link_is_rendered_not_inferred(self, armed):
        """The device measures it from /sys/class/udc/*/state. A board on a
        wall charger says `power`, and claiming USB for it would put the
        story back where the measurement belongs."""
        _beat(armed, link="power")
        assert "power only" in auth._device_island(auth._device_live()[0])["status"]
        _beat(armed, link="usb")
        assert auth._device_island(auth._device_live()[0])["status"] == "Online · USB"


class TestMessagesAreOrderedAndDeduped:
    def test_a_retried_delivery_does_not_print_twice(self, armed):
        _beat(armed)
        msg = {"slug": "taosusb", "id": "abc", "seq": 1, "text": "df -h"}
        first = _call(auth.device_agent_message(_Req(msg, armed)))
        second = _call(auth.device_agent_message(_Req(dict(msg), armed)))
        assert first.status_code == 200 and second.status_code == 200
        assert _body(second).get("duplicate") is True
        assert len(auth._DEVICE_THREADS["taosusb"]) == 1

    def test_a_board_that_never_heartbeat_cannot_write_a_thread(self, armed):
        resp = _call(auth.device_agent_message(
            _Req({"slug": "ghost", "id": "a", "seq": 1, "text": "hi"}, armed)
        ))
        assert resp.status_code == 404
        assert "ghost" not in auth._DEVICE_THREADS

    def test_an_oversized_message_is_truncated(self, armed):
        _beat(armed)
        _call(auth.device_agent_message(_Req(
            {"slug": "taosusb", "id": "a", "seq": 1, "text": "x" * 99999}, armed
        )))
        assert len(auth._DEVICE_THREADS["taosusb"][0]["text"]) == auth._DEVICE_MESSAGE_BYTES


class TestTheThreadIsCapped:
    """`apt upgrade` on a Zero emits thousands of lines, into a pre-auth
    screen holding them in the phone's memory."""

    def test_the_line_cap_holds_and_says_it_trimmed(self, armed):
        _beat(armed)
        for i in range(auth._DEVICE_THREAD_LINES + 40):
            _call(auth.device_agent_message(_Req(
                {"slug": "taosusb", "id": "run", "seq": i, "text": "line %d" % i},
                armed,
            )))
        thread = auth._DEVICE_THREADS["taosusb"]
        assert len(thread) <= auth._DEVICE_THREAD_LINES + 1
        assert thread[0]["text"] == auth._DEVICE_TRIM_MARKER
        # The NEWEST line survives: trimming takes the front, not the answer.
        assert thread[-1]["text"].endswith(str(auth._DEVICE_THREAD_LINES + 39))

    def test_the_byte_cap_holds(self, armed):
        _beat(armed)
        big = "y" * auth._DEVICE_MESSAGE_BYTES
        for i in range(40):
            _call(auth.device_agent_message(_Req(
                {"slug": "taosusb", "id": "run", "seq": i, "text": big}, armed
            )))
        total = sum(len(m["text"]) for m in auth._DEVICE_THREADS["taosusb"])
        assert total <= auth._DEVICE_THREAD_BYTES + len(auth._DEVICE_TRIM_MARKER)


class TestSendingToABoard:
    def test_an_unplugged_board_is_refused(self, armed):
        """Unplugged between the island being drawn and the send landing."""
        resp = _call(auth.lock_send("taosusb", _Req({"text": "health check"})))
        assert resp.status_code == 404

    def test_a_non_console_request_is_refused(self, armed, monkeypatch):
        monkeypatch.setattr(auth, "_request_is_console", lambda _r: False)
        assert _call(auth.lock_send("taosusb", _Req({"text": "hi"}))).status_code == 403

    def test_what_was_typed_is_in_the_thread_even_if_the_board_never_answers(
        self, armed
    ):
        """The sheet must show what was asked. A send that vanishes because
        the board went away reads as the phone having dropped it."""
        _beat(armed)
        resp = _call(auth.lock_send("taosusb", _Req({"text": "do a health check"})))
        # No board is really listening on that URL, so this is the 502 path.
        assert resp.status_code == 502
        thread = auth._DEVICE_THREADS["taosusb"]
        assert thread[-1]["role"] == "user"
        assert thread[-1]["text"] == "do a health check"


class TestTheRoutesAreReachableBeforeSignIn:
    def test_the_device_endpoints_are_exempt(self):
        assert "/auth/device-agent/heartbeat" in EXEMPT_PATHS
        assert "/auth/device-agent/message" in EXEMPT_PATHS

    def test_lock_send_is_exempt_WITH_its_slug(self):
        """It is `/auth/lock-send/<slug>`, so an exact-path exemption would
        only ever match a slug-less URL -- the route would 401 on the glass
        while every test that called the function directly stayed green."""
        assert "/auth/lock-send/" in EXEMPT_PREFIXES
        assert "/auth/lock-send" not in EXEMPT_PATHS


class TestTheHeartbeatUrlIsPinnedToTheCaller:
    """@taOS-dev's finding, demonstrated rather than argued (bus 4497).

    A board that could name any url made the phone POST to it WITH the bearer
    token -- an SSRF with credentials, from a surface that answers before
    anyone has signed in. The query string was the trick: appending "/chat" to
    ".../internal/admin-action?x=" lands the path inside the query.
    """

    def test_the_exact_exploit_is_refused(self, armed):
        resp = _beat(
            armed,
            url="http://127.0.0.1:9999/internal/admin-action?x=",
        )
        assert resp.status_code == 400
        assert auth._device_live() == []

    @pytest.mark.parametrize("bad", [
        "http://10.0.0.5/internal/admin?x=",     # query
        "http://10.0.0.5/some/path",             # path
        "http://10.0.0.5#frag",                  # fragment
        "https://10.0.0.5:8787",                 # scheme
        "http://user:pw@10.0.0.5:8787",          # credentials
        "http://10.0.0.9:8787",                  # a host that is not the caller
        "ftp://10.0.0.5",
        "",
    ])
    def test_anything_the_board_does_not_own_is_refused(self, armed, bad):
        assert _beat(armed, url=bad).status_code == 400

    def test_the_stored_url_is_rebuilt_from_parts(self, armed):
        """Not merely validated: REBUILT, so there is no attacker-controlled
        string left for a later path append to be smuggled into."""
        _beat(armed, url="http://10.0.0.5:8787/")
        with auth._DEVICE_LOCK:
            assert auth._DEVICE_AGENTS["taosusb"]["url"] == "http://10.0.0.5:8787"

    def test_a_board_may_name_itself(self, armed):
        """The fix must not break the real device: @taOS-dev's board reports
        the address it connects from, so this is the ordinary case."""
        assert _beat(armed, url="http://10.0.0.5:8787").status_code == 200


class TestTheRelayAndDedupAreBounded:
    def test_text_longer_than_the_board_accepts_is_refused_here(self, armed):
        """The board 400s above 2000 chars; a round trip that can only fail is
        worse than a straight answer."""
        _beat(armed)
        resp = _call(auth.lock_send("taosusb", _Req({"text": "x" * 2001})))
        assert resp.status_code == 400

    def test_the_dedup_set_does_not_grow_without_bound(self, armed):
        """A board plugged in all day would otherwise be a slow leak."""
        _beat(armed)
        for i in range(auth._DEVICE_SEEN_MAX + 50):
            _call(auth.device_agent_message(_Req(
                {"slug": "taosusb", "id": "run", "seq": i, "text": "."}, armed
            )))
        with auth._DEVICE_LOCK:
            assert len(auth._DEVICE_SEEN["taosusb"]) <= auth._DEVICE_SEEN_MAX + 1
