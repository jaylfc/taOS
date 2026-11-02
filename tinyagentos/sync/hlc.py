"""Hybrid logical clock (HLC) for taOS device account sync.

Pure, dependency-free implementation of the Kulkarni et al. algorithm
("Hybrid Logical Clocks: A New Approach to Distributed Systems") used to
order records across devices. A Timestamp is a (wall_ms, counter, node)
triple; wall_ms is milliseconds since epoch, counter disambiguates
same-millisecond events, and node breaks remaining ties.

Nothing in this module performs I/O: the caller supplies the wall clock
function, so tests can drive the clock deterministically.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable


MAX_DRIFT_MS = 60_000  # 60 seconds; a peer clock this far ahead is rejected


@dataclass(frozen=True, order=True)
class Timestamp:
    """Immutable, totally-ordered hybrid-logical timestamp.

    Field order (wall_ms, counter, node) gives the total order: ties on
    wall_ms and counter break by node id. ``pack`` renders fixed widths so
    that string order matches Timestamp order.
    """

    wall_ms: int
    counter: int
    node: str


def node_id(host: str, os_user: str) -> str:
    """Return ``f"{host}:{os_user}"``.

    Raises ValueError if either argument is empty or contains ``':'`` or
    whitespace, so the node id stays a single parseable field.
    """
    for value in (host, os_user):
        if not value or ":" in value or any(c.isspace() for c in value):
            raise ValueError(
                "host and os_user must be non-empty and free of ':' and "
                "whitespace"
            )
    return f"{host}:{os_user}"


def pack(ts: Timestamp) -> str:
    """Serialize a Timestamp to ``f"{wall_ms:015d}-{counter:04x}-{node}"``."""
    return f"{ts.wall_ms:015d}-{ts.counter:04x}-{ts.node}"


def unpack(s: str) -> Timestamp:
    """Inverse of :func:`pack`.

    Raises ValueError on any malformed input: wrong part count, non-hex or
    out-of-range counter, or a wall_ms that does not fit 15 digits.
    """
    if not isinstance(s, str):
        raise ValueError("timestamp must be a string")
    parts = s.split("-")
    if len(parts) != 3:
        raise ValueError(f"malformed timestamp: {s!r}")
    wall_ms_s, counter_s, node = parts
    if len(wall_ms_s) != 15 or not wall_ms_s.isdigit():
        raise ValueError(f"malformed wall_ms: {wall_ms_s!r}")
    if len(counter_s) != 4:
        raise ValueError(f"malformed counter: {counter_s!r}")
    try:
        counter = int(counter_s, 16)
    except ValueError:
        raise ValueError(f"malformed counter: {counter_s!r}")
    if not node:
        raise ValueError("empty node id")
    return Timestamp(int(wall_ms_s), counter, node)


class HLC:
    """Hybrid logical clock for a single node.

    The wall clock function returns milliseconds since epoch; the default
    is ``int(time.time() * 1000)``. Pass a callable for deterministic tests.
    """

    def __init__(self, node: str, clock: Callable[[], int] | None = None) -> None:
        self._node = node
        self._clock = clock if clock is not None else (lambda: int(time.time() * 1000))
        self._last = Timestamp(0, 0, node)

    def now(self) -> Timestamp:
        """Return a timestamp for a local or send event."""
        pt = self._clock()
        if pt > self._last.wall_ms:
            ts = Timestamp(pt, 0, self._node)
        else:
            ts = Timestamp(self._last.wall_ms, self._last.counter + 1, self._node)
        if ts.counter > 0xFFFF:
            raise OverflowError("HLC counter exceeded 0xFFFF")
        self._last = ts
        return ts

    def recv(self, remote: Timestamp) -> Timestamp:
        """Return a timestamp for a receive event.

        The local clock is never dragged forward by a remote timestamp:
        a remote more than ``MAX_DRIFT_MS`` ahead of the local clock is
        refused with ValueError.
        """
        pt = self._clock()
        w = max(self._last.wall_ms, remote.wall_ms, pt)
        if w == self._last.wall_ms == remote.wall_ms:
            counter = self._last.counter + 1
        elif w == self._last.wall_ms:
            counter = self._last.counter + 1
        elif w == remote.wall_ms:
            counter = remote.counter + 1
        else:
            counter = 0
        if remote.wall_ms - pt > MAX_DRIFT_MS:
            raise ValueError(
                f"remote clock {remote.wall_ms} is more than "
                f"{MAX_DRIFT_MS}ms ahead of local clock {pt}"
            )
        if counter > 0xFFFF:
            raise OverflowError("HLC counter exceeded 0xFFFF")
        ts = Timestamp(w, counter, self._node)
        self._last = ts
        return ts
