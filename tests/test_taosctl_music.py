"""Tests for the taosctl music command group."""
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
        if path == "/api/music":
            return {"tracks": [{"filename": "song.wav", "prompt": "lofi"}]}
        if path == "/api/music/status":
            return {"available": True, "backend": "musicgpt", "mode": "http"}
        return {}

    def post(self, path, body=None, params=None):
        self.calls.append(("POST", path))
        if self._raise:
            raise self._raise
        return {"status": "ok"}


def _run(monkeypatch, argv, fake):
    monkeypatch.setattr(cli_main, "TaosClient", lambda **k: fake)
    return cli_main.main(argv)


def test_music_list_calls_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["music", "list"], fake)
    assert rc == 0
    assert ("GET", "/api/music") in fake.calls


def test_music_status_calls_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["music", "status"], fake)
    assert rc == 0
    assert ("GET", "/api/music/status") in fake.calls


def test_music_api_error_maps_to_exit_2(monkeypatch, capsys):
    fake = _FakeClient()
    fake._raise = cli_client.ApiError(500, "backend unavailable")
    rc = _run(monkeypatch, ["music", "list"], fake)
    assert rc == 2
    assert "backend unavailable" in capsys.readouterr().err


def test_music_transport_error_maps_to_exit_1(monkeypatch, capsys):
    fake = _FakeClient()
    fake._raise = cli_client.TransportError("cannot reach http://x: refused")
    rc = _run(monkeypatch, ["music", "status"], fake)
    assert rc == 1
    assert "cannot reach" in capsys.readouterr().err
