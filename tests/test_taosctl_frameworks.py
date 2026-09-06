"""Tests for the taosctl frameworks command: dispatch, endpoint paths, and exit
codes. Mirrors the structure in tests/test_taosctl.py.
"""
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
        return {"items": [{"name": "crewai", "verified": True}]}


def _run(monkeypatch, argv, fake):
    monkeypatch.setattr(cli_main, "TaosClient", lambda **k: fake)
    return cli_main.main(argv)


def test_frameworks_list_calls_endpoint_and_succeeds(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["frameworks", "list"], fake)
    assert rc == 0
    assert ("GET", "/api/frameworks") in fake.calls
    assert "crewai" in capsys.readouterr().out


def test_frameworks_list_json_output(monkeypatch, capsys):
    import json

    fake = _FakeClient()
    rc = _run(monkeypatch, ["--json", "frameworks", "list"], fake)
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["items"][0]["name"] == "crewai"


def test_frameworks_api_error_maps_to_exit_2(monkeypatch, capsys):
    fake = _FakeClient()
    fake._raise = cli_client.ApiError(500, "internal error")
    rc = _run(monkeypatch, ["frameworks", "list"], fake)
    assert rc == 2
    assert "internal error" in capsys.readouterr().err


def test_frameworks_transport_error_maps_to_exit_1(monkeypatch, capsys):
    fake = _FakeClient()
    fake._raise = cli_client.TransportError("cannot reach http://x: refused")
    rc = _run(monkeypatch, ["frameworks", "list"], fake)
    assert rc == 1
    assert "cannot reach" in capsys.readouterr().err
