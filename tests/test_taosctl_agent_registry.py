"""Tests for the taosctl agent-registry command group: each verb hits the right path."""
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
        return {"ok": True}

    def post(self, path, body=None, params=None, json=None):
        self.calls.append(("POST", path, body))
        return {"ok": True}

    def patch(self, path, body=None, json=None):
        self.calls.append(("PATCH", path, body))
        return {"ok": True}

    def delete(self, path, params=None):
        self.calls.append(("DELETE", path, params))
        return {"ok": True}


def _run(monkeypatch, argv, fake):
    monkeypatch.setattr(cli_main, "TaosClient", lambda **k: fake)
    return cli_main.main(argv)


def _paths(fake):
    return [(m, p) for (m, p, *_rest) in fake.calls]


def test_list_no_status(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["agent-registry", "list"], fake) == 0
    method, path, params = fake.calls[0]
    assert (method, path) == ("GET", "/api/agents/registry")
    assert params is None


def test_list_with_status(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["agent-registry", "list", "--status", "pending"], fake) == 0
    method, path, params = fake.calls[0]
    assert params == {"status": "pending"}


def test_get(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["agent-registry", "get", "alpha-20260101-000000"], fake) == 0
    assert ("GET", "/api/agents/registry/alpha-20260101-000000") in _paths(fake)


def test_pubkey(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["agent-registry", "pubkey"], fake) == 0
    assert ("GET", "/api/agents/registry/pubkey") in _paths(fake)


def test_revoked(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["agent-registry", "revoked"], fake) == 0
    assert ("GET", "/api/agents/registry/revoked") in _paths(fake)


def test_inactive(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["agent-registry", "inactive"], fake) == 0
    assert ("GET", "/api/agents/registry/inactive") in _paths(fake)


def test_grants_all(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["agent-registry", "grants"], fake) == 0
    method, path, params = fake.calls[0]
    assert (method, path) == ("GET", "/api/agents/registry/grants")
    assert params is None


def test_grants_filtered(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["agent-registry", "grants", "--canonical-id", "alpha"], fake) == 0
    method, path, params = fake.calls[0]
    assert params == {"canonical_id": "alpha"}


def test_update_sends_only_supplied(monkeypatch):
    fake = _FakeClient()
    rc = _run(
        monkeypatch,
        ["agent-registry", "update", "alpha", "--display-name", "Alpha", "--capability", "app.llm"],
        fake,
    )
    assert rc == 0
    method, path, body = fake.calls[0]
    assert (method, path) == ("PATCH", "/api/agents/registry/alpha")
    assert body == {"display_name": "Alpha", "capabilities": ["app.llm"]}
    assert "handle" not in body and "role" not in body


def test_update_with_no_fields_errors_without_calling(monkeypatch):
    fake = _FakeClient()
    # No fields supplied: must not send an empty-body PATCH; exits non-zero so
    # scripts can detect the mistake.
    with pytest.raises(SystemExit):
        _run(monkeypatch, ["agent-registry", "update", "alpha"], fake)
    assert fake.calls == []


def test_revoke(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["agent-registry", "revoke", "alpha"], fake) == 0
    assert ("DELETE", "/api/agents/registry/alpha") in _paths(fake)


@pytest.mark.parametrize("verb", ["approve", "reject", "suspend", "reactivate"])
def test_lifecycle_transitions(monkeypatch, verb):
    fake = _FakeClient()
    assert _run(monkeypatch, ["agent-registry", verb, "alpha"], fake) == 0
    assert ("POST", f"/api/agents/registry/alpha/{verb}") in _paths(fake)
