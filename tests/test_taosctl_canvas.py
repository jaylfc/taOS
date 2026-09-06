"""Tests for the taosctl canvas command group."""
from __future__ import annotations

from tinyagentos.cli.taosctl import __main__ as cli_main


class _FakeClient:
    def __init__(self, *a, **k):
        self.calls = []
        self.base_url = "http://x"
        self.token = "t"

    def get(self, path, params=None):
        self.calls.append(("GET", path))
        return {"canvases": []}

    def delete(self, path, params=None):
        self.calls.append(("DELETE", path))
        return {"status": "deleted"}


def _run(monkeypatch, argv, fake):
    monkeypatch.setattr(cli_main, "TaosClient", lambda **k: fake)
    return cli_main.main(argv)


def test_canvas_list_calls_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["canvas", "list"], fake)
    assert rc == 0
    assert ("GET", "/api/canvas") in fake.calls


def test_canvas_get_targets_by_id(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["--json", "canvas", "get", "abc123"], fake)
    assert rc == 0
    assert ("GET", "/api/canvas/abc123/data") in fake.calls


def test_canvas_get_url_encodes_id(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["--json", "canvas", "get", "a/b c"], fake)
    assert rc == 0
    assert ("GET", "/api/canvas/a%2Fb%20c/data") in fake.calls


def test_canvas_delete_targets_by_id(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["--json", "canvas", "delete", "abc123"], fake)
    assert rc == 0
    assert ("DELETE", "/api/canvas/abc123") in fake.calls


def test_canvas_delete_url_encodes_id(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["--json", "canvas", "delete", "x/y"], fake)
    assert rc == 0
    assert ("DELETE", "/api/canvas/x%2Fy") in fake.calls


def test_canvas_noun_is_discovered(monkeypatch):
    from tinyagentos.cli.taosctl.commands import iter_noun_modules
    nouns = {m.NOUN for m in iter_noun_modules()}
    assert "canvas" in nouns
