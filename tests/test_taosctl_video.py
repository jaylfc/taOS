"""Tests for the taosctl video command: dispatch, endpoint paths, and error mapping."""
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
        self.calls.append(("GET", path))
        if self._raise:
            raise self._raise
        return {"videos": []}

    def post(self, path, body=None, params=None):
        self.calls.append(("POST", path, body))
        if self._raise:
            raise self._raise
        return {"status": "generated"}

    def delete(self, path, params=None):
        self.calls.append(("DELETE", path))
        if self._raise:
            raise self._raise
        return {"status": "deleted"}


def _run(monkeypatch, argv, fake):
    monkeypatch.setattr(cli_main, "TaosClient", lambda **k: fake)
    return cli_main.main(argv)


def test_video_list_calls_endpoint(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["video", "list"], fake)
    assert rc == 0
    assert ("GET", "/api/video") in fake.calls


def test_video_generate_posts_prompt(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["video", "generate", "a cat"], fake)
    assert rc == 0
    assert fake.calls[0][0] == "POST"
    assert fake.calls[0][1] == "/api/video/generate"
    assert fake.calls[0][2]["prompt"] == "a cat"


def test_video_generate_url_encodes_prompt_with_spaces(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["video", "generate", "a cat dancing"], fake)
    assert rc == 0
    assert fake.calls[0][2]["prompt"] == "a cat dancing"


def test_video_generate_includes_optional_args(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, [
        "video", "generate", "sunset",
        "--model", "wan2.1-14b",
        "--duration", "10",
        "--resolution", "720x1280",
        "--seed", "42",
    ], fake)
    assert rc == 0
    body = fake.calls[0][2]
    assert body == {
        "prompt": "sunset",
        "model": "wan2.1-14b",
        "duration": 10,
        "resolution": "720x1280",
        "seed": 42,
    }


def test_video_generate_default_body(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["video", "generate", "test"], fake)
    assert rc == 0
    body = fake.calls[0][2]
    assert body["model"] == "wan2.1-1.3b"
    assert body["duration"] == 5
    assert body["resolution"] == "480x832"
    assert body["seed"] is None


def test_video_delete_calls_endpoint(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["video", "delete", "12345_1.mp4"], fake)
    assert rc == 0
    assert ("DELETE", "/api/video/12345_1.mp4") in fake.calls


def test_video_delete_url_encodes_filename(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["video", "delete", "a b c.mp4"], fake)
    assert rc == 0
    assert ("DELETE", "/api/video/a%20b%20c.mp4") in fake.calls


def test_video_api_error_maps_to_exit_2(monkeypatch, capsys):
    from tinyagentos.cli.taosctl.client import ApiError
    fake = _FakeClient()
    fake._raise = ApiError(503, "No video backend")
    rc = _run(monkeypatch, ["video", "generate", "fail"], fake)
    assert rc == 2
    assert "No video backend" in capsys.readouterr().err


def test_video_transport_error_maps_to_exit_1(monkeypatch, capsys):
    from tinyagentos.cli.taosctl.client import TransportError
    fake = _FakeClient()
    fake._raise = TransportError("cannot reach http://x: refused")
    rc = _run(monkeypatch, ["video", "list"], fake)
    assert rc == 1
    assert "cannot reach" in capsys.readouterr().err
