"""Tests for the taosctl templates command group."""
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
        self.calls.append(("GET", path))
        if self._raise:
            raise self._raise
        if path == "/api/templates":
            return {"templates": [{"id": "base", "name": "Base"}], "total": 1}
        if path == "/api/templates/stats":
            return {"total": 42, "categories": 5}
        if path == "/api/templates/sources":
            return {"sources": [{"id": "builtin"}, {"id": "awesome-openclaw"}]}
        return {"id": "base", "name": "Base", "system_prompt": "..."}


def _run(monkeypatch, argv, fake):
    monkeypatch.setattr(cli_main, "TaosClient", lambda **k: fake)
    return cli_main.main(argv)


def test_templates_list_calls_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["templates", "list"], fake)
    assert rc == 0
    assert ("GET", "/api/templates") in fake.calls


def test_templates_list_passes_query_params(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["templates", "list", "--category", "coding", "--source", "builtin", "--limit", "10", "--offset", "5"], fake)
    assert rc == 0
    assert fake.calls[0][0] == "GET"
    assert fake.calls[0][1] == "/api/templates"


def test_templates_get_calls_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["templates", "get", "base"], fake)
    assert rc == 0
    assert ("GET", "/api/templates/base") in fake.calls


def test_templates_get_url_encodes_id(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["templates", "get", "a/b c"], fake)
    assert rc == 0
    assert ("GET", "/api/templates/a%2Fb%20c") in fake.calls


def test_templates_stats_calls_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["templates", "stats"], fake)
    assert rc == 0
    assert ("GET", "/api/templates/stats") in fake.calls


def test_templates_sources_calls_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["templates", "sources"], fake)
    assert rc == 0
    assert ("GET", "/api/templates/sources") in fake.calls


def test_templates_api_error_maps_to_exit_2(monkeypatch, capsys):
    fake = _FakeClient()
    fake._raise = cli_client.ApiError(404, "no such template")
    rc = _run(monkeypatch, ["templates", "get", "ghost"], fake)
    assert rc == 2
    assert "no such template" in capsys.readouterr().err


def test_templates_transport_error_maps_to_exit_1(monkeypatch, capsys):
    fake = _FakeClient()
    fake._raise = cli_client.TransportError("cannot reach http://x: refused")
    rc = _run(monkeypatch, ["templates", "list"], fake)
    assert rc == 1
    assert "cannot reach" in capsys.readouterr().err
