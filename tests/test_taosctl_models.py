"""Tests for the taosctl models command group."""
from __future__ import annotations

from tinyagentos.cli.taosctl import __main__ as cli_main


class _FakeClient:
    def __init__(self, *a, **k):
        self.calls = []
        self.base_url = "http://x"
        self.token = "t"

    def get(self, path, params=None):
        self.calls.append(("GET", path))
        return {"items": []}

    def post(self, path, body=None, params=None):
        self.calls.append(("POST", path))
        return {"status": "ok"}

    def delete(self, path):
        self.calls.append(("DELETE", path))
        return {"status": "deleted"}


def _run(monkeypatch, argv, fake):
    monkeypatch.setattr(cli_main, "TaosClient", lambda **k: fake)
    return cli_main.main(argv)


def test_models_list(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["models", "list"], fake)
    assert rc == 0
    assert ("GET", "/api/models") in fake.calls


def test_models_get(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["models", "get", "llama-3"], fake)
    assert rc == 0
    assert ("GET", "/api/models/llama-3") in fake.calls


def test_models_get_url_encodes_id(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["models", "get", "a/b c"], fake)
    assert rc == 0
    assert ("GET", "/api/models/a%2Fb%20c") in fake.calls


def test_models_delete(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["models", "delete", "llama-3"], fake)
    assert rc == 0
    assert ("DELETE", "/api/models/llama-3") in fake.calls


def test_models_recommended(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["models", "recommended"], fake)
    assert rc == 0
    assert ("GET", "/api/models/recommended") in fake.calls


def test_models_loaded(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["models", "loaded"], fake)
    assert rc == 0
    assert ("GET", "/api/models/loaded") in fake.calls


def test_models_downloads(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["models", "downloads"], fake)
    assert rc == 0
    assert ("GET", "/api/models/downloads") in fake.calls


def test_models_download(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["models", "download", "--app-id", "llama", "--variant-id", "q4"], fake)
    assert rc == 0
    assert ("POST", "/api/models/download") in fake.calls


def test_models_pull(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["models", "pull", "--model-name", "llama3"], fake)
    assert rc == 0
    assert ("POST", "/api/models/pull") in fake.calls
