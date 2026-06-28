"""Tests for the taosctl notifications command group."""
from __future__ import annotations

import json

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
        return {"items": []}

    def post(self, path, body=None, params=None, json=None):
        self.calls.append(("POST", path, body))
        if self._raise:
            raise self._raise
        return {"ok": True}

    def request(self, method, path, params=None, body=None):
        self.calls.append((method, path, body))
        if self._raise:
            raise self._raise
        return {"ok": True}


def _run(monkeypatch, argv, fake):
    monkeypatch.setattr(cli_main, "TaosClient", lambda **k: fake)
    return cli_main.main(argv)


def test_list_calls_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["notifications", "list"], fake)
    assert rc == 0
    assert ("GET", "/api/notifications", None) in fake.calls


def test_list_unread_only_sends_param(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["notifications", "list", "--unread-only"], fake)
    assert rc == 0
    assert any(c[0] == "GET" and c[1] == "/api/notifications"
               and c[2] == {"unread_only": True} for c in fake.calls)


def test_read_calls_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["notifications", "read", "7"], fake)
    assert rc == 0
    assert ("POST", "/api/notifications/7/read", None) in fake.calls


def test_read_all_calls_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["notifications", "read-all"], fake)
    assert rc == 0
    assert ("POST", "/api/notifications/read-all", None) in fake.calls


def test_mark_all_read_calls_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["notifications", "mark-all-read"], fake)
    assert rc == 0
    assert ("POST", "/api/notifications/mark-all-read", None) in fake.calls


def test_count_calls_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["notifications", "count"], fake)
    assert rc == 0
    assert ("GET", "/api/notifications/count", None) in fake.calls


def test_prefs_calls_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["notifications", "prefs"], fake)
    assert rc == 0
    assert ("GET", "/api/notifications/prefs", None) in fake.calls


def test_set_pref_calls_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["notifications", "set-pref", "agent.error", "--muted"], fake)
    assert rc == 0
    assert ("PUT", "/api/notifications/prefs/agent.error", {"muted": True}) in fake.calls


def test_api_error_maps_to_exit_2(monkeypatch, capsys):
    fake = _FakeClient()
    fake._raise = cli_client.ApiError(404, "not found")
    rc = _run(monkeypatch, ["notifications", "read", "999"], fake)
    assert rc == 2
    assert "not found" in capsys.readouterr().err


def test_transport_error_maps_to_exit_1(monkeypatch, capsys):
    fake = _FakeClient()
    fake._raise = cli_client.TransportError("cannot reach http://x: refused")
    rc = _run(monkeypatch, ["notifications", "list"], fake)
    assert rc == 1
    assert "cannot reach" in capsys.readouterr().err
