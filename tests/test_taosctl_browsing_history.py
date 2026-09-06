"""Tests for the taosctl browsing_history command group."""

from __future__ import annotations

import pytest

from tinyagentos.cli.taosctl import client as cli_client
from tinyagentos.cli.taosctl import __main__ as cli_main


class _FakeClient:
    def __init__(self, *a, **k):
        self.calls = []
        self.base_url = "http://x"
        self.token = "t"
        self._raise = None

    def get(self, path, params=None):
        self.calls.append(("GET", path, params))
        if self._raise:
            raise self._raise
        return {"items": [], "count": 0}

    def delete(self, path, params=None):
        self.calls.append(("DELETE", path, params))
        if self._raise:
            raise self._raise
        return {"deleted": 0}


def _run(monkeypatch, argv, fake):
    monkeypatch.setattr(cli_main, "TaosClient", lambda **k: fake)
    return cli_main.main(argv)


def test_browsing_history_list_calls_endpoint(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["browsing_history", "list"], fake)
    assert rc == 0
    assert ("GET", "/api/browsing-history", None) in fake.calls


def test_browsing_history_list_with_params(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["browsing_history", "list", "--source-type", "web", "--limit", "10"], fake)
    assert rc == 0
    assert ("GET", "/api/browsing-history", {"source_type": "web", "limit": 10}) in fake.calls


def test_browsing_history_list_explicit_default_limit(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["browsing_history", "list", "--limit", "50"], fake)
    assert rc == 0
    assert ("GET", "/api/browsing-history", {"limit": 50}) in fake.calls


def test_browsing_history_clear_calls_endpoint(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["browsing_history", "clear"], fake)
    assert rc == 0
    assert ("DELETE", "/api/browsing-history", None) in fake.calls


def test_browsing_history_clear_with_source_type(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["browsing_history", "clear", "--source-type", "web"], fake)
    assert rc == 0
    assert ("DELETE", "/api/browsing-history", {"source_type": "web"}) in fake.calls


def test_browsing_history_api_error_maps_to_exit_2(monkeypatch, capsys):
    fake = _FakeClient()
    fake._raise = cli_client.ApiError(500, "server error")
    rc = _run(monkeypatch, ["browsing_history", "list"], fake)
    assert rc == 2
    assert "server error" in capsys.readouterr().err


def test_browsing_history_transport_error_maps_to_exit_1(monkeypatch, capsys):
    fake = _FakeClient()
    fake._raise = cli_client.TransportError("cannot reach http://x: refused")
    rc = _run(monkeypatch, ["browsing_history", "list"], fake)
    assert rc == 1
    assert "cannot reach" in capsys.readouterr().err
