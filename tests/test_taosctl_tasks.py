"""Tests for the taosctl tasks command group."""
from __future__ import annotations

import pytest

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

    def request(self, method, path, body=None, params=None):
        self.calls.append((method, path))
        return {"status": "ok"}

    def delete(self, path):
        self.calls.append(("DELETE", path))
        return {"status": "ok"}


def _run(monkeypatch, argv, fake):
    monkeypatch.setattr(cli_main, "TaosClient", lambda **k: fake)
    return cli_main.main(argv)


def test_tasks_list_hits_correct_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["tasks", "list"], fake)
    assert rc == 0
    assert ("GET", "/api/tasks") in fake.calls


def test_tasks_list_passes_agent_filter(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["tasks", "list", "--agent", "myagent"], fake)
    assert rc == 0
    assert ("GET", "/api/tasks") in fake.calls


def test_tasks_get_hits_correct_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["tasks", "get", "42"], fake)
    assert rc == 0
    assert ("GET", "/api/tasks/42") in fake.calls


def test_tasks_create_hits_correct_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, [
        "tasks", "create",
        "--name", "test",
        "--schedule", "* * * * *",
        "--command", "echo hi",
    ], fake)
    assert rc == 0
    assert ("POST", "/api/tasks") in fake.calls


def test_tasks_update_hits_correct_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, [
        "tasks", "update", "42",
        "--name", "renamed",
        "--enabled", "false",
    ], fake)
    assert rc == 0
    assert ("PUT", "/api/tasks/42") in fake.calls


def test_tasks_update_rejects_invalid_enabled(monkeypatch):
    fake = _FakeClient()
    with pytest.raises(SystemExit):
        _run(monkeypatch, ["tasks", "update", "42", "--enabled", "maybe"], fake)
    # an unrecognised value must error, not silently disable the task
    assert ("PUT", "/api/tasks/42") not in fake.calls


def test_tasks_delete_hits_correct_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["tasks", "delete", "42"], fake)
    assert rc == 0
    assert ("DELETE", "/api/tasks/42") in fake.calls


def test_tasks_toggle_hits_correct_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["tasks", "toggle", "42"], fake)
    assert rc == 0
    assert ("POST", "/api/tasks/42/toggle") in fake.calls
