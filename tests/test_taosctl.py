"""Tests for the taosctl CLI: config resolution, error mapping, command
dispatch, exit codes, and output rendering."""
from __future__ import annotations

import json

import pytest

from tinyagentos.cli.taosctl import client as cli_client
from tinyagentos.cli.taosctl import output
from tinyagentos.cli.taosctl import __main__ as cli_main
from tinyagentos.cli.taosctl.commands import iter_noun_modules


# ---- config resolution -------------------------------------------------------

def test_resolve_prefers_flags_over_env(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_client, "CONFIG_PATH", tmp_path / "none.json")
    monkeypatch.setenv("TAOS_URL", "http://env:1")
    monkeypatch.setenv("TAOS_TOKEN", "envtok")
    url, tok = cli_client.resolve("http://flag:2", "flagtok")
    assert url == "http://flag:2" and tok == "flagtok"


def test_resolve_falls_back_to_env_then_default(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_client, "CONFIG_PATH", tmp_path / "none.json")
    monkeypatch.delenv("TAOS_URL", raising=False)
    monkeypatch.setenv("TAOS_TOKEN", "envtok")
    url, tok = cli_client.resolve(None, None)
    assert url == cli_client.DEFAULT_URL and tok == "envtok"


def test_resolve_reads_config_file_when_no_flag_or_env(monkeypatch, tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"url": "http://cfg:9", "token": "cfgtok"}))
    monkeypatch.setattr(cli_client, "CONFIG_PATH", cfg)
    monkeypatch.delenv("TAOS_URL", raising=False)
    monkeypatch.delenv("TAOS_TOKEN", raising=False)
    url, tok = cli_client.resolve(None, None)
    assert url == "http://cfg:9" and tok == "cfgtok"


# ---- error extraction --------------------------------------------------------

def test_extract_error_uses_json_detail():
    raw = json.dumps({"detail": "not your project"}).encode()
    assert cli_client._extract_error(raw, 403) == "not your project"


def test_extract_error_falls_back_to_status():
    assert cli_client._extract_error(b"", 500) == "HTTP 500"


# ---- discovery ---------------------------------------------------------------

def test_agents_and_auth_nouns_are_discovered():
    nouns = {m.NOUN for m in iter_noun_modules()}
    assert {"agents", "auth"} <= nouns


# ---- command dispatch + exit codes (fake client) -----------------------------

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
        return {"items": [{"name": "alpha", "status": "running"}]}


def _run(monkeypatch, argv, fake):
    monkeypatch.setattr(cli_main, "TaosClient", lambda **k: fake)
    return cli_main.main(argv)


def test_agents_list_calls_endpoint_and_succeeds(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["agents", "list"], fake)
    assert rc == 0
    assert ("GET", "/api/agents") in fake.calls
    assert "alpha" in capsys.readouterr().out


def test_agents_get_targets_named_agent(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["--json", "agents", "get", "alpha"], fake)
    assert rc == 0
    assert ("GET", "/api/agents/alpha") in fake.calls


def test_agents_get_url_encodes_the_name(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["--json", "agents", "get", "a/b c"], fake)
    assert rc == 0
    assert ("GET", "/api/agents/a%2Fb%20c") in fake.calls


def test_api_error_maps_to_exit_2(monkeypatch, capsys):
    fake = _FakeClient()
    fake._raise = cli_client.ApiError(404, "no such agent")
    rc = _run(monkeypatch, ["agents", "get", "ghost"], fake)
    assert rc == 2
    assert "no such agent" in capsys.readouterr().err


def test_transport_error_maps_to_exit_1(monkeypatch, capsys):
    fake = _FakeClient()
    fake._raise = cli_client.TransportError("cannot reach http://x: refused")
    rc = _run(monkeypatch, ["agents", "list"], fake)
    assert rc == 1
    assert "cannot reach" in capsys.readouterr().err


# ---- output rendering --------------------------------------------------------

def test_render_json_emits_valid_json(capsys):
    output.render({"a": 1, "b": [1, 2]}, as_json=True)
    assert json.loads(capsys.readouterr().out) == {"a": 1, "b": [1, 2]}


def test_render_table_unwraps_items_and_shows_rows(capsys):
    output.render({"items": [{"name": "x", "status": "ok"}]}, as_json=False)
    out = capsys.readouterr().out
    assert "NAME" in out and "x" in out and "ok" in out


# ---- auth login writes the credential file owner-only ------------------------

def test_auth_login_writes_config_owner_only(monkeypatch, tmp_path):
    import argparse
    import stat

    from tinyagentos.cli.taosctl.commands import auth as auth_cmd

    cfg = tmp_path / "sub" / "config.json"
    monkeypatch.setattr(cli_client, "CONFIG_PATH", cfg)
    monkeypatch.delenv("TAOS_URL", raising=False)
    monkeypatch.delenv("TAOS_TOKEN", raising=False)
    auth_cmd._login(argparse.Namespace(url="http://h:1", token="sekret"), None)
    assert cfg.exists()
    assert stat.S_IMODE(cfg.stat().st_mode) == 0o600
    assert json.loads(cfg.read_text())["token"] == "sekret"


def test_auth_login_does_not_persist_env_only_token(monkeypatch, tmp_path):
    import argparse

    from tinyagentos.cli.taosctl.commands import auth as auth_cmd

    cfg = tmp_path / "config.json"
    monkeypatch.setattr(cli_client, "CONFIG_PATH", cfg)
    monkeypatch.setenv("TAOS_TOKEN", "env-secret")
    monkeypatch.delenv("TAOS_URL", raising=False)
    # bare `auth login` (no --token): the env token must NOT be written to disk
    auth_cmd._login(argparse.Namespace(url="http://h:1", token=None), None)
    saved = json.loads(cfg.read_text())
    assert saved["token"] is None
    assert saved["url"] == "http://h:1"
