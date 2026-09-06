"""Tests for the taosctl shared_folders command group."""
from __future__ import annotations

from tinyagentos.cli.taosctl import __main__ as cli_main


class _FakeClient:
    def __init__(self, *a, **k):
        self.calls = []
        self.base_url = "http://x"
        self.token = "t"

    def get(self, path, params=None):
        self.calls.append(("GET", path))
        if params:
            self.last_params = params
        if path == "/api/shared-folders":
            # The list endpoint returns a bare list of folders.
            return [{"id": "1", "name": "docs"}]
        return {"items": []}

    def post(self, path, body=None, params=None):
        self.calls.append(("POST", path))
        self.last_body = body
        return {"id": "2", "name": (body or {}).get("name", "x")}

    def delete(self, path):
        self.calls.append(("DELETE", path))
        return {"id": "1", "status": "deleted"}


def _run(monkeypatch, argv, fake):
    monkeypatch.setattr(cli_main, "TaosClient", lambda **k: fake)
    return cli_main.main(argv)


def test_shared_folders_list_hits_correct_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["shared_folders", "list"], fake)
    assert rc == 0
    assert ("GET", "/api/shared-folders") in fake.calls


def test_shared_folders_list_with_agent_name_param(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["shared_folders", "list", "--agent-name", "alpha"], fake)
    assert rc == 0
    assert ("GET", "/api/shared-folders") in fake.calls
    assert fake.last_params == {"agent_name": "alpha"}


def test_shared_folders_get_filters_list_by_id(monkeypatch):
    # No single-folder GET route exists, so get fetches the list and filters.
    fake = _FakeClient()
    rc = _run(monkeypatch, ["shared_folders", "get", "1"], fake)
    assert rc == 0
    assert ("GET", "/api/shared-folders") in fake.calls


def test_shared_folders_create_sends_post_with_body(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["shared_folders", "create", "docs"], fake)
    assert rc == 0
    assert ("POST", "/api/shared-folders") in fake.calls
    assert fake.last_body == {"name": "docs", "description": ""}


def test_shared_folders_create_with_agents(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["shared_folders", "create", "docs", "--agents", "a,b"], fake)
    assert rc == 0
    assert fake.last_body == {"name": "docs", "description": "", "agents": ["a", "b"]}


def test_shared_folders_delete_hits_correct_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["shared_folders", "delete", "1"], fake)
    assert rc == 0
    assert ("DELETE", "/api/shared-folders/1") in fake.calls


def test_shared_folders_files_hits_correct_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["shared_folders", "files", "docs"], fake)
    assert rc == 0
    assert ("GET", "/api/shared-folders/docs/files") in fake.calls


def test_shared_folders_grant_hits_correct_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["shared_folders", "grant", "1", "alpha"], fake)
    assert rc == 0
    assert ("POST", "/api/shared-folders/1/access") in fake.calls
    assert fake.last_body == {"agent_name": "alpha", "permission": "readwrite"}
