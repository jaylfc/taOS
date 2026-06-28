"""Tests for the taosctl secrets command group."""
from __future__ import annotations

import pytest

from tinyagentos.cli.taosctl import client as cli_client
from tinyagentos.cli.taosctl import __main__ as cli_main
from tinyagentos.cli.taosctl.commands import iter_noun_modules


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
        return {"items": [{"name": "db-pass", "category": "general"}]}

    def post(self, path, body=None, params=None, json=None):
        self.calls.append(("POST", path))
        if self._raise:
            raise self._raise
        return {"id": "db-pass", "status": "created"}

    def put(self, path, body=None, json=None):
        self.calls.append(("PUT", path))
        if self._raise:
            raise self._raise
        return {"status": "updated"}

    def delete(self, path, params=None):
        self.calls.append(("DELETE", path))
        if self._raise:
            raise self._raise
        return {"status": "deleted"}


def _run(monkeypatch, argv, fake):
    monkeypatch.setattr(cli_main, "TaosClient", lambda **k: fake)
    return cli_main.main(argv)


def test_secrets_noun_is_discovered():
    nouns = {m.NOUN for m in iter_noun_modules()}
    assert "secrets" in nouns


def test_secrets_list_calls_endpoint(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["secrets", "list"], fake)
    assert rc == 0
    assert ("GET", "/api/secrets") in fake.calls


def test_secrets_list_with_category(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["secrets", "list", "--category", "db"], fake)
    assert rc == 0
    assert ("GET", "/api/secrets") in fake.calls


def test_secrets_get_calls_endpoint(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["--json", "secrets", "get", "db-pass"], fake)
    assert rc == 0
    assert ("GET", "/api/secrets/db-pass") in fake.calls


def test_secrets_get_url_encodes_name(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["--json", "secrets", "get", "a/b c"], fake)
    assert rc == 0
    assert ("GET", "/api/secrets/a%2Fb%20c") in fake.calls


def test_secrets_create_calls_endpoint(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["--json", "secrets", "create", "new-pass", "s3cret"], fake)
    assert rc == 0
    assert ("POST", "/api/secrets") in fake.calls


def test_secrets_create_with_options(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["--json", "secrets", "create", "new-pass", "s3cret", "--category", "db", "--description", "main db"], fake)
    assert rc == 0
    assert ("POST", "/api/secrets") in fake.calls


def test_secrets_update_calls_endpoint(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["--json", "secrets", "update", "db-pass", "--value", "new-val"], fake)
    assert rc == 0
    assert ("PUT", "/api/secrets/db-pass") in fake.calls


def test_secrets_update_url_encodes_name(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["--json", "secrets", "update", "a/b", "--category", "x"], fake)
    assert rc == 0
    assert ("PUT", "/api/secrets/a%2Fb") in fake.calls


def test_secrets_delete_calls_endpoint(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["--json", "secrets", "delete", "db-pass"], fake)
    assert rc == 0
    assert ("DELETE", "/api/secrets/db-pass") in fake.calls


def test_secrets_delete_url_encodes_name(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["--json", "secrets", "delete", "x y"], fake)
    assert rc == 0
    assert ("DELETE", "/api/secrets/x%20y") in fake.calls


def test_secrets_api_error_maps_to_exit_2(monkeypatch, capsys):
    fake = _FakeClient()
    fake._raise = cli_client.ApiError(404, "no such secret")
    rc = _run(monkeypatch, ["secrets", "get", "ghost"], fake)
    assert rc == 2
    assert "no such secret" in capsys.readouterr().err


def test_secrets_transport_error_maps_to_exit_1(monkeypatch, capsys):
    fake = _FakeClient()
    fake._raise = cli_client.TransportError("cannot reach http://x: refused")
    rc = _run(monkeypatch, ["secrets", "list"], fake)
    assert rc == 1
    assert "cannot reach" in capsys.readouterr().err
