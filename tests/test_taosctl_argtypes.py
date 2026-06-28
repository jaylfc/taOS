"""Tests for the shared taosctl argparse validators and their wiring.

Unit-tests positive_int/nonneg_int, then confirms a representative command group
rejects non-positive --limit / negative --offset at parse time (exit 2) without
forwarding the invalid value, while valid values still dispatch.
"""
from __future__ import annotations

import argparse

import pytest

from tinyagentos.cli.taosctl import __main__ as cli_main
from tinyagentos.cli.taosctl.argtypes import nonneg_int, positive_int


def test_positive_int_accepts_positive():
    assert positive_int("3") == 3


@pytest.mark.parametrize("bad", ["0", "-1"])
def test_positive_int_rejects_non_positive(bad):
    with pytest.raises(argparse.ArgumentTypeError):
        positive_int(bad)


def test_nonneg_int_accepts_zero_and_positive():
    assert nonneg_int("0") == 0
    assert nonneg_int("5") == 5


def test_nonneg_int_rejects_negative():
    with pytest.raises(argparse.ArgumentTypeError):
        nonneg_int("-1")


class _FakeClient:
    def __init__(self, *a, **k):
        self.calls = []
        self.base_url = "http://x"
        self.token = "t"

    def get(self, path, params=None):
        self.calls.append(("GET", path, params))
        return {"ok": True}


def _run(monkeypatch, argv, fake):
    monkeypatch.setattr(cli_main, "TaosClient", lambda **k: fake)
    return cli_main.main(argv)


def test_memory_list_rejects_zero_limit(monkeypatch):
    fake = _FakeClient()
    with pytest.raises(SystemExit):
        _run(monkeypatch, ["memory", "list", "--limit", "0"], fake)
    assert fake.calls == []


def test_memory_list_rejects_negative_offset(monkeypatch):
    fake = _FakeClient()
    with pytest.raises(SystemExit):
        _run(monkeypatch, ["memory", "list", "--offset", "-1"], fake)
    assert fake.calls == []


def test_memory_list_accepts_valid_paging(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["memory", "list", "--limit", "10", "--offset", "0"], fake) == 0
    assert fake.calls and fake.calls[0][0] == "GET"
