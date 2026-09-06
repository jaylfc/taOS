"""Tests for the taosctl memory command group: dispatch, endpoint paths, and
exit codes via a fake client driven through tinyagentos.cli.taosctl.__main__."""
from __future__ import annotations

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
        return {"chunks": []}

    def post(self, path, json=None):
        self.calls.append(("POST", path))
        if self._raise:
            raise self._raise
        return {"results": []}

    def delete(self, path, params=None):
        self.calls.append(("DELETE", path))
        if self._raise:
            raise self._raise
        return {"status": "ok"}


def _run(monkeypatch, argv, fake):
    monkeypatch.setattr(cli_main, "TaosClient", lambda **k: fake)
    return cli_main.main(argv)


def test_memory_list_calls_browse(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["memory", "list"], fake)
    assert rc == 0
    assert ("GET", "/api/memory/browse") in fake.calls


def test_memory_list_with_agent(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["memory", "list", "--agent", "alpha"], fake)
    assert rc == 0
    assert ("GET", "/api/memory/browse") in fake.calls


def test_memory_search_calls_search(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["memory", "search", "hello world"], fake)
    assert rc == 0
    assert ("POST", "/api/memory/search") in fake.calls


def test_memory_search_with_mode(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["memory", "search", "q", "--mode", "semantic"], fake)
    assert rc == 0
    assert ("POST", "/api/memory/search") in fake.calls


def test_memory_collections_calls_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["memory", "collections", "alpha"], fake)
    assert rc == 0
    assert ("GET", "/api/memory/collections/alpha") in fake.calls


def test_memory_collections_url_encodes_agent(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["memory", "collections", "a/b c"], fake)
    assert rc == 0
    assert ("GET", "/api/memory/collections/a%2Fb%20c") in fake.calls


def test_memory_delete_calls_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["memory", "delete", "abc123"], fake)
    assert rc == 0
    assert ("DELETE", "/api/memory/chunk/abc123") in fake.calls


def test_memory_delete_url_encodes_hash(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["memory", "delete", "ab/c d"], fake)
    assert rc == 0
    assert ("DELETE", "/api/memory/chunk/ab%2Fc%20d") in fake.calls


def test_memory_api_error_maps_to_exit_2(monkeypatch, capsys):
    fake = _FakeClient()
    fake._raise = cli_client.ApiError(502, "qmd unavailable")
    rc = _run(monkeypatch, ["memory", "list"], fake)
    assert rc == 2
    assert "qmd unavailable" in capsys.readouterr().err


def test_memory_transport_error_maps_to_exit_1(monkeypatch, capsys):
    fake = _FakeClient()
    fake._raise = cli_client.TransportError("cannot reach http://x: refused")
    rc = _run(monkeypatch, ["memory", "list"], fake)
    assert rc == 1
    assert "cannot reach" in capsys.readouterr().err
