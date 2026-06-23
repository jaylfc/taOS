"""Tests for the taosctl knowledge command group: each verb hits the right path."""
from __future__ import annotations

import pytest

from tinyagentos.cli.taosctl import __main__ as cli_main


class _FakeClient:
    def __init__(self, *a, **k):
        self.calls = []
        self.base_url = "http://x"
        self.token = "t"

    def get(self, path, params=None):
        self.calls.append(("GET", path, params))
        return {"items": [{"id": "k1"}]}

    def post(self, path, body=None, params=None, json=None):
        self.calls.append(("POST", path, body))
        return {"id": "k1", "status": "pending"}

    def delete(self, path, params=None):
        self.calls.append(("DELETE", path, params))
        return {"ok": True}


def _run(monkeypatch, argv, fake):
    monkeypatch.setattr(cli_main, "TaosClient", lambda **k: fake)
    return cli_main.main(argv)


def _paths(fake):
    return [(m, p) for (m, p, *_rest) in fake.calls]


def test_list_items(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["knowledge", "list"], fake) == 0
    assert ("GET", "/api/knowledge/items") in _paths(fake)


def test_get_item(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["knowledge", "get", "k1"], fake) == 0
    assert ("GET", "/api/knowledge/items/k1") in _paths(fake)


def test_snapshots(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["knowledge", "snapshots", "k1"], fake) == 0
    assert ("GET", "/api/knowledge/items/k1/snapshots") in _paths(fake)


def test_delete_item(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["knowledge", "delete", "k1"], fake) == 0
    assert ("DELETE", "/api/knowledge/items/k1") in _paths(fake)


def test_search_passes_query_params(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["knowledge", "search", "neural", "--mode", "semantic", "--limit", "5"], fake) == 0
    method, path, params = fake.calls[0]
    assert (method, path) == ("GET", "/api/knowledge/search")
    assert params == {"q": "neural", "mode": "semantic", "limit": 5}


def test_ingest_posts_body(monkeypatch):
    fake = _FakeClient()
    rc = _run(
        monkeypatch,
        ["knowledge", "ingest", "--url", "https://e.com", "--category", "a", "--category", "b"],
        fake,
    )
    assert rc == 0
    method, path, body = fake.calls[0]
    assert (method, path) == ("POST", "/api/knowledge/ingest")
    assert body["url"] == "https://e.com"
    assert body["categories"] == ["a", "b"]


def test_rules_list(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["knowledge", "rules"], fake) == 0
    assert ("GET", "/api/knowledge/rules") in _paths(fake)


def test_add_rule_posts_fields(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["knowledge", "add-rule", "--pattern", "ml*", "--category", "ai"], fake)
    assert rc == 0
    method, path, body = fake.calls[0]
    assert (method, path) == ("POST", "/api/knowledge/rules")
    assert body == {"pattern": "ml*", "match_on": "title", "category": "ai"}


def test_delete_rule(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["knowledge", "delete-rule", "r1"], fake) == 0
    assert ("DELETE", "/api/knowledge/rules/r1") in _paths(fake)


def test_subscriptions_list(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["knowledge", "subscriptions"], fake) == 0
    assert ("GET", "/api/knowledge/subscriptions") in _paths(fake)


def test_subscribe_posts_body(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["knowledge", "subscribe", "--agent", "alpha", "--category", "ai", "--auto-ingest"], fake)
    assert rc == 0
    method, path, body = fake.calls[0]
    assert (method, path) == ("POST", "/api/knowledge/subscriptions")
    assert body == {"agent_name": "alpha", "category": "ai", "auto_ingest": True}


def test_unsubscribe_targets_agent_and_category(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["knowledge", "unsubscribe", "alpha", "ai"], fake) == 0
    assert ("DELETE", "/api/knowledge/subscriptions/alpha/ai") in _paths(fake)
