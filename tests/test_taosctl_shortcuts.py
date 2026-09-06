"""Tests for the taosctl shortcuts command group."""
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
        return {"items": [{"idx": 0, "kind": "shell", "label": "bash", "icon": "terminal"}]}


def _run(monkeypatch, argv, fake):
    monkeypatch.setattr(cli_main, "TaosClient", lambda **k: fake)
    return cli_main.main(argv)


def test_shortcuts_list_calls_endpoint(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["shortcuts", "list", "my-agent"], fake)
    assert rc == 0
    assert ("GET", "/api/agents/my-agent/shortcuts") in fake.calls


def test_shortcuts_list_url_encodes_agent_id(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["shortcuts", "list", "a/b c"], fake)
    assert rc == 0
    assert ("GET", "/api/agents/a%2Fb%20c/shortcuts") in fake.calls


def test_shortcuts_list_outputs_data(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["shortcuts", "list", "alpha"], fake)
    assert rc == 0
    assert "bash" in capsys.readouterr().out


def test_shortcuts_api_error_maps_to_exit_2(monkeypatch, capsys):
    fake = _FakeClient()
    fake._raise = cli_client.ApiError(404, "no such agent")
    rc = _run(monkeypatch, ["shortcuts", "list", "ghost"], fake)
    assert rc == 2
    assert "no such agent" in capsys.readouterr().err


def test_shortcuts_transport_error_maps_to_exit_1(monkeypatch, capsys):
    fake = _FakeClient()
    fake._raise = cli_client.TransportError("cannot reach http://x: refused")
    rc = _run(monkeypatch, ["shortcuts", "list", "alpha"], fake)
    assert rc == 1
    assert "cannot reach" in capsys.readouterr().err
