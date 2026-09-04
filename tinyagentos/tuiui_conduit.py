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

Threading: one background reader thread owns the socket and demultiplexes
every incoming event into a frame backlog and a reply backlog, so a thread
iterating frames and a thread awaiting a reply never compete for bytes and
never discard each other's events.
"""

from __future__ import annotations

import json
import os
import select
import socket
import stat
import struct
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterator

# How long the reader thread blocks in one recv() before looping. Only a
# liveness knob: it bounds how quickly close() joins the reader.
_READ_POLL_SECS = 0.5

# Replies buffered for waiters that have not asked for them yet. Replies are
# small and one-per-request; this is a leak guard, not flow control.
_REPLY_BACKLOG = 256

# struct ucred: pid, uid, gid as native ints.
_UCRED = "3i"


def default_socket_path() -> str:
    """Return the default apphost socket path.

    Matches ``$XDG_RUNTIME_DIR/tuiui-$USER/apphost.sock`` per the spike
    (per-user, mode 0600 socket, 0700 directory).

    With no ``XDG_RUNTIME_DIR`` there is no per-user runtime directory to
    use, so the fallback is keyed on the numeric uid rather than ``$USER``:
    ``$USER`` is caller-controlled environment that any local user can
    predict and pre-create a listener for (CWE-377), while the uid is not
    forgeable from the environment. Either way
    :meth:`TuiuiConduit.connect` verifies the socket's owner and mode
    before it speaks to whatever is listening there.
    """
    if xdg := os.environ.get("XDG_RUNTIME_DIR"):
        user = os.environ.get("USER", "unknown")
        return os.path.join(xdg, f"tuiui-{user}", "apphost.sock")
    return os.path.join(tempfile.gettempdir(), f"tuiui-{os.geteuid()}", "apphost.sock")


class TuiuiConduitError(Exception):
    """Raised when the apphost returns an error or the protocol is violated."""


def _as_int(value: Any, field: str) -> int:
    """Coerce one wire value to int, or report it as a protocol violation.

    Everything arriving on the socket is untrusted input. A bare
    ``int("two")`` would surface as a ValueError from whichever call the
    caller happened to make, which is neither the documented contract nor
    something a caller can reasonably catch.
    """
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise TuiuiConduitError(
            f"bad frame: {field}={value!r} is not an integer"
        ) from exc


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

    ``cells`` is the row-major char grid extracted from the raw grid dict,
    always exactly ``rows * cols`` long. ``cols`` is the width of each row;
    ``rows`` is the row count. Per the spike, cells are ANSI-free;
    reconstruct lines with :meth:`TuiuiConduit.frame_lines`.

    ``truncated`` is True when the wire grid did not match its own declared
    geometry (short rows padded with spaces, or surplus cells dropped), so
    a caller can tell missing data from genuinely blank cells instead of
    having :meth:`TuiuiConduit.frame_lines` quietly strip the gap away.
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
    truncated: bool = False


def _socket_connect(path: str, timeout: float) -> socket.socket:
    """Open an AF_UNIX socket to ``path`` with the given timeout."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect(path)
    return sock


def _verify_socket_owner(path: str) -> None:
    """Refuse an apphost socket another local user could have planted.

    The apphost publishes a mode-0600 socket inside a mode-0700 per-user
    directory. Anything looser means a second local user can substitute a
    listener of their own and then read every keystroke this client sends
    to the PTY, or forge replies back to it. Both the socket and the
    directory holding it are checked, because a socket nobody else can open
    is still replaceable if its directory is writable by others.
    """
    euid = os.geteuid()
    directory = os.path.dirname(path) or "."
    for target, label, is_expected_type in (
        (directory, "directory", stat.S_ISDIR),
        (path, "socket", stat.S_ISSOCK),
    ):
        try:
            # lstat, not stat: a symlink standing in for the socket is
            # itself the substitution this check exists to catch.
            st = os.lstat(target) if label == "socket" else os.stat(target)
        except OSError as exc:
            raise TuiuiConduitError(
                f"cannot stat apphost {label} {target}: {exc}"
            ) from exc
        if not is_expected_type(st.st_mode):
            raise TuiuiConduitError(f"apphost {label} {target} is not a {label}")
        if st.st_uid != euid:
            raise TuiuiConduitError(
                f"refusing apphost {label} {target}: owned by uid {st.st_uid}, not {euid}"
            )
        if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise TuiuiConduitError(
                f"refusing apphost {label} {target}: mode "
                f"{stat.S_IMODE(st.st_mode):04o} grants group/other access"
            )


def _verify_connected_peer(sock: socket.socket, path: str) -> None:
    """Confirm the process holding the other end really is this user.

    :func:`_verify_socket_owner` validates a *pathname*, and a pathname can
    be swapped between the check and the connect (CWE-367). SO_PEERCRED is
    read from the kernel for this established connection, so there is
    nothing left to swap: it is the identity of the process actually on the
    other end, which is what the ownership check was trying to establish.

    Fails closed. Without peer credentials the pathname checks are all
    that is left, and a pathname is exactly what an attacker can swap
    between the check and the connect, so "could not verify" has to mean
    "do not connect" rather than "connect anyway": the apphost runs on the
    same host as this client, so a platform that cannot answer the question
    is not one where the answer may be assumed.
    """
    peercred = getattr(socket, "SO_PEERCRED", None)
    if peercred is None:
        raise TuiuiConduitError(
            f"refusing apphost at {path}: peer credentials are unavailable on "
            "this platform, so the socket cannot be bound to its owner"
        )
    try:
        raw = sock.getsockopt(socket.SOL_SOCKET, peercred, struct.calcsize(_UCRED))
    except OSError as exc:
        raise TuiuiConduitError(
            f"refusing apphost at {path}: cannot read peer credentials: {exc}"
        ) from exc
    _pid, uid, _gid = struct.unpack(_UCRED, raw)
    euid = os.geteuid()
    if uid != euid:
        raise TuiuiConduitError(
            f"refusing apphost at {path}: peer runs as uid {uid}, not {euid}"
        )


class TuiuiConduit:
    """Synchronous client for the tuiui apphost Unix socket.

    The wire format is one JSON object per line, externally tagged (the Rust
    side uses serde's externally-tagged enums). One client at a time per
    apphost instance.

    A background reader thread is the only caller of ``recv()``; it sorts
    every event into a frame backlog and a reply backlog. That is what lets
    :meth:`iter_frames` run concurrently with :meth:`spawn` / :meth:`list_apps`
    without either side consuming the other's events.

    Lock order, never the reverse: ``_lock`` (one request at a time, held
    across its send-and-wait cycle) then ``_conn_lock`` (the socket itself)
    then ``_cv`` (the backlogs).

    Use as a context manager or call :meth:`close` explicitly.
    """

    def __init__(
        self,
        socket_path: str | None = None,
        *,
        timeout: float = 30.0,
        frame_backlog: int = 512,
    ) -> None:
        self.socket_path = socket_path or default_socket_path()
        self.timeout = timeout
        #: Frames dropped because no consumer kept up with ``frame_backlog``.
        self.dropped_frames = 0
        #: Replies dropped because no waiter claimed them within the backlog.
        self.dropped_replies = 0
        self._sock: socket.socket | None = None
        self._read_buf = b""
        self._req_counter = 0
        self._lock = threading.RLock()
        self._conn_lock = threading.RLock()
        self._cv = threading.Condition()
        self._frames: deque[dict[str, Any]] = deque(maxlen=max(1, frame_backlog))
        self._replies: deque[dict[str, Any]] = deque(maxlen=_REPLY_BACKLOG)
        self._reader: threading.Thread | None = None
        self._reader_error: BaseException | None = None
        self._closed = False
        self._desynced = False
        self._stopping = threading.Event()

    def __enter__(self) -> "TuiuiConduit":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def connect(self) -> None:
        """Open the socket and start the demultiplexing reader.

        The pathname is verified before connecting and the connected peer's
        uid after, so a swap between the two lookups cannot get an
        attacker's listener talking to this client. The whole sequence is
        one atomic step under ``_conn_lock``, so two threads racing through
        ``__enter__`` cannot both open a socket and leak one of them.

        Raises :class:`TuiuiConduitError` when the socket is not ours to
        talk to, or when the connection itself fails: connection failures
        are the most common error a caller sees, so they belong to the same
        exception contract as protocol errors rather than leaking raw
        ``OSError``.
        """
        with self._conn_lock:
            if self._sock is not None:
                return
            _verify_socket_owner(self.socket_path)
            try:
                sock = _socket_connect(self.socket_path, self.timeout)
            except OSError as exc:
                raise TuiuiConduitError(
                    f"cannot connect to apphost at {self.socket_path}: {exc}"
                ) from exc
            try:
                _verify_connected_peer(sock, self.socket_path)
            except BaseException:
                sock.close()
                raise
            self._read_buf = b""
            self._stopping.clear()
            with self._cv:
                self._frames.clear()
                self._replies.clear()
                self._reader_error = None
                self._closed = False
                self._desynced = False
            self._sock = sock
            self._reader = threading.Thread(
                target=self._read_loop,
                args=(sock,),
                name="tuiui-conduit-reader",
                daemon=True,
            )
            self._reader.start()

    def close(self) -> None:
        """Close the socket and stop the reader thread."""
        with self._conn_lock:
            self._stopping.set()
            sock, self._sock = self._sock, None
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    sock.close()
                except OSError:
                    pass
            reader, self._reader = self._reader, None
            if reader is not None and reader is not threading.current_thread():
                reader.join(timeout=_READ_POLL_SECS * 4)
            self._read_buf = b""
        with self._cv:
            self._closed = True
            self._cv.notify_all()

    def _send(self, payload: dict[str, Any]) -> None:
        """Encode ``payload`` as one JSON line and write it.

        ``_conn_lock`` alone guards the write: it serialises concurrent
        writers and stops a concurrent :meth:`close` pulling the fd out from
        under ``sendall``. It must not nest ``_lock`` inside it -- a request
        holds ``_lock`` across its whole send-and-wait cycle and reaches
        ``_conn_lock`` from inside it, so taking the two in the other order
        here would be an AB-BA deadlock with any fire-and-forget caller.
        The one lock order in this class is ``_lock`` then ``_conn_lock``.
        """
        line = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
        with self._conn_lock:
            sock = self._sock
            if sock is None:
                raise TuiuiConduitError("not connected")
            try:
                sock.sendall(line)
            except OSError as exc:
                raise TuiuiConduitError(f"apphost write failed: {exc}") from exc

    def _read_loop(self, sock: socket.socket) -> None:
        """Own the socket and sort every event into its consumer's backlog.

        This is the only place ``recv()`` and ``_read_buf`` are touched, so
        no two threads can ever split a JSON line between them or claim
        bytes meant for the other. Frame events go to the frame backlog,
        everything else to the reply backlog; nothing is discarded here
        except an oldest entry once a backlog is full, which
        :attr:`dropped_frames` and :attr:`dropped_replies` count.
        """
        try:
            while not self._stopping.is_set():
                evt = self._recv_event(sock)
                if evt is None:
                    continue
                with self._cv:
                    if "Frame" in evt:
                        if len(self._frames) == self._frames.maxlen:
                            self.dropped_frames += 1
                        self._frames.append(evt)
                    else:
                        if len(self._replies) == self._replies.maxlen:
                            self.dropped_replies += 1
                        self._replies.append(evt)
                    self._cv.notify_all()
        except BaseException as exc:  # noqa: BLE001 - handed to every waiter
            with self._cv:
                if not self._stopping.is_set():
                    self._reader_error = exc
                self._cv.notify_all()
        finally:
            with self._cv:
                self._closed = True
                self._cv.notify_all()

    def _recv_event(self, sock: socket.socket) -> dict[str, Any] | None:
        """Read one full newline-delimited JSON object, or None if none is ready.

        Called only from the reader thread. Frames are pushed by the apphost
        without a request, so this is the generic receive path: a request
        that expects a reply also arrives here because the apphost
        interleaves frames freely.

        Readiness is polled with ``select`` rather than a short socket
        timeout, so bounding how long the reader parks does not also bound
        how long a write may take.
        """
        while True:
            if b"\n" in self._read_buf:
                line, self._read_buf = self._read_buf.split(b"\n", 1)
                if not line:
                    continue
                try:
                    return json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise TuiuiConduitError(f"bad frame: {exc!r}") from exc
            ready, _, _ = select.select([sock], [], [], _READ_POLL_SECS)
            if not ready:
                return None
            chunk = sock.recv(65536)
            if not chunk:
                raise TuiuiConduitError("apphost closed connection")
            self._read_buf += chunk

    def _fail_if_reader_stopped(self) -> None:
        """Raise the reader's failure (or a clean close) for a waiter. Caller holds ``_cv``."""
        if self._reader_error is not None:
            raise TuiuiConduitError(
                f"apphost read failed: {self._reader_error!r}"
            ) from self._reader_error
        if self._closed:
            raise TuiuiConduitError("apphost closed connection")

    def _next_req_id(self) -> int:
        with self._lock:
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
        spawned = evt["Spawned"]
        return SpawnedApp(
            app=_as_int(spawned["app"], "Spawned.app"),
            pid=_as_int(spawned["pid"], "Spawned.pid"),
        )

    @staticmethod
    def _match_spawned(evt: dict[str, Any]) -> bool:
        return "Spawned" in evt and "app" in evt["Spawned"] and "pid" in evt["Spawned"]

    def _wait_for_matching(
        self,
        predicate,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Return the first buffered reply matching ``predicate``.

        Replies that do not match stay in the backlog for the waiter that
        does want them, and Frame events never reach this queue at all, so
        waiting for a reply can no longer destroy either.

        A timeout desynchronises the conduit. The apphost does not echo the
        request's ``req_id`` back on the reply (``Spawned`` carries only
        ``app`` and ``pid``), so once a reply is late there is no way to
        tell it apart from the next request's reply, and accepting it would
        hand the caller a stale AppId to send input to or kill. Rather than
        guess, every later request refuses until the caller reconnects.
        Frames are unsolicited and uncorrelated, so :meth:`iter_frames`
        keeps working.
        """
        if timeout is None:
            timeout = self.timeout
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cv:
            if self._desynced:
                raise TuiuiConduitError(
                    "conduit desynchronised by an earlier request timeout: a late "
                    "reply cannot be told from this one, so close() and connect() "
                    "before issuing further requests"
                )
            while True:
                for index, evt in enumerate(self._replies):
                    if predicate(evt):
                        del self._replies[index]
                        return evt
                self._fail_if_reader_stopped()
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    self._desynced = True
                    raise TuiuiConduitError("timed out waiting for an apphost reply")
                self._cv.wait(remaining)

    def send_input(self, app: int, data: bytes) -> None:
        """Write raw PTY bytes to ``app``.

        Per the spike, ``bytes`` serializes as an integer array (NOT base64):
        ``{"Input": {"app": 1, "bytes": [104, 101, 108, 108, 111]}}`` writes
        ``"hello"`` to the PTY.
        """
        self._send({"Input": {"app": app, "bytes": list(data)}})

    def list_apps(self, *, timeout: float | None = None) -> list[RosterEntry]:
        """Ask the apphost for a Roster and return all live apps."""
        with self._lock:
            self._send({"ListApps": {}})
            evt = self._wait_for_matching(self._match_roster, timeout=timeout)
        apps = evt.get("Roster") or evt.get("apps") or []
        return [self._parse_roster(a) for a in apps]

    @staticmethod
    def _match_roster(evt: dict[str, Any]) -> bool:
        return "Roster" in evt or "apps" in evt

    @staticmethod
    def _parse_roster(raw: dict[str, Any]) -> RosterEntry:
        return RosterEntry(
            app=_as_int(raw["app"], "Roster.app"),
            cmd=str(raw.get("cmd", "")),
            args=list(raw.get("args", [])),
            pid=_as_int(raw.get("pid", 0), "Roster.pid"),
            cols=_as_int(raw.get("cols", 0), "Roster.cols"),
            rows=_as_int(raw.get("rows", 0), "Roster.rows"),
            age_secs=_as_int(raw.get("age_secs", 0), "Roster.age_secs"),
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

    def iter_frames(self, *, timeout: float | None = None) -> Iterator[Frame]:
        """Yield :class:`Frame` objects as the apphost pushes them.

        Frames come from the backlog the reader thread fills, so this really
        does run alongside request/response traffic: it can neither consume
        a reply another thread is waiting on nor lose a frame that arrived
        while a request was in flight.

        Waits up to ``timeout`` seconds (default: the conduit timeout) for
        each frame and raises :class:`TuiuiConduitError` if none arrives or
        the apphost hangs up. A consumer slower than ``frame_backlog``
        loses the oldest frames; :attr:`dropped_frames` counts them.
        """
        while True:
            yield self._parse_frame(self._next_frame(timeout)["Frame"])

    def _next_frame(self, timeout: float | None) -> dict[str, Any]:
        """Pop the oldest buffered Frame event, waiting up to ``timeout``."""
        if timeout is None:
            timeout = self.timeout
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cv:
            while True:
                if self._frames:
                    return self._frames.popleft()
                self._fail_if_reader_stopped()
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise TuiuiConduitError("timed out waiting for an apphost frame")
                self._cv.wait(remaining)

    @classmethod
    def _parse_frame(cls, raw: dict[str, Any]) -> Frame:
        grid = raw.get("grid") or {}
        cells, cols, rows, truncated = _extract_grid(grid)
        cursor = raw.get("cursor")
        cur_pair: tuple[int, int] | None = None
        if isinstance(cursor, (list, tuple)) and len(cursor) == 2:
            cur_pair = (
                _as_int(cursor[0], "Frame.cursor[0]"),
                _as_int(cursor[1], "Frame.cursor[1]"),
            )
        return Frame(
            cells=cells,
            cols=cols,
            rows=rows,
            cursor=cur_pair,
            flags=_as_int(raw.get("flags", 0), "Frame.flags"),
            images=list(raw.get("images", [])),
            image_data=list(raw.get("image_data", [])),
            clear=bool(raw.get("clear", False)),
            switch_to=(
                _as_int(raw["switch_to"], "Frame.switch_to")
                if raw.get("switch_to") is not None
                else None
            ),
            clipboard=(str(raw["clipboard"]) if raw.get("clipboard") is not None else None),
            truncated=truncated,
        )

    @staticmethod
    def frame_lines(frame: Frame) -> list[str]:
        """Reconstruct visible text lines from a :class:`Frame`.

        Per the spike, every cell is ANSI-free, so the rows are read
        row-major and each row's ``cols`` chars are joined. Trailing spaces
        are stripped per line so empty rows show as ``""``. ``cells`` is
        already normalised to ``rows * cols``, so a short row cannot be
        mistaken here for a blank one -- check ``frame.truncated`` to tell
        the two apart.
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


def _cell_char(cell: Any) -> str:
    """Render one wire cell as its character."""
    return str(cell.get("ch", "") if isinstance(cell, dict) else cell)


def _ceil_div(numerator: int, denominator: int) -> int:
    return -(-numerator // denominator) if denominator > 0 else 0


def _fit(cells: list[str], cols: int, rows: int) -> tuple[list[str], int, int, bool]:
    """Normalise ``cells`` to exactly ``rows * cols`` entries.

    Returns the ``truncated`` flag so a caller can tell a grid that did not
    match its declared geometry from one that is simply blank.
    """
    want = cols * rows
    if len(cells) < want:
        return cells + [" "] * (want - len(cells)), cols, rows, True
    if len(cells) > want:
        return cells[:want], cols, rows, True
    return cells, cols, rows, False


def _extract_grid(grid: dict[str, Any]) -> tuple[list[str], int, int, bool]:
    """Flatten a grid dict into a row-major list of ``ch`` strings.

    Returns ``(cells, cols, rows, truncated)``. Accepts both
    ``{"rows_list": [{"cols": [...]}]}`` and ``{"cells": [...]}`` shapes.
    ``cells`` is always exactly ``rows * cols`` long, and ``truncated``
    reports whether the wire grid had to be padded or clipped to get there.

    Geometry is never guessed: a flat ``cells`` list with no ``cols`` is
    read as a single row rather than assumed to be 80 wide, because an
    assumed width silently garbles every other width (a 132-column grid
    would be re-flowed into nonsense with no signal at all).
    """
    if not grid:
        return [], 0, 0, False
    if isinstance(grid.get("cells"), list):
        cells = [_cell_char(c) for c in grid["cells"]]
        declared_cols = grid.get("cols")
        declared_rows = grid.get("rows")
        if declared_cols is not None:
            cols = _as_int(declared_cols, "grid.cols")
        elif declared_rows is not None and _as_int(declared_rows, "grid.rows") > 0:
            cols = _ceil_div(len(cells), _as_int(declared_rows, "grid.rows"))
        else:
            cols = len(cells)
        if cols <= 0:
            return [], 0, 0, False
        rows = (
            _as_int(declared_rows, "grid.rows")
            if declared_rows is not None
            else _ceil_div(len(cells), cols)
        )
        return _fit(cells, cols, max(0, rows))
    rows_raw = grid.get("rows_list") or grid.get("rows")
    if isinstance(rows_raw, list):
        per_row: list[list[str]] = []
        for row in rows_raw:
            if isinstance(row, dict):
                raw_cells = row.get("cols") or row.get("cells") or []
            elif isinstance(row, list):
                raw_cells = row
            else:
                raw_cells = []
            per_row.append([_cell_char(c) for c in raw_cells])
        declared_cols = grid.get("cols")
        if declared_cols is not None:
            cols = _as_int(declared_cols, "grid.cols")
        else:
            cols = max((len(r) for r in per_row), default=0)
        cells: list[str] = []
        truncated = False
        for row_cells in per_row:
            if len(row_cells) != cols:
                truncated = True
            if len(row_cells) < cols:
                row_cells = row_cells + [" "] * (cols - len(row_cells))
            elif len(row_cells) > cols:
                row_cells = row_cells[:cols]
            cells.extend(row_cells)
        return cells, cols, len(per_row), truncated
    return [], 0, 0, False
