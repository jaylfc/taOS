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

import pytest

from tinyagentos.tuiui_conduit import (
    Frame,
    TuiuiConduit,
    TuiuiConduitError,
    default_socket_path,
)


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

    def test_default_falls_back_to_tmp_when_xdg_unset(self, monkeypatch):
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        monkeypatch.setenv("USER", "bob")
        assert default_socket_path() == "/tmp/tuiui-bob/apphost.sock"


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

        original_handle = server._handle

        def _capturing_handle(conn, msg):
            captured_lines.append(json.dumps(msg).encode("utf-8"))
            original_handle(conn, msg)

        server._handle = _capturing_handle

        with TuiuiConduit(sock_path, timeout=2.0) as c:
            c.spawn("sh", [], cols=6, rows=2)
            c.send_input(1, b"hello")
            time.sleep(0.1)

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
                spawned = c.spawn("sh", ["-c", "echo hi"], cols=80, rows=24)
                c.set_meta(spawned.app, [{"title": "agent-shell", "app_key": "k1"}])
                time.sleep(0.05)
        finally:
            server_a.stop()

        sock_b = str(tmp_path / "second.sock")
        server_b = StubApphost(sock_b)
        try:
            with TuiuiConduit(sock_b, timeout=2.0) as c:
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