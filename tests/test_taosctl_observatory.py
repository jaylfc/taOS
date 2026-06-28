"""Tests for the taosctl observatory command group."""
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
        return {"global": False, "lanes": {}}

    def post(self, path, body=None, params=None, json=None):
        self.calls.append(("POST", path, body))
        if self._raise:
            raise self._raise
        return {"ok": True}


def _run(monkeypatch, argv, fake):
    monkeypatch.setattr(cli_main, "TaosClient", lambda **k: fake)
    return cli_main.main(argv)


def test_fleet_calls_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["observatory", "fleet"], fake)
    assert rc == 0
    assert ("GET", "/api/observatory/fleet", None) in fake.calls


def test_pause_status_calls_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["observatory", "pause-status"], fake)
    assert rc == 0
    assert ("GET", "/api/observatory/pause", None) in fake.calls


def test_pause_global_by_default(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["observatory", "pause"], fake)
    assert rc == 0
    assert ("POST", "/api/observatory/pause",
            {"scope": "global", "paused": True}) in fake.calls


def test_pause_specific_lane(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["observatory", "pause", "owl-lane-1"], fake)
    assert rc == 0
    assert ("POST", "/api/observatory/pause",
            {"scope": "owl-lane-1", "paused": True}) in fake.calls


def test_resume_sends_paused_false(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["observatory", "resume", "owl-lane-1"], fake)
    assert rc == 0
    assert ("POST", "/api/observatory/pause",
            {"scope": "owl-lane-1", "paused": False}) in fake.calls


def test_throttle_status_calls_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["observatory", "throttle-status"], fake)
    assert rc == 0
    assert ("GET", "/api/observatory/throttle", None) in fake.calls


def test_throttle_set_max(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["observatory", "throttle", "owl-lane-1", "--max", "3"], fake)
    assert rc == 0
    assert ("POST", "/api/observatory/throttle",
            {"scope": "owl-lane-1", "max_concurrent": 3}) in fake.calls


def test_throttle_clear_global_sends_null(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["observatory", "throttle", "--clear"], fake)
    assert rc == 0
    assert ("POST", "/api/observatory/throttle",
            {"scope": "global", "max_concurrent": None}) in fake.calls


def test_throttle_clear_specific_lane_sends_null(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["observatory", "throttle", "owl-lane-1", "--clear"], fake)
    assert rc == 0
    assert ("POST", "/api/observatory/throttle",
            {"scope": "owl-lane-1", "max_concurrent": None}) in fake.calls


def test_throttle_requires_max_or_clear(monkeypatch):
    fake = _FakeClient()
    with pytest.raises(SystemExit):
        _run(monkeypatch, ["observatory", "throttle", "owl-lane-1"], fake)


def test_throttle_rejects_nonpositive_max(monkeypatch):
    fake = _FakeClient()
    with pytest.raises(SystemExit):
        _run(monkeypatch, ["observatory", "throttle", "--max", "0"], fake)


def test_api_error_maps_to_exit_2(monkeypatch, capsys):
    fake = _FakeClient()
    fake._raise = cli_client.ApiError(403, "forbidden")
    rc = _run(monkeypatch, ["observatory", "pause"], fake)
    assert rc == 2
    assert "forbidden" in capsys.readouterr().err


def test_transport_error_maps_to_exit_1(monkeypatch, capsys):
    fake = _FakeClient()
    fake._raise = cli_client.TransportError("cannot reach http://x: refused")
    rc = _run(monkeypatch, ["observatory", "fleet"], fake)
    assert rc == 1
    assert "cannot reach" in capsys.readouterr().err
