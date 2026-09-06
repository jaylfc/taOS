"""Tests for the taosctl apps command group."""
from __future__ import annotations

import pytest

from tinyagentos.cli.taosctl import client as cli_client
from tinyagentos.cli.taosctl import __main__ as cli_main


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
        if path == "/api/apps/installed":
            return [{"app_id": "gitea-lxc", "display_name": "Gitea", "status": "running"}]
        return {"items": []}

    def post(self, path, body=None, params=None):
        self.calls.append(("POST", path))
        if self._raise:
            raise self._raise
        return {"status": "ok"}


def _run(monkeypatch, argv, fake):
    monkeypatch.setattr(cli_main, "TaosClient", lambda **k: fake)
    return cli_main.main(argv)


def test_apps_list_calls_installed_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["apps", "list"], fake)
    assert rc == 0
    assert ("GET", "/api/apps/installed") in fake.calls


def test_apps_get_filters_installed_list_by_id(monkeypatch):
    # No single-app backend route exists, so get fetches the list and filters.
    fake = _FakeClient()
    rc = _run(monkeypatch, ["apps", "get", "gitea-lxc"], fake)
    assert rc == 0
    assert ("GET", "/api/apps/installed") in fake.calls


def test_apps_get_unknown_id_errors(monkeypatch):
    fake = _FakeClient()
    with pytest.raises(SystemExit):
        _run(monkeypatch, ["apps", "get", "ghost-app"], fake)


def test_apps_installed_calls_optional_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["apps", "installed"], fake)
    assert rc == 0
    assert ("GET", "/api/apps/optional/installed") in fake.calls


def test_apps_install_posts_to_optional_install(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["apps", "install", "reddit"], fake)
    assert rc == 0
    assert ("POST", "/api/apps/optional/reddit/install") in fake.calls


def test_apps_uninstall_posts_to_optional_uninstall(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["apps", "uninstall", "reddit"], fake)
    assert rc == 0
    assert ("POST", "/api/apps/optional/reddit/uninstall") in fake.calls


def test_apps_api_error_maps_to_exit_2(monkeypatch, capsys):
    fake = _FakeClient()
    fake._raise = cli_client.ApiError(404, "no such app")
    rc = _run(monkeypatch, ["apps", "get", "ghost"], fake)
    assert rc == 2
    assert "no such app" in capsys.readouterr().err


def test_apps_transport_error_maps_to_exit_1(monkeypatch, capsys):
    fake = _FakeClient()
    fake._raise = cli_client.TransportError("cannot reach http://x: refused")
    rc = _run(monkeypatch, ["apps", "list"], fake)
    assert rc == 1
    assert "cannot reach" in capsys.readouterr().err
