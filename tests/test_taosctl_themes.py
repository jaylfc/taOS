"""Tests for the taosctl themes command group."""
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
        return {"items": [{"id": "dark", "name": "Dark"}]}

    def delete(self, path, params=None):
        self.calls.append(("DELETE", path))
        if self._raise:
            raise self._raise
        return {"status": "ok"}


def _run(monkeypatch, argv, fake):
    monkeypatch.setattr(cli_main, "TaosClient", lambda **k: fake)
    return cli_main.main(argv)


def test_themes_list_calls_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["themes", "list"], fake)
    assert rc == 0
    assert ("GET", "/api/themes") in fake.calls


def test_themes_delete_calls_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["themes", "delete", "dark"], fake)
    assert rc == 0
    assert ("DELETE", "/api/themes/dark") in fake.calls


def test_themes_delete_url_encodes_id(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["--json", "themes", "delete", "x/y"], fake)
    assert rc == 0
    assert ("DELETE", "/api/themes/x%2Fy") in fake.calls


def test_themes_api_error_maps_to_exit_2(monkeypatch, capsys):
    fake = _FakeClient()
    fake._raise = cli_client.ApiError(404, "not found")
    rc = _run(monkeypatch, ["themes", "delete", "ghost"], fake)
    assert rc == 2
    assert "not found" in capsys.readouterr().err


def test_themes_transport_error_maps_to_exit_1(monkeypatch, capsys):
    fake = _FakeClient()
    fake._raise = cli_client.TransportError("cannot reach http://x: refused")
    rc = _run(monkeypatch, ["themes", "list"], fake)
    assert rc == 1
    assert "cannot reach" in capsys.readouterr().err
