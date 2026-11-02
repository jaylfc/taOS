"""Tests for the hybrid logical clock library (tinyagentos/sync/hlc.py).

A fake wall clock (a list consumed in order) drives the HLC deterministically.
"""

import pytest

from tinyagentos.sync.hlc import (
    HLC,
    MAX_DRIFT_MS,
    Timestamp,
    node_id,
    pack,
    unpack,
)


class FakeClock:
    """Wall clock that returns successive values from a list."""

    def __init__(self, values):
        self._values = list(values)
        self.calls = 0

    def __call__(self):
        value = self._values[self.calls]
        self.calls += 1
        return value


class TestNodeId:
    def test_format(self):
        assert node_id("host", "user") == "host:user"

    @pytest.mark.parametrize("host,os_user", [("", "user"), ("host", ""), ("a:b", "u"), ("h", "a:b"), ("a b", "u"), ("h", "a b")])
    def test_invalid(self, host, os_user):
        with pytest.raises(ValueError):
            node_id(host, os_user)


class TestPackUnpack:
    def test_round_trip(self):
        ts = Timestamp(1700000000000, 42, "host:user")
        assert unpack(pack(ts)) == ts

    @pytest.mark.parametrize("s", ["", "abc", "000000000000000-000g-node", "000000000000000-0000", "000000000000000-0000-", "00000000000000-0000-node"])
    def test_unpack_refuses(self, s):
        with pytest.raises(ValueError):
            unpack(s)

    def test_sorted_matches_timestamp_order(self):
        timestamps = [
            Timestamp(1000, 0, "b"),
            Timestamp(1000, 0, "a"),
            Timestamp(1000, 1, "a"),
            Timestamp(999, 0xFFFF, "z"),
            Timestamp(2000, 0, "a"),
        ]
        assert sorted(pack(t) for t in timestamps) == [
            pack(t) for t in sorted(timestamps)
        ]


class TestNow:
    def test_increasing_when_clock_goes_backwards(self):
        clock = FakeClock([1000, 1000, 900, 900])
        hlc = HLC("n", clock=clock)
        a = hlc.now()
        b = hlc.now()
        c = hlc.now()
        d = hlc.now()
        assert a < b < c < d
        # clock went backwards but the HLC still advanced
        assert c.wall_ms >= 1000
        assert d.counter > c.counter


class TestRecv:
    def test_remote_ahead_advances_local(self):
        clock = FakeClock([1000, 1000])
        hlc = HLC("n", clock=clock)
        remote = Timestamp(5000, 0, "peer")
        got = hlc.recv(remote)
        assert got > remote
        assert got.wall_ms == 5000
        next_ts = hlc.now()
        assert next_ts > got

    def test_remote_too_far_ahead_raises(self):
        clock = FakeClock([1000])
        hlc = HLC("n", clock=clock)
        remote = Timestamp(1000 + MAX_DRIFT_MS + 1, 0, "peer")
        with pytest.raises(ValueError):
            hlc.recv(remote)

    def test_counter_overflow_raises(self):
        clock = FakeClock([1000, 1000])
        hlc = HLC("n", clock=clock)
        hlc._last = Timestamp(1000, 0xFFFF, "n")
        with pytest.raises(OverflowError):
            hlc.now()
