"""Tests for `taosctl relationships`: each verb hits the right method, path and body."""
from __future__ import annotations

from tinyagentos.cli.taosctl import __main__ as cli_main
from tinyagentos.cli.taosctl.commands import iter_noun_modules


class _FakeClient:
    def __init__(self, *a, **k):
        self.calls = []
        self.base_url = "http://x"
        self.token = "t"

    def get(self, path, params=None):
        self.calls.append(("GET", path, None))
        return {"ok": True}

    def post(self, path, body=None, params=None, json=None):
        self.calls.append(("POST", path, body))
        return {"ok": True}

    def put(self, path, body=None, json=None):
        self.calls.append(("PUT", path, body))
        return {"ok": True}

    def patch(self, path, body=None, json=None):
        self.calls.append(("PATCH", path, body))
        return {"ok": True}

    def delete(self, path, params=None):
        self.calls.append(("DELETE", path, None))
        return {"ok": True}

    def request(self, method, path, params=None, body=None):
        self.calls.append((method, path, body))
        return {"ok": True}


def _run(monkeypatch, argv):
    fake = _FakeClient()
    monkeypatch.setattr(cli_main, "TaosClient", lambda **k: fake)
    rc = cli_main.main(["--json", *argv])
    assert rc == 0
    assert len(fake.calls) == 1
    return fake.calls[0]


def test_relationships_noun_is_discovered():
    assert "relationships" in {m.NOUN for m in iter_noun_modules()}


def test_list(monkeypatch):
    assert _run(monkeypatch, ["relationships", "list"]) == (
        "GET", "/api/relationships/groups", None)


def test_create_minimal_sends_only_name(monkeypatch):
    assert _run(monkeypatch, ["relationships", "create", "ops"]) == (
        "POST", "/api/relationships/groups", {"name": "ops"})


def test_create_full_body(monkeypatch):
    call = _run(monkeypatch, [
        "relationships", "create", "ops", "--description", "on call",
        "--lead", "alpha", "--color", "#ff0000",
    ])
    assert call == ("POST", "/api/relationships/groups", {
        "name": "ops", "description": "on call",
        "lead_agent": "alpha", "color": "#ff0000",
    })


def test_update_sends_only_given_fields_via_put(monkeypatch):
    call = _run(monkeypatch, ["relationships", "update", "7", "--name", "ops2", "--lead", "beta"])
    assert call == ("PUT", "/api/relationships/groups/7",
                    {"name": "ops2", "lead_agent": "beta"})


def test_update_rejects_non_integer_id(monkeypatch, capsys):
    fake = _FakeClient()
    monkeypatch.setattr(cli_main, "TaosClient", lambda **k: fake)
    try:
        rc = cli_main.main(["relationships", "update", "abc"])
    except SystemExit as exc:
        rc = exc.code
    assert rc != 0
    assert fake.calls == []


def test_delete(monkeypatch):
    assert _run(monkeypatch, ["relationships", "delete", "7"]) == (
        "DELETE", "/api/relationships/groups/7", None)


def test_add_member_default_role_omitted(monkeypatch):
    assert _run(monkeypatch, ["relationships", "add-member", "3", "alpha"]) == (
        "POST", "/api/relationships/groups/3/members", {"agent_name": "alpha"})


def test_add_member_with_role(monkeypatch):
    call = _run(monkeypatch, ["relationships", "add-member", "3", "alpha", "--role", "lead"])
    assert call == ("POST", "/api/relationships/groups/3/members",
                    {"agent_name": "alpha", "role": "lead"})


def test_remove_member_url_encodes_name(monkeypatch):
    assert _run(monkeypatch, ["relationships", "remove-member", "3", "a/b c"]) == (
        "DELETE", "/api/relationships/groups/3/members/a%2Fb%20c", None)


def test_agent_info(monkeypatch):
    assert _run(monkeypatch, ["relationships", "agent", "alpha"]) == (
        "GET", "/api/relationships/agent/alpha", None)


def test_allow(monkeypatch):
    call = _run(monkeypatch, ["relationships", "allow", "--from", "alpha", "--to", "beta"])
    assert call == ("POST", "/api/relationships/permissions",
                    {"from_agent": "alpha", "to_agent": "beta"})


def test_revoke_sends_delete_with_body(monkeypatch):
    call = _run(monkeypatch, ["relationships", "revoke", "--from", "alpha", "--to", "beta"])
    assert call == ("DELETE", "/api/relationships/permissions",
                    {"from_agent": "alpha", "to_agent": "beta"})
