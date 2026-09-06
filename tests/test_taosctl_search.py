"""Tests for the taosctl search command: dispatch, endpoint paths, and exit codes."""
from __future__ import annotations

import pytest

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
        return {"results": [], "query": params.get("q", "") if params else "", "total": 0}


def _run(monkeypatch, argv, fake):
    monkeypatch.setattr(cli_main, "TaosClient", lambda **k: fake)
    return cli_main.main(argv)


def test_search_list_calls_endpoint(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["search", "list", "hello"], fake)
    assert rc == 0
    assert any(c[0] == "GET" and c[1] == "/api/search" for c in fake.calls)


def test_search_list_passes_query_param(monkeypatch, capsys):
    fake = _FakeClient()
    _run(monkeypatch, ["search", "list", "hello"], fake)
    get_calls = [c for c in fake.calls if c[0] == "GET"]
    assert len(get_calls) == 1
    assert get_calls[0][2]["q"] == "hello"


def test_search_list_default_limit(monkeypatch, capsys):
    fake = _FakeClient()
    _run(monkeypatch, ["search", "list", "test"], fake)
    get_calls = [c for c in fake.calls if c[0] == "GET"]
    assert get_calls[0][2]["limit"] == 5


def test_search_list_custom_limit(monkeypatch, capsys):
    fake = _FakeClient()
    _run(monkeypatch, ["search", "list", "test", "--limit", "10"], fake)
    get_calls = [c for c in fake.calls if c[0] == "GET"]
    assert get_calls[0][2]["limit"] == 10


def test_search_list_json_output(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["--json", "search", "list", "alpha"], fake)
    assert rc == 0
    out = capsys.readouterr().out
    assert '"query"' in out


def test_search_api_error_maps_to_exit_2(monkeypatch, capsys):
    from tinyagentos.cli.taosctl.client import ApiError

    fake = _FakeClient()
    fake._raise = ApiError(500, "search backend unavailable")
    rc = _run(monkeypatch, ["search", "list", "fail"], fake)
    assert rc == 2
    assert "search backend unavailable" in capsys.readouterr().err


def test_search_transport_error_maps_to_exit_1(monkeypatch, capsys):
    from tinyagentos.cli.taosctl.client import TransportError

    fake = _FakeClient()
    fake._raise = TransportError("cannot reach http://x: refused")
    rc = _run(monkeypatch, ["search", "list", "fail"], fake)
    assert rc == 1
    assert "cannot reach" in capsys.readouterr().err
