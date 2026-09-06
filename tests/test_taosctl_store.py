"""Tests for the taosctl store command group."""
from __future__ import annotations

from tinyagentos.cli.taosctl import __main__ as cli_main


class _FakeClient:
    def __init__(self, *a, **k):
        self.calls = []
        self.base_url = "http://x"
        self.token = "t"

    def get(self, path, params=None):
        self.calls.append(("GET", path, params))
        return {"items": []}

    def post(self, path, body=None, json=None):
        self.calls.append(("POST", path, json or body))
        return {"status": "ok"}


def _run(monkeypatch, argv, fake):
    monkeypatch.setattr(cli_main, "TaosClient", lambda **k: fake)
    return cli_main.main(argv)


def test_store_list_calls_catalog(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["store", "list"], fake)
    assert rc == 0
    assert ("GET", "/api/store/catalog", None) in fake.calls


def test_store_list_with_type_filter(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["store", "list", "--type", "model"], fake)
    assert rc == 0
    assert any(c[0] == "GET" and c[1] == "/api/store/catalog" and c[2] == {"type": "model"} for c in fake.calls)


def test_store_list_with_query(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["store", "list", "--query", "llama"], fake)
    assert rc == 0
    assert any(c[0] == "GET" and c[1] == "/api/store/catalog" and c[2] == {"query": "llama"} for c in fake.calls)


def test_store_installed_calls_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["store", "installed"], fake)
    assert rc == 0
    assert ("GET", "/api/store/installed", None) in fake.calls


def test_store_get_calls_app_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["store", "get", "llama-cpp"], fake)
    assert rc == 0
    assert ("GET", "/api/store/app/llama-cpp", None) in fake.calls


def test_store_get_url_encodes_app_id(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["store", "get", "a/b c"], fake)
    assert rc == 0
    assert ("GET", "/api/store/app/a%2Fb%20c", None) in fake.calls


def test_store_popularity_calls_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["store", "popularity"], fake)
    assert rc == 0
    assert ("GET", "/api/store/popularity", None) in fake.calls


def test_store_popularity_with_type_filter(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["store", "popularity", "--type", "plugin"], fake)
    assert rc == 0
    assert any(c[0] == "GET" and c[1] == "/api/store/popularity" and c[2] == {"type": "plugin"} for c in fake.calls)


def test_store_install_posts_app_id(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["store", "install", "llama-cpp"], fake)
    assert rc == 0
    assert ("POST", "/api/store/install", {"app_id": "llama-cpp"}) in fake.calls


def test_store_install_with_variant(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["store", "install", "llama-cpp", "--variant-id", "7b-q4"], fake)
    assert rc == 0
    assert ("POST", "/api/store/install", {"app_id": "llama-cpp", "variant_id": "7b-q4"}) in fake.calls


def test_store_uninstall_posts_app_id(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["store", "uninstall", "llama-cpp"], fake)
    assert rc == 0
    assert ("POST", "/api/store/uninstall", {"app_id": "llama-cpp"}) in fake.calls


def test_store_sync_calls_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["store", "sync"], fake)
    assert rc == 0
    assert ("POST", "/api/store/sync", None) in fake.calls
