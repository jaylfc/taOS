"""Tests for the taosctl bookmarks command group: dispatch, endpoint paths,
URL-encoding, and error mapping."""
from __future__ import annotations

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
        return {"bookmarks": []}

    def post(self, path, body=None, params=None):
        self.calls.append(("POST", path, body, params))
        if self._raise:
            raise self._raise
        return {"bookmark_id": "bm-1"}

    def delete(self, path, params=None):
        self.calls.append(("DELETE", path, params))
        if self._raise:
            raise self._raise
        return None


def _run(monkeypatch, argv, fake):
    monkeypatch.setattr(cli_main, "TaosClient", lambda **k: fake)
    return cli_main.main(argv)


def test_bookmarks_list_calls_endpoint(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["bookmarks", "list", "--profile-id", "prof-1"], fake)
    assert rc == 0
    assert ("GET", "/api/desktop/browser/bookmarks", {"profile_id": "prof-1"}) in fake.calls


def test_bookmarks_create_posts_body(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(
        monkeypatch,
        ["bookmarks", "create", "--profile-id", "prof-1", "--url", "https://example.com", "--title", "Example"],
        fake,
    )
    assert rc == 0
    assert (
        "POST",
        "/api/desktop/browser/bookmarks",
        {"profile_id": "prof-1", "url": "https://example.com", "title": "Example"},
        None,
    ) in fake.calls


def test_bookmarks_delete_url_encodes_id(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(
        monkeypatch,
        ["bookmarks", "delete", "bm/1 2", "--profile-id", "prof-1"],
        fake,
    )
    assert rc == 0
    assert (
        "DELETE",
        "/api/desktop/browser/bookmarks/bm%2F1%202",
        {"profile_id": "prof-1"},
    ) in fake.calls


def test_bookmarks_api_error_maps_to_exit_2(monkeypatch, capsys):
    from tinyagentos.cli.taosctl.client import ApiError

    fake = _FakeClient()
    fake._raise = ApiError(400, "bad request")
    rc = _run(monkeypatch, ["bookmarks", "list", "--profile-id", "prof-1"], fake)
    assert rc == 2
    assert "bad request" in capsys.readouterr().err


def test_bookmarks_transport_error_maps_to_exit_1(monkeypatch, capsys):
    from tinyagentos.cli.taosctl.client import TransportError

    fake = _FakeClient()
    fake._raise = TransportError("cannot reach http://x: refused")
    rc = _run(monkeypatch, ["bookmarks", "list", "--profile-id", "prof-1"], fake)
    assert rc == 1
    assert "cannot reach" in capsys.readouterr().err
