"""Tests for the taosctl mail command group: verify each verb hits the correct
endpoint path using a fake client driven through __main__.main()."""
from __future__ import annotations

import json

import pytest

from tinyagentos.cli.taosctl import __main__ as cli_main


class _FakeClient:
    def __init__(self, *a, **k):
        self.calls = []
        self.base_url = "http://x"
        self.token = "t"
        self._raise = None

    def get(self, path, params=None):
        self.calls.append(("GET", path, params))
        if self._raise:
            raise self._raise
        return {}

    def post(self, path, json=None, params=None):
        self.calls.append(("POST", path, json))
        if self._raise:
            raise self._raise
        return {}

    def delete(self, path, params=None):
        self.calls.append(("DELETE", path, params))
        if self._raise:
            raise self._raise
        return {}


def _run(monkeypatch, argv, fake):
    monkeypatch.setattr(cli_main, "TaosClient", lambda **k: fake)
    return cli_main.main(argv)


def test_mail_list(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["mail", "list"], fake)
    assert rc == 0
    assert ("GET", "/api/mail/accounts", None) in fake.calls


def test_mail_create(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, [
        "mail", "create",
        "--email", "u@x.dev",
        "--imap-host", "imap.x.dev",
        "--smtp-host", "smtp.x.dev",
        "--username", "u",
        "--password", "p",
    ], fake)
    assert rc == 0
    call = next(c for c in fake.calls if c[0] == "POST")
    assert call[1] == "/api/mail/accounts"
    assert call[2]["email_address"] == "u@x.dev"


def test_mail_delete(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["mail", "delete", "abc-123"], fake)
    assert rc == 0
    assert ("DELETE", "/api/mail/accounts/abc-123", None) in fake.calls


def test_mail_delete_url_encodes_id(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["mail", "delete", "a/b c"], fake)
    assert rc == 0
    assert ("DELETE", "/api/mail/accounts/a%2Fb%20c", None) in fake.calls


def test_mail_folders(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["mail", "folders", "acc-1"], fake)
    assert rc == 0
    assert ("GET", "/api/mail/accounts/acc-1/folders", None) in fake.calls


def test_mail_messages(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["mail", "messages", "acc-1", "--folder", "Sent", "--limit", "10"], fake)
    assert rc == 0
    call = next(c for c in fake.calls if c[0] == "GET")
    assert call[1] == "/api/mail/accounts/acc-1/messages"
    assert call[2] == {"folder": "Sent", "limit": 10}


def test_mail_get(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["mail", "get", "acc-1", "42", "--folder", "INBOX"], fake)
    assert rc == 0
    call = next(c for c in fake.calls if c[0] == "GET")
    assert call[1] == "/api/mail/accounts/acc-1/messages/42"
    assert call[2] == {"folder": "INBOX"}


def test_mail_get_url_encodes_uid(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["mail", "get", "acc-1", "4 2/3"], fake)
    assert rc == 0
    call = next(c for c in fake.calls if c[0] == "GET")
    assert call[1] == "/api/mail/accounts/acc-1/messages/4%202%2F3"


def test_mail_send(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, [
        "mail", "send", "acc-1",
        "--to", "b@x.dev",
        "--subject", "hi",
        "--body", "hello",
        "--cc", "c@x.dev",
    ], fake)
    assert rc == 0
    call = next(c for c in fake.calls if c[0] == "POST")
    assert call[1] == "/api/mail/accounts/acc-1/send"
    assert call[2]["to"] == "b@x.dev"
    assert call[2]["subject"] == "hi"
    assert call[2]["body"] == "hello"
    assert call[2]["cc"] == "c@x.dev"


def test_mail_noun_is_discovered():
    from tinyagentos.cli.taosctl.commands import iter_noun_modules
    nouns = {m.NOUN for m in iter_noun_modules()}
    assert "mail" in nouns


def test_resolve_password_precedence(monkeypatch):
    from tinyagentos.cli.taosctl.commands.mail import _resolve_password
    import argparse

    # explicit arg wins
    a = argparse.Namespace(password="from-arg")
    monkeypatch.setenv("TAOS_MAIL_PASSWORD", "from-env")
    assert _resolve_password(a) == "from-arg"

    # env var used when no arg (no command-line exposure)
    b = argparse.Namespace(password=None)
    assert _resolve_password(b) == "from-env"

    # falls through to prompt when neither present
    c = argparse.Namespace(password=None)
    monkeypatch.delenv("TAOS_MAIL_PASSWORD", raising=False)
    monkeypatch.setattr("getpass.getpass", lambda *_: "from-prompt")
    assert _resolve_password(c) == "from-prompt"
