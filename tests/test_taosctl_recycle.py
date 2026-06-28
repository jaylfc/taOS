"""Tests for the taosctl recycle command group: each verb hits the right path."""
from __future__ import annotations

from tinyagentos.cli.taosctl import __main__ as cli_main


class _FakeClient:
    def __init__(self, *a, **k):
        self.calls = []
        self.base_url = "http://x"
        self.token = "t"

    def get(self, path, params=None):
        self.calls.append(("GET", path, None))
        return {"items": []}

    def post(self, path, body=None, params=None, json=None):
        self.calls.append(("POST", path, body))
        return {"ok": True}

    def delete(self, path, params=None):
        self.calls.append(("DELETE", path, None))
        return {"ok": True}


def _run(monkeypatch, argv, fake):
    monkeypatch.setattr(cli_main, "TaosClient", lambda **k: fake)
    return cli_main.main(argv)


def _paths(fake):
    return [(m, p) for (m, p, *_rest) in fake.calls]


def test_list_for_agent(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["recycle", "list", "alpha"], fake) == 0
    assert ("GET", "/api/agents/alpha/recycle") in _paths(fake)


def test_list_all(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["recycle", "list-all"], fake) == 0
    assert ("GET", "/api/recycle") in _paths(fake)


def test_restore_by_id(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["recycle", "restore", "alpha", "--id", "abc"], fake) == 0
    method, path, body = fake.calls[0]
    assert (method, path) == ("POST", "/api/agents/alpha/recycle/restore")
    assert body == {"id": "abc"}


def test_restore_by_original_path(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["recycle", "restore", "alpha", "--original-path", "/x/y.txt"], fake) == 0
    method, path, body = fake.calls[0]
    assert (method, path) == ("POST", "/api/agents/alpha/recycle/restore")
    assert body == {"original_path": "/x/y.txt"}


def test_restore_requires_a_target(monkeypatch):
    import pytest

    fake = _FakeClient()
    # No --id and no --original-path: the required mutex group rejects it at the
    # CLI (exit 2) before any request, so the server never sees an empty body.
    with pytest.raises(SystemExit):
        _run(monkeypatch, ["recycle", "restore", "alpha"], fake)
    assert fake.calls == []


def test_purge(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["recycle", "purge", "alpha", "item7"], fake) == 0
    assert ("DELETE", "/api/agents/alpha/recycle/item7") in _paths(fake)
