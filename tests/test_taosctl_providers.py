"""Tests for the taosctl providers command group: verb dispatch, endpoint
paths, URL encoding, and JSON body construction."""
from __future__ import annotations

import json

import pytest

from tinyagentos.cli.taosctl import __main__ as cli_main
from tinyagentos.cli.taosctl import output
from tinyagentos.cli.taosctl.client import ApiError, TransportError
from tinyagentos.cli.taosctl.commands import iter_noun_modules


# ---- discovery ----------------------------------------------------------------

def test_providers_noun_is_discovered():
    nouns = {m.NOUN for m in iter_noun_modules()}
    assert "providers" in nouns


# ---- fake client -------------------------------------------------------------

class _FakeClient:
    def __init__(self, *a, **k):
        self.calls = []
        self.base_url = "http://x"
        self.token = "t"
        self._raise = None

    def get(self, path, params=None):
        self.calls.append(("GET", path, None))
        if self._raise:
            raise self._raise
        return {"items": [{"name": "openai", "type": "openai", "status": "ok"}]}

    def post(self, path, body=None, params=None, json=None):
        payload = body if body is not None else json
        self.calls.append(("POST", path, payload))
        if self._raise:
            raise self._raise
        return {"status": "ok"}

    def patch(self, path, body=None, json=None):
        payload = body if body is not None else json
        self.calls.append(("PATCH", path, payload))
        if self._raise:
            raise self._raise
        return {"status": "updated"}

    def delete(self, path, params=None):
        self.calls.append(("DELETE", path, None))
        if self._raise:
            raise self._raise
        return {"status": "deleted"}


def _run(monkeypatch, argv, fake):
    monkeypatch.setattr(cli_main, "TaosClient", lambda **k: fake)
    return cli_main.main(argv)


# ---- list ---------------------------------------------------------------------

def test_providers_list_calls_endpoint(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["providers", "list"], fake)
    assert rc == 0
    assert ("GET", "/api/providers", None) in fake.calls


# ---- get ----------------------------------------------------------------------

# ---- create -------------------------------------------------------------------

def test_providers_create_posts_body(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, [
        "providers", "create",
        "--name", "openai",
        "--type", "openai",
        "--url", "https://api.openai.com/v1",
        "--api-key-secret", "my-secret",
    ], fake)
    assert rc == 0
    call = next(c for c in fake.calls if c[0] == "POST" and c[1] == "/api/providers")
    body = call[2]
    assert body["name"] == "openai"
    assert body["type"] == "openai"
    assert body["url"] == "https://api.openai.com/v1"
    assert body["api_key_secret"] == "my-secret"


def test_providers_create_minimal_body(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, [
        "providers", "create",
        "--name", "ollama",
        "--type", "ollama",
    ], fake)
    assert rc == 0
    call = next(c for c in fake.calls if c[0] == "POST" and c[1] == "/api/providers")
    body = call[2]
    assert body == {"name": "ollama", "type": "ollama"}


# ---- update -------------------------------------------------------------------

def test_providers_update_patches_fields(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, [
        "providers", "update", "openai",
        "--url", "https://new.example.com/v1",
        "--enabled", "false",
    ], fake)
    assert rc == 0
    call = next(c for c in fake.calls if c[0] == "PATCH")
    assert call[1] == "/api/providers/openai"
    body = call[2]
    assert body["url"] == "https://new.example.com/v1"
    assert body["enabled"] is False


def test_providers_update_url_encodes_name(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, [
        "providers", "update", "my/provider",
        "--enabled", "true",
    ], fake)
    assert rc == 0
    call = next(c for c in fake.calls if c[0] == "PATCH")
    assert call[1] == "/api/providers/my%2Fprovider"
    assert call[2]["enabled"] is True


def test_providers_update_auto_manage_and_keep_alive(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, [
        "providers", "update", "ollama",
        "--auto-manage", "true",
        "--keep-alive-minutes", "30",
    ], fake)
    assert rc == 0
    call = next(c for c in fake.calls if c[0] == "PATCH")
    body = call[2]
    assert body["auto_manage"] is True
    assert body["keep_alive_minutes"] == 30


# ---- delete -------------------------------------------------------------------

def test_providers_delete_hits_endpoint(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["providers", "delete", "openai"], fake)
    assert rc == 0
    assert ("DELETE", "/api/providers/openai", None) in fake.calls


def test_providers_delete_url_encodes_name(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["providers", "delete", "a/b"], fake)
    assert rc == 0
    assert ("DELETE", "/api/providers/a%2Fb", None) in fake.calls


# ---- start --------------------------------------------------------------------

def test_providers_start_posts(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["providers", "start", "ollama"], fake)
    assert rc == 0
    assert ("POST", "/api/providers/ollama/start", None) in fake.calls


# ---- stop ---------------------------------------------------------------------

def test_providers_stop_posts(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["providers", "stop", "ollama"], fake)
    assert rc == 0
    assert ("POST", "/api/providers/ollama/stop", None) in fake.calls


# ---- error mapping ------------------------------------------------------------

def test_api_error_maps_to_exit_2(monkeypatch, capsys):
    fake = _FakeClient()
    fake._raise = ApiError(404, "no such provider")
    rc = _run(monkeypatch, ["providers", "delete", "ghost"], fake)
    assert rc == 2
    assert "no such provider" in capsys.readouterr().err


def test_transport_error_maps_to_exit_1(monkeypatch, capsys):
    fake = _FakeClient()
    fake._raise = TransportError("cannot reach http://x: refused")
    rc = _run(monkeypatch, ["providers", "list"], fake)
    assert rc == 1
    assert "cannot reach" in capsys.readouterr().err


def test_providers_update_with_no_fields_errors(monkeypatch):
    import pytest
    fake = _FakeClient()
    with pytest.raises(SystemExit):
        _run(monkeypatch, ["providers", "update", "openai"], fake)
    assert not any(c[0] == "PATCH" for c in fake.calls)
