"""tuiui apphost Unix-socket client.

The tuiui apphost is the agent-terminal surface of taOS (not a sandboxed app).
This module is the protocol client that talks to it over the apphost Unix
socket using newline-delimited externally-tagged JSON, as verified by the
spike at docs/design/taos-tuiui-spike-findings.md.

Scope (D1): protocol client only. No container plumbing, no bind-mounts, no UI.

Session identity: AppId is transient within one apphost lifetime. The
SetMeta blob is the persistent identifier across a daemon restart. Use
:meth:`TuiuiConduit.rebind_by_meta` after a reconnect to recover the new
AppId for a known meta title.
"""

from __future__ import annotations

import json
import os
import socket
import threading
from dataclasses import dataclass, field
from typing import Any, Iterator


def default_socket_path() -> str:
    """Return the default apphost socket path.

    Matches ``$XDG_RUNTIME_DIR/tuiui-$USER/apphost.sock`` per the spike
    (per-user, mode 0600 socket, 0700 directory). Falls back to
    ``/tmp/tuiui-$USER/apphost.sock`` when ``XDG_RUNTIME_DIR`` is unset.
    """
    user = os.environ.get("USER", "unknown")
    if xdg := os.environ.get("XDG_RUNTIME_DIR"):
        return os.path.join(xdg, f"tuiui-{user}", "apphost.sock")
    return os.path.join("/tmp", f"tuiui-{user}", "apphost.sock")


class TuiuiConduitError(Exception):
    """Raised when the apphost returns an error or the protocol is violated."""


@dataclass
class SpawnedApp:
    """Result of a successful :meth:`TuiuiConduit.spawn` call."""

    app: int
    pid: int


@dataclass
class RosterEntry:
    """One entry from a :meth:`TuiuiConduit.list_apps` (Roster) response."""

    app: int
    cmd: str
    args: list[str]
    pid: int
    cols: int
    rows: int
    age_secs: int
    alive: bool
    meta: list | None = None


@dataclass
class Frame:
    """A single ``Frame`` event from the apphost.

    ``cells`` is the row-major char grid extracted from the raw grid dict.
    ``cols`` is the width of each row; ``rows`` is the row count. Per the
    spike, cells are ANSI-free; reconstruct lines with
    :meth:`TuiuiConduit.frame_lines`.
    """

    cells: list[str]
    cols: int
    rows: int
    cursor: tuple[int, int] | None = None
    flags: int = 0
    images: list = field(default_factory=list)
    image_data: list = field(default_factory=list)
    clear: bool = False
    switch_to: int | None = None
    clipboard: str | None = None


def _socket_connect(path: str, timeout: float) -> socket.socket:
    """Open an AF_UNIX socket to ``path`` with the given timeout."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect(path)
    return sock


class TuiuiConduit:
    """Synchronous client for the tuiui apphost Unix socket.

    The wire format is one JSON object per line, externally tagged (the Rust
    side uses serde's externally-tagged enums). One client at a time per
    apphost instance.

    Use as a context manager or call :meth:`close` explicitly.
    """

    def __init__(
        self,
        socket_path: str | None = None,
        *,
        timeout: float = 30.0,
    ) -> None:
        self.socket_path = socket_path or default_socket_path()
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._read_buf = b""
        self._req_counter = 0
        self._lock = threading.RLock()

    def __enter__(self) -> "TuiuiConduit":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def connect(self) -> None:
        """Open the socket and prepare for JSON line exchange."""
        if self._sock is not None:
            return
        self._sock = _socket_connect(self.socket_path, self.timeout)
        self._read_buf = b""

    def close(self) -> None:
        """Close the socket if open."""
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None
                self._read_buf = b""

    def _send(self, payload: dict[str, Any]) -> None:
        """Encode ``payload`` as one JSON line and write it."""
        if self._sock is None:
            raise TuiuiConduitError("not connected")
        line = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
        with self._lock:
            self._sock.sendall(line)

    def _recv_event(self) -> dict[str, Any]:
        """Read one full newline-delimited JSON object from the socket.

        Frames are pushed by the apphost without a request, so this is the
        generic receive path. A request that expects a reply also goes through
        here because the apphost interleaves frames freely.
        """
        if self._sock is None:
            raise TuiuiConduitError("not connected")
        while True:
            if b"\n" in self._read_buf:
                line, self._read_buf = self._read_buf.split(b"\n", 1)
                if not line:
                    continue
                try:
                    return json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise TuiuiConduitError(f"bad frame: {exc!r}") from exc
            chunk = self._sock.recv(65536)
            if not chunk:
                raise TuiuiConduitError("apphost closed connection")
            self._read_buf += chunk

    def _next_req_id(self) -> int:
        self._req_counter += 1
        return self._req_counter

    def spawn(
        self,
        cmd: str,
        args: list[str] | None = None,
        *,
        cwd: str | None = None,
        cols: int = 80,
        rows: int = 24,
        req_id: int | None = None,
        timeout: float | None = None,
    ) -> SpawnedApp:
        """Spawn a PTY-backed app.

        ``cols``/``rows`` set the initial PTY size; the apphost pushes Frame
        events with the live grid as the app produces output.
        """
        payload: dict[str, Any] = {
            "Spawn": {
                "req_id": req_id if req_id is not None else self._next_req_id(),
                "cmd": cmd,
                "args": list(args) if args else [],
                "cols": cols,
                "rows": rows,
            }
        }
        if cwd is not None:
            payload["Spawn"]["cwd"] = cwd
        with self._lock:
            self._send(payload)
            evt = self._wait_for_matching(self._match_spawned, timeout=timeout)
        return SpawnedApp(app=int(evt["Spawned"]["app"]), pid=int(evt["Spawned"]["pid"]))

    @staticmethod
    def _match_spawned(evt: dict[str, Any]) -> bool:
        return "Spawned" in evt and "app" in evt["Spawned"] and "pid" in evt["Spawned"]

    def _wait_for_matching(
        self,
        predicate,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Drain incoming events until one matches ``predicate``.

        The apphost may push Frame events between a request and its reply,
        so callers cannot assume the next event is the reply.
        """
        if timeout is None:
            timeout = self.timeout
        if self._sock is None:
            raise TuiuiConduitError("not connected")
        prev_timeout = self._sock.gettimeout()
        if timeout is not None:
            self._sock.settimeout(timeout)
        try:
            while True:
                evt = self._recv_event()
                if predicate(evt):
                    return evt
        finally:
            self._sock.settimeout(prev_timeout)

    def send_input(self, app: int, data: bytes) -> None:
        """Write raw PTY bytes to ``app``.

        Per the spike, ``bytes`` serializes as an integer array (NOT base64):
        ``{"Input": {"app": 1, "bytes": [104, 101, 108, 108, 111]}}`` writes
        ``"hello"`` to the PTY.
        """
        self._send({"Input": {"app": app, "bytes": list(data)}})

    def list_apps(self) -> list[RosterEntry]:
        """Ask the apphost for a Roster and return all live apps."""
        with self._lock:
            self._send({"ListApps": {}})
            evt = self._wait_for_matching(self._match_roster)
        apps = evt.get("Roster") or evt.get("apps") or []
        return [self._parse_roster(a) for a in apps]

    @staticmethod
    def _match_roster(evt: dict[str, Any]) -> bool:
        return "Roster" in evt or "apps" in evt

    @staticmethod
    def _parse_roster(raw: dict[str, Any]) -> RosterEntry:
        return RosterEntry(
            app=int(raw["app"]),
            cmd=str(raw.get("cmd", "")),
            args=list(raw.get("args", [])),
            pid=int(raw.get("pid", 0)),
            cols=int(raw.get("cols", 0)),
            rows=int(raw.get("rows", 0)),
            age_secs=int(raw.get("age_secs", 0)),
            alive=bool(raw.get("alive", True)),
            meta=raw.get("meta"),
        )

    def kill(self, app: int) -> None:
        """Kill a single app by AppId."""
        self._send({"Kill": {"app": app}})

    def set_meta(self, app: int, meta: list) -> None:
        """Store an opaque meta blob for ``app``.

        The meta blob (typically ``[{title, rect, z, minimized, app_key}]``)
        is the persistent session identifier across daemon restarts; the
        AppId alone resets on restart.
        """
        self._send({"SetMeta": {"app": app, "meta": meta}})

    def shutdown(self) -> None:
        """Ask the apphost daemon to shut down."""
        self._send({"Shutdown": {}})

    def iter_frames(self) -> Iterator[Frame]:
        """Yield :class:`Frame` objects as the apphost pushes them.

        Mixed events (Roster replies, Spawned replies) are filtered out;
        unknown event shapes are skipped so the iterator can run alongside
        request/response traffic on the same socket.
        """
        while True:
            evt = self._recv_event()
            if "Frame" not in evt:
                continue
            yield self._parse_frame(evt["Frame"])

    @classmethod
    def _parse_frame(cls, raw: dict[str, Any]) -> Frame:
        grid = raw.get("grid") or {}
        cells, cols, rows = _extract_grid(grid)
        cursor = raw.get("cursor")
        cur_pair: tuple[int, int] | None = None
        if isinstance(cursor, (list, tuple)) and len(cursor) == 2:
            cur_pair = (int(cursor[0]), int(cursor[1]))
        return Frame(
            cells=cells,
            cols=cols,
            rows=rows,
            cursor=cur_pair,
            flags=int(raw.get("flags", 0)),
            images=list(raw.get("images", [])),
            image_data=list(raw.get("image_data", [])),
            clear=bool(raw.get("clear", False)),
            switch_to=(int(raw["switch_to"]) if raw.get("switch_to") is not None else None),
            clipboard=(str(raw["clipboard"]) if raw.get("clipboard") is not None else None),
        )

    @staticmethod
    def frame_lines(frame: Frame) -> list[str]:
        """Reconstruct visible text lines from a :class:`Frame`.

        Per the spike, every cell is ANSI-free, so the rows are read
        row-major and each row's ``cols`` chars are joined. Trailing spaces
        are stripped per line so empty rows show as ``""``.
        """
        out: list[str] = []
        width = frame.cols
        for r in range(frame.rows):
            start = r * width
            row_chars = frame.cells[start:start + width]
            out.append("".join(row_chars).rstrip())
        return out

    def rebind_by_meta(
        self,
        title: str,
        *,
        app_key: str | None = None,
    ) -> RosterEntry | None:
        """Find a live app whose meta matches ``title`` (and ``app_key``).

        Use this after a reconnect, when the AppId counter has reset on
        daemon restart: the meta blob is the persistent identifier, and the
        new AppId for the same window lives at ``entry.app``.
        """
        for entry in self.list_apps():
            meta = entry.meta or []
            for blob in meta:
                if not isinstance(blob, dict):
                    continue
                if blob.get("title") != title:
                    continue
                if app_key is not None and blob.get("app_key") != app_key:
                    continue
                return entry
        return None


def _extract_grid(grid: dict[str, Any]) -> tuple[list[str], int, int]:
    """Flatten a grid dict into a row-major list of ``ch`` strings.

    Returns ``(cells, cols, rows)``. Accepts both
    ``{"rows_list": [{"cols": [...]}]}`` and ``{"cells": [...]}`` shapes.
    When the grid only carries a flat ``cells`` list, ``cols`` is taken from
    ``grid["cols"]`` (default 80) so the row-major join in
    :meth:`Frame.frame_lines` stays deterministic.
    """
    if not grid:
        return [], 0, 0
    if isinstance(grid.get("cells"), list):
        flat = grid["cells"]
        cells = [str(c.get("ch", "") if isinstance(c, dict) else c) for c in flat]
        cols = int(grid.get("cols", 80))
        rows = int(grid.get("rows", max(1, len(cells) // max(1, cols))))
        return cells, cols, rows
    rows_raw = grid.get("rows_list") or grid.get("rows")
    if isinstance(rows_raw, list):
        cells: list[str] = []
        cols = 0
        for row in rows_raw:
            row_cells: list[str] = []
            if isinstance(row, dict):
                cols_raw = row.get("cols") or row.get("cells") or []
                for c in cols_raw:
                    row_cells.append(str(c.get("ch", "") if isinstance(c, dict) else c))
                if cols == 0:
                    cols = int(row.get("cols_len", len(row_cells)))
            elif isinstance(row, list):
                for c in row:
                    row_cells.append(str(c.get("ch", "") if isinstance(c, dict) else c))
            if cols == 0:
                cols = len(row_cells)
            cells.extend(row_cells)
        return cells, cols, len(rows_raw)
    return [], 0, 0