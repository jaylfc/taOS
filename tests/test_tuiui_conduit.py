"""Tests for the tuiui apphost protocol client.

Runs against an in-test stub apphost (a Unix-socket server that speaks the
same newline-delimited externally-tagged JSON). The real tuiui binary is
not required for CI.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from collections import deque

import pytest

from tinyagentos import tuiui_conduit
from tinyagentos.tuiui_conduit import (
    Frame,
    SpawnedApp,
    TuiuiConduit,
    TuiuiConduitError,
    _extract_grid,
    _verify_connected_peer,
    default_socket_path,
)


def _line(payload: dict) -> bytes:
    """Encode one wire event the way the apphost writes it."""
    return (json.dumps(payload) + "\n").encode("utf-8")


def _read_line(sock: socket.socket, timeout: float = 5.0) -> bytes:
    """Read one newline-delimited line off a raw peer socket."""
    sock.settimeout(timeout)
    buf = b""
    while b"\n" not in buf:
        chunk = sock.recv(65536)
        if not chunk:
            raise AssertionError("peer closed before a full line arrived")
        buf += chunk
    return buf.split(b"\n", 1)[0]


# Time allowed for a thread to settle into its blocking read before the next
# event goes out, so both consumers really are parked on the socket at once.
# It shapes which reader the kernel wakes, not whether the assertions hold.
_SETTLE_SECS = 0.25


def _flat_frame(text: str, *, cols: int, rows: int) -> dict:
    """A Frame event carrying ``text`` in a flat ``cells`` grid."""
    chars = list(text.ljust(cols * rows, " "))
    return {
        "Frame": {
            "grid": {"cols": cols, "rows": rows, "cells": [{"ch": ch} for ch in chars]},
            "cursor": [0, 0],
            "flags": 0,
            "images": [],
            "image_data": [],
            "clear": False,
            "switch_to": None,
            "clipboard": None,
        }
    }


class StubApphost:
    """In-test apphost: newline-delimited externally-tagged JSON over AF_UNIX.

    The state model mirrors the real apphost: an ``AppId`` counter starts at
    1 and increments per spawn; ``Roster`` includes the stored meta blob;
    ``SetMeta`` updates the meta blob for an existing app.
    """

    def __init__(self, socket_path: str) -> None:
        self.socket_path = socket_path
        self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._listener.bind(socket_path)
        # The real apphost publishes a mode-0600 socket inside a mode-0700
        # per-user directory; bind() alone leaves it at 0777 & ~umask, which
        # the client's ownership guard rightly refuses.
        os.chmod(socket_path, 0o600)
        self._listener.listen(1)
        self._listener.settimeout(2.0)
        self._stop = threading.Event()
        self._next_app = 1
        self._apps: dict[int, dict] = {}
        self._client: socket.socket | None = None
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            self._client = conn
            conn.settimeout(2.0)
            buf = b""
            try:
                while not self._stop.is_set():
                    try:
                        chunk = conn.recv(65536)
                    except socket.timeout:
                        continue
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        if not line:
                            continue
                        msg = json.loads(line.decode("utf-8"))
                        self._handle(conn, msg)
            except (ConnectionResetError, BrokenPipeError, json.JSONDecodeError, OSError):
                return
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    def _send(self, conn: socket.socket, payload: dict) -> None:
        line = (json.dumps(payload) + "\n").encode("utf-8")
        try:
            conn.sendall(line)
        except OSError:
            pass

    def _frame(
        self,
        app: int,
        rows: int = 3,
        cols: int = 6,
        text: str = "",
        flags: int = 0,
    ) -> dict:
        chars = list(text.ljust(rows * cols, " "))
        row_cells = [
            [c for c in chars[i * cols:(i + 1) * cols]]
            for i in range(rows)
        ]
        return {
            "Frame": {
                "grid": {
                    "cols": cols,
                    "rows_list": [
                        {"cols": [{"ch": ch} for ch in row]}
                        for row in row_cells
                    ],
                },
                "cursor": [0, 0],
                "flags": flags,
                "images": [],
                "image_data": [],
                "clear": False,
                "switch_to": None,
                "clipboard": None,
            }
        }

    def _handle(self, conn: socket.socket, msg: dict) -> None:
        if "Spawn" in msg:
            spawn = msg["Spawn"]
            app_id = self._next_app
            self._next_app += 1
            self._apps[app_id] = {
                "cmd": spawn.get("cmd", ""),
                "args": list(spawn.get("args", [])),
                "pid": 10000 + app_id,
                "cols": int(spawn.get("cols", 80)),
                "rows": int(spawn.get("rows", 24)),
                "age_secs": 0,
                "alive": True,
                "meta": None,
            }
            self._send(conn, {"Spawned": {"app": app_id, "pid": self._apps[app_id]["pid"]}})
            self._send(conn, self._frame(app_id, rows=2, cols=6, text="hi"))
            return
        if "Input" in msg:
            return
        if "ListApps" in msg:
            roster = [
                {
                    "app": app_id,
                    "cmd": info["cmd"],
                    "args": info["args"],
                    "pid": info["pid"],
                    "cols": info["cols"],
                    "rows": info["rows"],
                    "age_secs": info["age_secs"],
                    "alive": info["alive"],
                    "meta": info["meta"],
                }
                for app_id, info in self._apps.items()
            ]
            self._send(conn, {"Roster": roster})
            return
        if "SetMeta" in msg:
            sm = msg["SetMeta"]
            if sm["app"] in self._apps:
                self._apps[sm["app"]]["meta"] = sm["meta"]
            return
        if "Kill" in msg:
            if msg["Kill"]["app"] in self._apps:
                self._apps[msg["Kill"]["app"]]["alive"] = False
            return
        if "Shutdown" in msg:
            self._stop.set()
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            return

    def stop(self) -> None:
        self._stop.set()
        try:
            self._listener.close()
        except OSError:
            pass
        if self._client is not None:
            try:
                self._client.close()
            except OSError:
                pass
        self._thread.join(timeout=2.0)
        try:
            os.unlink(self.socket_path)
        except OSError:
            pass


@pytest.fixture
def apphost_sock(tmp_path):
    sock_path = str(tmp_path / "apphost.sock")
    server = StubApphost(sock_path)
    try:
        yield server, sock_path
    finally:
        server.stop()


class TestDefaultSocketPath:
    def test_default_uses_xdg_runtime_dir(self, monkeypatch):
        monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
        monkeypatch.setenv("USER", "alice")
        assert default_socket_path() == "/run/user/1000/tuiui-alice/apphost.sock"

    def test_fallback_is_uid_scoped_not_user_scoped(self, monkeypatch):
        """$USER is caller-controlled env; the numeric uid is not.

        A $USER-keyed fallback path is trivially predictable by any local
        user, who can then pre-create it and receive this client's PTY
        input (CWE-377).
        """
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        monkeypatch.setenv("USER", "bob")
        path = default_socket_path()
        assert f"tuiui-{os.geteuid()}" in path
        assert "tuiui-bob" not in path


class TestSpawnRoundTrip:
    def test_spawn_returns_app_and_pid(self, apphost_sock):
        _, sock_path = apphost_sock
        with TuiuiConduit(sock_path, timeout=2.0) as c:
            result = c.spawn("sh", ["-c", "echo hi"], cols=80, rows=24)
        assert result.app == 1
        assert result.pid == 10001


class TestInputByteEncoding:
    def test_send_input_writes_integer_array_on_wire(self, apphost_sock):
        """Each byte must serialise as its integer code point, not base64.

        The apphost's ``_handle`` is wrapped so every line it parses off the
        socket (the bytes the client actually wrote) is recorded, then we
        verify the ``Input`` line carries an integer array.
        """
        server, sock_path = apphost_sock
        captured_lines: list[bytes] = []
        input_seen = threading.Event()

        original_handle = server._handle

        def _capturing_handle(conn, msg):
            captured_lines.append(json.dumps(msg).encode("utf-8"))
            if "Input" in msg:
                input_seen.set()
            original_handle(conn, msg)

        server._handle = _capturing_handle

        try:
            with TuiuiConduit(sock_path, timeout=2.0) as c:
                c.spawn("sh", [], cols=6, rows=2)
                c.send_input(1, b"hello")
                assert input_seen.wait(5.0), "apphost never parsed the Input line"
        finally:
            server._handle = original_handle

        input_lines = [ln for ln in captured_lines if b'"Input"' in ln]
        assert input_lines, f"no Input line captured: {captured_lines!r}"
        decoded = json.loads(input_lines[0].decode("utf-8"))
        assert decoded == {"Input": {"app": 1, "bytes": [104, 101, 108, 108, 111]}}
        assert all(isinstance(b, int) for b in decoded["Input"]["bytes"])


class TestFrameReconstruction:
    def test_frame_lines_reconstructs_visible_text(self, apphost_sock):
        """A Frame carrying 'hi' in a 2x6 grid must yield ['hi', '']."""
        _, sock_path = apphost_sock
        with TuiuiConduit(sock_path, timeout=2.0) as c:
            c.spawn("sh", ["-c", "echo hi"], cols=6, rows=2)
            frame = next(c.iter_frames())
        lines = TuiuiConduit.frame_lines(frame)
        assert lines == ["hi", ""]


class TestMetaRebindAcrossRestart:
    def test_rebind_finds_app_after_counter_reset(self, tmp_path):
        """Spawn under one apphost, simulate daemon restart, rebind by meta."""
        sock_a = str(tmp_path / "first.sock")
        server_a = StubApphost(sock_a)
        try:
            with TuiuiConduit(sock_a, timeout=2.0) as c:
                # Burn AppId 1 first so the pre-restart id cannot coincide
                # with the id the restarted daemon hands out.
                c.spawn("sh", ["-c", "true"], cols=80, rows=24)
                spawned = c.spawn("sh", ["-c", "echo hi"], cols=80, rows=24)
                assert spawned.app == 2
                c.set_meta(spawned.app, [{"title": "agent-shell", "app_key": "k1"}])
                # A round trip on the same connection is an ordered barrier:
                # the Roster reply cannot come back before SetMeta was applied.
                assert any(
                    e.app == spawned.app and e.meta for e in c.list_apps()
                ), "SetMeta was not applied before the restart"
        finally:
            server_a.stop()

        sock_b = str(tmp_path / "second.sock")
        server_b = StubApphost(sock_b)
        try:
            with TuiuiConduit(sock_b, timeout=2.0) as c:
                # The restarted daemon reset its counter: the same window is
                # back under AppId 1, and only the meta blob still matches.
                server_b._apps[1] = {
                    "cmd": "sh",
                    "args": ["-c", "echo hi"],
                    "pid": 99999,
                    "cols": 80,
                    "rows": 24,
                    "age_secs": 0,
                    "alive": True,
                    "meta": [{"title": "agent-shell", "app_key": "k1"}],
                }
                rebind = c.rebind_by_meta("agent-shell", app_key="k1")
            assert rebind is not None
            assert rebind.app == 1
            assert rebind.app != spawned.app, "the AppId did not actually change"
            assert rebind.meta[0]["title"] == "agent-shell"
        finally:
            server_b.stop()

    def test_rebind_returns_none_when_no_match(self, apphost_sock):
        _, sock_path = apphost_sock
        with TuiuiConduit(sock_path, timeout=2.0) as c:
            assert c.rebind_by_meta("does-not-exist") is None


class TestProtocolErrors:
    def test_not_connected_raises(self, tmp_path):
        c = TuiuiConduit(str(tmp_path / "missing.sock"), timeout=2.0)
        with pytest.raises(TuiuiConduitError):
            c.send_input(1, b"x")

    def test_list_apps_parses_roster(self, apphost_sock):
        _, sock_path = apphost_sock
        with TuiuiConduit(sock_path, timeout=2.0) as c:
            c.spawn("sh", ["-c", "echo a"], cols=80, rows=24)
            roster = c.list_apps()
        assert len(roster) == 1
        assert roster[0].app == 1
        assert roster[0].cmd == "sh"
        assert roster[0].alive is True

@pytest.fixture
def paired_conduit(tmp_path, monkeypatch):
    """A conduit wired to one end of a real ``socketpair``.

    The other end is handed to the test so it can write exactly the event
    sequence a race needs, byte for byte, with no stub scheduling in the
    way. A real mode-0600 socket file is published at the conduit's path so
    ``connect()``'s ownership guard sees production-shaped permissions.
    """
    sock_path = str(tmp_path / "paired.sock")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(sock_path)
    os.chmod(sock_path, 0o600)
    client_end, peer_end = socket.socketpair()

    def _fake_connect(path: str, timeout: float) -> socket.socket:
        client_end.settimeout(timeout)
        return client_end

    monkeypatch.setattr("tinyagentos.tuiui_conduit._socket_connect", _fake_connect)
    conduit = TuiuiConduit(sock_path, timeout=2.0)
    try:
        yield conduit, peer_end
    finally:
        conduit.close()
        for sock in (peer_end, client_end, listener):
            try:
                sock.close()
            except OSError:
                pass


class TestConcurrentFramesAndRequests:
    """The socket has two consumers; neither may eat the other's events."""

    def test_spawn_reply_survives_a_concurrent_frame_consumer(self, paired_conduit):
        """A frame consumer and a request waiter must not eat each other's events.

        Two threads are parked on the same socket -- one inside
        ``iter_frames()``, one inside ``spawn()`` -- when the apphost writes
        Frame, Spawned, Frame. Whichever blocked reader the kernel picks
        first, the reply belongs to ``spawn()`` and both frames belong to
        the iterator; no consumer may discard the other's event.
        """
        conduit, peer = paired_conduit
        conduit.connect()

        spawned: list[SpawnedApp] = []
        spawn_error: list[BaseException] = []
        consumed: list[str] = []
        consumer_error: list[BaseException] = []

        def request() -> None:
            try:
                spawned.append(conduit.spawn("sh", [], cols=6, rows=2))
            except BaseException as exc:  # noqa: BLE001 - reported to the test
                spawn_error.append(exc)

        requester = threading.Thread(target=request, daemon=True)
        requester.start()
        # Reading the request off the peer is the deterministic proof that
        # spawn() has written and is now sitting in the read path.
        _read_line(peer)
        time.sleep(_SETTLE_SECS)

        def consume() -> None:
            try:
                frames = conduit.iter_frames()
                for _ in range(2):
                    consumed.append(TuiuiConduit.frame_lines(next(frames))[0])
            except BaseException as exc:  # noqa: BLE001 - reported to the test
                consumer_error.append(exc)

        consumer = threading.Thread(target=consume, daemon=True)
        consumer.start()
        time.sleep(_SETTLE_SECS)

        peer.sendall(_line(_flat_frame("one", cols=6, rows=2)))
        time.sleep(_SETTLE_SECS)
        peer.sendall(_line({"Spawned": {"app": 1, "pid": 4242}}))
        time.sleep(_SETTLE_SECS)
        peer.sendall(_line(_flat_frame("two", cols=6, rows=2)))

        requester.join(timeout=10.0)
        consumer.join(timeout=10.0)
        assert not spawn_error, f"spawn() lost its reply: {spawn_error!r}"
        assert not consumer_error, f"frame consumer lost a frame: {consumer_error!r}"
        assert [(s.app, s.pid) for s in spawned] == [(1, 4242)]
        assert consumed == ["one", "two"]

    def test_frame_arriving_before_a_reply_is_not_discarded(self, paired_conduit):
        """A Frame that precedes the reply must still reach ``iter_frames()``."""
        conduit, peer = paired_conduit
        conduit.connect()

        def reply() -> None:
            _read_line(peer)  # the Spawn request
            peer.sendall(_line(_flat_frame("early", cols=6, rows=2)))
            peer.sendall(_line({"Spawned": {"app": 7, "pid": 99}}))

        threading.Thread(target=reply, daemon=True).start()

        spawned = conduit.spawn("sh", [], cols=6, rows=2)
        assert spawned.app == 7
        frame = next(conduit.iter_frames())
        assert TuiuiConduit.frame_lines(frame)[0] == "early"

    def test_unmatched_reply_is_kept_for_its_own_waiter(self, paired_conduit):
        """A Roster that arrives while Spawned is awaited must not be lost."""
        conduit, peer = paired_conduit
        conduit.connect()

        def reply() -> None:
            _read_line(peer)  # the Spawn request
            peer.sendall(_line({"Roster": [{"app": 3, "cmd": "sh", "pid": 1}]}))
            peer.sendall(_line({"Spawned": {"app": 3, "pid": 1}}))

        threading.Thread(target=reply, daemon=True).start()

        assert conduit.spawn("sh", []).app == 3
        # The Roster was already buffered before anyone asked for it; it must
        # still be there for list_apps() rather than dropped by spawn().
        roster = conduit.list_apps()
        assert [e.app for e in roster] == [3]


class TestSocketOwnershipGuard:
    """The client must not hand PTY input to someone else's listener."""

    def test_connect_refuses_a_socket_owned_by_another_uid(self, apphost_sock, monkeypatch):
        _, sock_path = apphost_sock
        real_uid = os.stat(sock_path).st_uid
        monkeypatch.setattr(os, "geteuid", lambda: real_uid + 1)
        with pytest.raises(TuiuiConduitError, match="owned by uid"):
            TuiuiConduit(sock_path, timeout=2.0).connect()

    def test_connect_refuses_a_group_or_world_accessible_socket(self, apphost_sock):
        _, sock_path = apphost_sock
        os.chmod(sock_path, 0o666)
        with pytest.raises(TuiuiConduitError, match="group/other access"):
            TuiuiConduit(sock_path, timeout=2.0).connect()

    def test_connect_refuses_a_world_writable_parent_directory(self, apphost_sock, tmp_path):
        _, sock_path = apphost_sock
        os.chmod(tmp_path, 0o777)
        try:
            with pytest.raises(TuiuiConduitError, match="group/other access"):
                TuiuiConduit(sock_path, timeout=2.0).connect()
        finally:
            os.chmod(tmp_path, 0o700)

    def test_connect_wraps_connection_failure(self, tmp_path):
        """A refused connection is part of the TuiuiConduitError contract."""
        dead_path = str(tmp_path / "dead.sock")
        bound = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        bound.bind(dead_path)  # bound but never listening -> ECONNREFUSED
        os.chmod(dead_path, 0o600)
        try:
            with pytest.raises(TuiuiConduitError, match="cannot connect"):
                TuiuiConduit(dead_path, timeout=2.0).connect()
        finally:
            bound.close()

    def test_connect_reports_a_missing_socket(self, tmp_path):
        with pytest.raises(TuiuiConduitError, match="cannot stat"):
            TuiuiConduit(str(tmp_path / "missing.sock"), timeout=2.0).connect()


class TestGridGeometry:
    """The grid parser must never silently lose or invent cells."""

    def test_partial_final_row_is_kept(self):
        cells, cols, rows, truncated = _extract_grid({"cells": list("abcdefg"), "cols": 6})
        assert (cols, rows) == (6, 2)
        assert truncated is True
        frame = Frame(cells=cells, cols=cols, rows=rows, truncated=truncated)
        assert TuiuiConduit.frame_lines(frame) == ["abcdef", "g"]

    def test_flat_grid_without_cols_does_not_assume_eighty(self):
        cells, cols, rows, truncated = _extract_grid({"cells": list("x" * 132), "rows": 1})
        assert (cols, rows) == (132, 1)
        assert truncated is False
        assert len(cells) == 132

    def test_flat_grid_without_any_geometry_is_one_row(self):
        cells, cols, rows, truncated = _extract_grid({"cells": list("abc")})
        assert (cols, rows) == (3, 1)
        assert truncated is False
        assert cells == ["a", "b", "c"]

    def test_short_row_is_padded_and_flagged(self):
        grid = {
            "cols": 4,
            "rows_list": [
                {"cols": [{"ch": ch} for ch in "ab"]},
                {"cols": [{"ch": ch} for ch in "cdef"]},
            ],
        }
        cells, cols, rows, truncated = _extract_grid(grid)
        assert (cols, rows) == (4, 2)
        assert truncated is True, "a short row must be reported, not rstripped away"
        assert len(cells) == 8
        frame = Frame(cells=cells, cols=cols, rows=rows, truncated=truncated)
        assert TuiuiConduit.frame_lines(frame) == ["ab", "cdef"]


class TestPeerCredentialGuard:
    """A verified pathname can be swapped; the connected peer cannot."""

    def test_peer_running_as_another_uid_is_refused(self, monkeypatch):
        ours, _theirs = socket.socketpair()
        try:
            monkeypatch.setattr(os, "geteuid", lambda: os.getuid() + 1)
            with pytest.raises(TuiuiConduitError, match="peer runs as uid"):
                _verify_connected_peer(ours, "/run/user/x/apphost.sock")
        finally:
            ours.close()
            _theirs.close()

    def test_peer_running_as_us_is_accepted(self):
        ours, theirs = socket.socketpair()
        try:
            _verify_connected_peer(ours, "/run/user/x/apphost.sock")
        finally:
            ours.close()
            theirs.close()

    def test_connect_verifies_the_peer_it_actually_reached(self, apphost_sock):
        """The guard runs on the live connection, not just on the path."""
        _, sock_path = apphost_sock
        calls: list[str] = []
        monkeypatched = pytest.MonkeyPatch()
        monkeypatched.setattr(
            "tinyagentos.tuiui_conduit._verify_connected_peer",
            lambda sock, path: calls.append(path),
        )
        try:
            with TuiuiConduit(sock_path, timeout=2.0):
                pass
        finally:
            monkeypatched.undo()
        assert calls == [sock_path]


class TestMalformedWireData:
    """Garbage on the wire is a protocol error, not an arbitrary ValueError."""

    @pytest.mark.parametrize(
        "grid",
        [
            {"cells": list("ab"), "rows": "two"},
            {"cells": list("ab"), "cols": "six"},
            {"cols": "wide", "rows_list": [{"cols": [{"ch": "a"}]}]},
        ],
    )
    def test_non_integer_geometry_raises_conduit_error(self, grid):
        with pytest.raises(TuiuiConduitError, match="not an integer"):
            _extract_grid(grid)

    def test_null_geometry_is_treated_as_absent(self):
        """An explicit JSON null means "not sent", as it does elsewhere."""
        cells, cols, rows, truncated = _extract_grid({"cells": list("ab"), "cols": None})
        assert (cols, rows) == (2, 1)
        assert truncated is False

    def test_non_integer_roster_field_raises_conduit_error(self):
        with pytest.raises(TuiuiConduitError, match="not an integer"):
            TuiuiConduit._parse_roster({"app": "one"})

    def test_a_malformed_frame_does_not_kill_the_reader(self, paired_conduit):
        """One bad frame must not take down every other in-flight consumer."""
        conduit, peer = paired_conduit
        conduit.connect()
        bad = _flat_frame("x", cols=1, rows=1)
        bad["Frame"]["grid"]["cols"] = "wide"
        peer.sendall(_line(bad))
        peer.sendall(_line({"Spawned": {"app": 5, "pid": 55}}))

        frames = conduit.iter_frames()
        with pytest.raises(TuiuiConduitError, match="not an integer"):
            next(frames)
        # The reader thread is untouched: the reply behind the bad frame is
        # still there for its waiter.
        assert conduit._wait_for_matching(TuiuiConduit._match_spawned)["Spawned"]["app"] == 5


class TestBacklogAccounting:
    """A dropped event must be counted, never silently vanish."""

    def test_dropped_replies_are_counted(self, paired_conduit):
        conduit, peer = paired_conduit
        conduit._replies = deque(maxlen=2)
        conduit.connect()
        conduit._replies = deque(maxlen=2)
        for app in range(4):
            peer.sendall(_line({"Roster": [{"app": app, "cmd": "sh", "pid": 1}]}))
        deadline = time.monotonic() + 5.0
        while conduit.dropped_replies < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert conduit.dropped_replies == 2

    def test_dropped_frames_are_counted(self, paired_conduit):
        conduit, peer = paired_conduit
        conduit.connect()
        conduit._frames = deque(maxlen=2)
        for text in ("a", "b", "c", "d"):
            peer.sendall(_line(_flat_frame(text, cols=1, rows=1)))
        deadline = time.monotonic() + 5.0
        while conduit.dropped_frames < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert conduit.dropped_frames == 2


class TestConnectIsAtomic:
    """Racing __enter__ calls must not leak a socket or a reader thread."""

    def test_concurrent_connect_opens_exactly_one_socket(self, apphost_sock):
        _, sock_path = apphost_sock
        opened: list[socket.socket] = []
        real_connect = tuiui_conduit._socket_connect

        def counting_connect(path: str, timeout: float) -> socket.socket:
            sock = real_connect(path, timeout)
            opened.append(sock)
            # Widen the window between the guard and the assignment.
            time.sleep(0.1)
            return sock

        monkeypatched = pytest.MonkeyPatch()
        monkeypatched.setattr(tuiui_conduit, "_socket_connect", counting_connect)
        conduit = TuiuiConduit(sock_path, timeout=2.0)
        try:
            threads = [threading.Thread(target=conduit.connect) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10.0)
            assert len(opened) == 1, f"connect() opened {len(opened)} sockets"
            assert threading.active_count() >= 1
        finally:
            monkeypatched.undo()
            conduit.close()


class _RecordingLock:
    """An RLock that records the order a thread takes the conduit's locks.

    Wrapping both locks lets a test assert the class's one lock order
    directly, rather than hoping a deadlock's narrow window happens to be
    hit while the suite is running.
    """

    def __init__(self, name: str, state: dict) -> None:
        self._name = name
        self._lock = threading.RLock()
        self._state = state

    def __enter__(self) -> "_RecordingLock":
        held = self._state["held"].setdefault(threading.get_ident(), [])
        if self._name == "_lock" and "_conn_lock" in held:
            self._state["violations"].append((*held, "_lock"))
        self._lock.acquire()
        held.append(self._name)
        return self

    def __exit__(self, *exc) -> None:
        self._state["held"][threading.get_ident()].pop()
        self._lock.release()


class TestLockOrder:
    """One lock order, or the conduit wedges with no timeout to rescue it."""

    def test_send_never_takes_the_request_lock_inside_the_connection_lock(
        self, apphost_sock
    ):
        """`_lock` then `_conn_lock`, never the reverse.

        `spawn()` holds `_lock` across its whole cycle and reaches
        `_conn_lock` from inside it, so a fire-and-forget caller taking
        `_conn_lock` first and then wanting `_lock` is an AB-BA deadlock:
        neither side can give way and neither acquisition has a timeout.
        """
        _, sock_path = apphost_sock
        state: dict = {"held": {}, "violations": []}
        with TuiuiConduit(sock_path, timeout=2.0) as conduit:
            conduit._lock = _RecordingLock("_lock", state)
            conduit._conn_lock = _RecordingLock("_conn_lock", state)
            conduit.spawn("sh", [], cols=6, rows=2)  # request path
            conduit.kill(1)  # fire-and-forget path
            conduit.send_input(1, b"x")
            conduit.list_apps()
        assert state["violations"] == [], (
            "a thread held _conn_lock while acquiring _lock, inverting the "
            f"class's lock order: {state['violations']}"
        )

    def test_a_fire_and_forget_call_is_not_blocked_by_a_waiting_request(
        self, paired_conduit
    ):
        """A request parked waiting for its reply must not wedge `kill()`."""
        conduit, peer = paired_conduit
        conduit.connect()

        spawn_error: list[BaseException] = []

        def request() -> None:
            try:
                conduit.spawn("sh", [], cols=6, rows=2, timeout=5.0)
            except BaseException as exc:  # noqa: BLE001 - reported to the test
                spawn_error.append(exc)

        requester = threading.Thread(target=request, daemon=True)
        requester.start()
        _read_line(peer)  # spawn() has written and is now waiting for its reply

        killer = threading.Thread(target=conduit.kill, args=(1,), daemon=True)
        killer.start()
        killer.join(timeout=5.0)
        assert not killer.is_alive(), "kill() deadlocked behind a waiting spawn()"

        peer.sendall(_line({"Spawned": {"app": 1, "pid": 2}}))
        requester.join(timeout=5.0)
        assert not spawn_error, f"spawn() failed: {spawn_error!r}"


class TestPeerCredentialsFailClosed:
    """"Cannot verify" must mean "do not connect"."""

    def test_connect_refuses_when_peer_credentials_are_unsupported(
        self, apphost_sock, monkeypatch
    ):
        _, sock_path = apphost_sock
        monkeypatch.delattr(socket, "SO_PEERCRED", raising=False)
        with pytest.raises(TuiuiConduitError, match="peer credentials are unavailable"):
            TuiuiConduit(sock_path, timeout=2.0).connect()

    def test_connect_refuses_when_peer_credentials_cannot_be_read(
        self, apphost_sock, monkeypatch
    ):
        _, sock_path = apphost_sock

        def _refuse(self, *args, **kwargs):
            raise OSError("getsockopt unavailable")

        monkeypatch.setattr(socket.socket, "getsockopt", _refuse)
        with pytest.raises(TuiuiConduitError, match="cannot read peer credentials"):
            TuiuiConduit(sock_path, timeout=2.0).connect()


class TestRequestTimeoutDesync:
    """A late reply must never be handed to the request that follows it."""

    def test_a_late_reply_cannot_satisfy_the_next_request(self, paired_conduit):
        """The wire has no req_id on replies, so a stale one is unattributable.

        `Spawned` carries only `app` and `pid`. Accepting a reply that
        arrived after its own request gave up would hand the caller an AppId
        belonging to some other spawn, which they would then send input to
        or kill.
        """
        conduit, peer = paired_conduit
        conduit.connect()

        with pytest.raises(TuiuiConduitError, match="timed out"):
            conduit.spawn("sh", [], cols=6, rows=2, timeout=0.3)
        _read_line(peer)  # the request the apphost was slow to answer

        # The apphost answers, far too late.
        peer.sendall(_line({"Spawned": {"app": 11, "pid": 111}}))
        deadline = time.monotonic() + 5.0
        while not conduit._replies and time.monotonic() < deadline:
            time.sleep(0.05)
        assert conduit._replies, "the late reply never arrived"

        with pytest.raises(TuiuiConduitError, match="desynchronised"):
            conduit.spawn("sh", [], cols=6, rows=2, timeout=1.0)

    def test_frames_still_flow_after_a_desync(self, paired_conduit):
        """Frames are unsolicited, so they are never mis-attributed."""
        conduit, peer = paired_conduit
        conduit.connect()
        with pytest.raises(TuiuiConduitError, match="timed out"):
            conduit.spawn("sh", [], cols=6, rows=2, timeout=0.3)
        peer.sendall(_line(_flat_frame("live", cols=6, rows=2)))
        assert TuiuiConduit.frame_lines(next(conduit.iter_frames()))[0] == "live"

    def test_reconnecting_clears_the_desync(self, apphost_sock):
        _, sock_path = apphost_sock
        conduit = TuiuiConduit(sock_path, timeout=2.0)
        conduit.connect()
        try:
            conduit._desynced = True
            with pytest.raises(TuiuiConduitError, match="desynchronised"):
                conduit.list_apps()
            conduit.close()
            conduit.connect()
            assert conduit.list_apps() == []
        finally:
            conduit.close()


def _peer_is_quiet(peer: socket.socket) -> bool:
    """True when the client wrote nothing more to ``peer``."""
    peer.setblocking(False)
    try:
        return not peer.recv(65536)
    except BlockingIOError:
        return True
    finally:
        peer.setblocking(True)


class TestDesyncRefusesEveryCommand:
    """A desynced conduit must not put anything else on the wire."""

    def _desync(self, conduit, peer) -> None:
        with pytest.raises(TuiuiConduitError, match="timed out"):
            conduit.spawn("sh", [], cols=6, rows=2, timeout=0.3)
        _read_line(peer)  # the request the apphost never answered

    def test_a_refused_spawn_never_reaches_the_apphost(self, paired_conduit):
        """Raising after the write would leave an app the caller has no id for."""
        conduit, peer = paired_conduit
        conduit.connect()
        self._desync(conduit, peer)

        with pytest.raises(TuiuiConduitError, match="desynchronised"):
            conduit.spawn("sh", [], cols=6, rows=2, timeout=1.0)
        assert _peer_is_quiet(peer), "the refused Spawn was still sent to the apphost"

    @pytest.mark.parametrize(
        "command",
        [
            lambda c: c.kill(11),
            lambda c: c.send_input(11, b"x"),
            lambda c: c.set_meta(11, [{"title": "t"}]),
            lambda c: c.shutdown(),
            lambda c: c.list_apps(timeout=1.0),
        ],
        ids=["kill", "send_input", "set_meta", "shutdown", "list_apps"],
    )
    def test_fire_and_forget_commands_are_refused_too(self, paired_conduit, command):
        """An AppId held across a desync may be stale; acting on it is the hazard."""
        conduit, peer = paired_conduit
        conduit.connect()
        self._desync(conduit, peer)

        with pytest.raises(TuiuiConduitError, match="desynchronised"):
            command(conduit)
        assert _peer_is_quiet(peer), "a refused command was still sent to the apphost"
