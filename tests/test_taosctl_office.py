"""Tests for the taosctl office command group: each verb hits the right path."""
from __future__ import annotations

from tinyagentos.cli.taosctl import __main__ as cli_main


class _FakeClient:
    def __init__(self, *a, **k):
        self.calls = []
        self.base_url = "http://x"
        self.token = "t"

    def get(self, path, params=None):
        self.calls.append(("GET", path, None))
        return {"items": [{"id": "d1"}]}

    def post(self, path, body=None, params=None, json=None):
        self.calls.append(("POST", path, body))
        return {"id": "d1"}

    def put(self, path, body=None, json=None):
        self.calls.append(("PUT", path, body))
        return {"id": "d1"}

    def delete(self, path, params=None):
        self.calls.append(("DELETE", path, None))
        return {"ok": True}


def _run(monkeypatch, argv, fake):
    monkeypatch.setattr(cli_main, "TaosClient", lambda **k: fake)
    return cli_main.main(argv)


def _paths(fake):
    return [(m, p) for (m, p, *_rest) in fake.calls]


def test_create_posts_body(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["office", "create", "--kind", "write", "--title", "Notes"], fake) == 0
    method, path, body = fake.calls[0]
    assert (method, path) == ("POST", "/api/office/docs")
    assert body == {"kind": "write", "title": "Notes", "content": ""}


def test_create_rejects_bad_kind(monkeypatch):
    fake = _FakeClient()
    # argparse choices rejects an invalid kind before any client call (exit 2).
    import pytest

    with pytest.raises(SystemExit):
        _run(monkeypatch, ["office", "create", "--kind", "bogus", "--title", "x"], fake)
    assert fake.calls == []


def test_list(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["office", "list"], fake) == 0
    assert ("GET", "/api/office/docs") in _paths(fake)


def test_get(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["office", "get", "d1"], fake) == 0
    assert ("GET", "/api/office/docs/d1") in _paths(fake)


def test_update_sends_only_supplied_fields(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["office", "update", "d1", "--title", "Renamed"], fake) == 0
    method, path, body = fake.calls[0]
    assert (method, path) == ("PUT", "/api/office/docs/d1")
    assert body == {"title": "Renamed"}


def test_delete(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["office", "delete", "d1"], fake) == 0
    assert ("DELETE", "/api/office/docs/d1") in _paths(fake)
