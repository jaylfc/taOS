"""Tests for the taosctl projects command group."""
from __future__ import annotations

from tinyagentos.cli.taosctl import __main__ as cli_main


class _FakeClient:
    def __init__(self, *a, **k):
        self.calls = []
        self.base_url = "http://x"
        self.token = "t"

    def get(self, path, params=None):
        self.calls.append(("GET", path))
        return {"items": [{"id": "p1", "name": "alpha"}]}

    def post(self, path, body=None, params=None):
        self.calls.append(("POST", path))
        self.last_body = body
        return {"id": "p2", "name": (body or {}).get("name", "x")}

    def patch(self, path, body=None):
        self.calls.append(("PATCH", path))
        self.last_body = body
        return {"id": "p1", "name": (body or {}).get("name", "x")}

    def delete(self, path):
        self.calls.append(("DELETE", path))
        return {"id": "p1", "status": "deleted"}


def _run(monkeypatch, argv, fake):
    monkeypatch.setattr(cli_main, "TaosClient", lambda **k: fake)
    return cli_main.main(argv)


def test_projects_list_hits_correct_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["projects", "list"], fake)
    assert rc == 0
    assert ("GET", "/api/projects") in fake.calls


def test_projects_get_targets_by_id(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["projects", "get", "p1"], fake)
    assert rc == 0
    assert ("GET", "/api/projects/p1") in fake.calls


def test_projects_create_sends_post_with_body(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["projects", "create", "alpha", "alpha-slug"], fake)
    assert rc == 0
    assert ("POST", "/api/projects") in fake.calls
    assert fake.last_body == {"name": "alpha", "slug": "alpha-slug", "description": ""}


def test_projects_update_sends_patch_with_fields(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["projects", "update", "p1", "--name", "beta"], fake)
    assert rc == 0
    assert ("PATCH", "/api/projects/p1") in fake.calls
    assert fake.last_body == {"name": "beta"}


def test_projects_delete_hits_correct_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["projects", "delete", "p1"], fake)
    assert rc == 0
    assert ("DELETE", "/api/projects/p1") in fake.calls


def test_projects_archive_hits_correct_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["projects", "archive", "p1"], fake)
    assert rc == 0
    assert ("POST", "/api/projects/p1/archive") in fake.calls
