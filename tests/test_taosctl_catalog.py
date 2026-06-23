"""Tests for the taosctl catalog command group: each verb hits the right path."""
from __future__ import annotations

from tinyagentos.cli.taosctl import __main__ as cli_main


class _FakeClient:
    def __init__(self, *a, **k):
        self.calls = []
        self.base_url = "http://x"
        self.token = "t"

    def get(self, path, params=None):
        self.calls.append(("GET", path, params))
        return {"items": [{"id": 1}]}

    def post(self, path, body=None, params=None, json=None):
        self.calls.append(("POST", path, body))
        return {"ok": True}


def _run(monkeypatch, argv, fake):
    monkeypatch.setattr(cli_main, "TaosClient", lambda **k: fake)
    return cli_main.main(argv)


def _paths(fake):
    return [(m, p) for (m, p, *_rest) in fake.calls]


def test_stats(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["catalog", "stats"], fake) == 0
    assert ("GET", "/api/memory/catalog/stats") in _paths(fake)


def test_date(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["catalog", "date", "2026-06-23"], fake) == 0
    assert ("GET", "/api/memory/catalog/date/2026-06-23") in _paths(fake)


def test_range_passes_params(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["catalog", "range", "2026-06-01", "2026-06-23"], fake) == 0
    method, path, params = fake.calls[0]
    assert (method, path) == ("GET", "/api/memory/catalog/range")
    assert params == {"start": "2026-06-01", "end": "2026-06-23"}


def test_search_passes_params(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["catalog", "search", "agents", "--limit", "5"], fake) == 0
    method, path, params = fake.calls[0]
    assert (method, path) == ("GET", "/api/memory/catalog/search")
    assert params == {"q": "agents", "limit": 5}


def test_session(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["catalog", "session", "42"], fake) == 0
    assert ("GET", "/api/memory/catalog/session/42") in _paths(fake)


def test_context(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["catalog", "context", "42"], fake) == 0
    assert ("GET", "/api/memory/catalog/session/42/context") in _paths(fake)


def test_recent(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["catalog", "recent", "--limit", "3"], fake) == 0
    method, path, params = fake.calls[0]
    assert (method, path) == ("GET", "/api/memory/catalog/recent")
    assert params == {"limit": 3}


def test_index_posts_body(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["catalog", "index", "--date", "2026-06-23", "--force"], fake) == 0
    method, path, body = fake.calls[0]
    assert (method, path) == ("POST", "/api/memory/catalog/index")
    assert body["date"] == "2026-06-23"
    assert body["force"] is True


def test_rebuild_posts(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["catalog", "rebuild"], fake) == 0
    assert ("POST", "/api/memory/catalog/rebuild") in _paths(fake)
