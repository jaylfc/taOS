"""taosctl agents mint / seed-internal: each verb hits the right path + body."""
from __future__ import annotations

from tinyagentos.cli.taosctl import __main__ as cli_main


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


def _run(monkeypatch, argv, fake):
    monkeypatch.setattr(cli_main, "TaosClient", lambda **k: fake)
    return cli_main.main(argv)


def test_mint_posts_handle_slug_scopes(monkeypatch):
    fake = _FakeClient()
    rc = _run(
        monkeypatch,
        ["agents", "mint", "--handle", "@taOS-dev", "--slug", "taos-dev",
         "--scopes", "a2a_send,a2a_receive"],
        fake,
    )
    assert rc == 0
    method, path, body = fake.calls[0]
    assert (method, path) == ("POST", "/api/agents/registry/mint-internal")
    assert body == {"handle": "@taOS-dev", "slug": "taos-dev", "scopes": ["a2a_send", "a2a_receive"]}


def test_mint_default_scopes(monkeypatch):
    fake = _FakeClient()
    _run(monkeypatch, ["agents", "mint", "--handle", "@x", "--slug", "x"], fake)
    _method, _path, body = fake.calls[0]
    assert body["scopes"] == ["a2a_send", "a2a_receive"]


def test_mint_with_project(monkeypatch):
    fake = _FakeClient()
    _run(
        monkeypatch,
        ["agents", "mint", "--handle", "@x", "--slug", "x", "--project", "prj-1"],
        fake,
    )
    _method, _path, body = fake.calls[0]
    assert body["project_id"] == "prj-1"


def test_seed_internal_posts(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["agents", "seed-internal"], fake)
    assert rc == 0
    method, path, _body = fake.calls[0]
    assert (method, path) == ("POST", "/api/agents/registry/seed-internal")
