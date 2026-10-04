"""Tests for the taosctl channels noun: each verb hits the right method,
endpoint path and (for writes) JSON body."""
from __future__ import annotations

import pytest

from tinyagentos.cli.taosctl import __main__ as cli_main
from tinyagentos.cli.taosctl.commands import iter_noun_modules


class _FakeClient:
    def __init__(self, *a, **k):
        self.calls = []
        self.base_url = "http://x"
        self.token = "t"

    def get(self, path, params=None):
        self.calls.append(("GET", path))
        return [{"id": 1, "agent_name": "alpha", "type": "telegram"}]

    def post(self, path, body=None, params=None, json=None):
        self.calls.append(("POST", path, body))
        return {"status": "ok"}

    def delete(self, path, params=None):
        self.calls.append(("DELETE", path))
        return {"status": "removed"}


def _run(monkeypatch, argv, fake):
    monkeypatch.setattr(cli_main, "TaosClient", lambda **k: fake)
    return cli_main.main(argv)


def test_channels_noun_is_discovered():
    assert "channels" in {m.NOUN for m in iter_noun_modules()}


def test_list_all_channels(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["--json", "channels", "list"], fake)
    assert rc == 0
    assert fake.calls == [("GET", "/api/channels")]
    assert "telegram" in capsys.readouterr().out


def test_list_for_agent_url_encodes_name(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["--json", "channels", "list", "--agent", "a/b c"], fake)
    assert rc == 0
    assert fake.calls == [("GET", "/api/channels/agent/a%2Fb%20c")]


def test_types(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["--json", "channels", "types"], fake)
    assert rc == 0
    assert fake.calls == [("GET", "/api/channels/types")]


def test_create_without_config(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch,
              ["--json", "channels", "create", "--agent", "alpha", "--type", "telegram"],
              fake)
    assert rc == 0
    assert fake.calls == [("POST", "/api/channels",
                           {"agent_name": "alpha", "type": "telegram"})]


def test_create_with_json_config(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch,
              ["--json", "channels", "create", "--agent", "alpha", "--type", "discord",
               "--config", '{"token": "abc", "guild": 7}'],
              fake)
    assert rc == 0
    assert fake.calls == [("POST", "/api/channels",
                           {"agent_name": "alpha", "type": "discord",
                            "config": {"token": "abc", "guild": 7}})]


@pytest.mark.parametrize("bad", ["not json", "[1, 2]"])
def test_create_rejects_bad_config_before_any_call(monkeypatch, capsys, bad):
    fake = _FakeClient()
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch,
             ["channels", "create", "--agent", "alpha", "--type", "x", "--config", bad],
             fake)
    assert exc.value.code == 2
    assert fake.calls == []


def test_delete_url_encodes_both_segments(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["--json", "channels", "delete", "al pha", "tele/gram"], fake)
    assert rc == 0
    assert fake.calls == [("DELETE", "/api/channels/al%20pha/tele%2Fgram")]


def test_toggle_enable(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["--json", "channels", "toggle", "5", "--enable"], fake)
    assert rc == 0
    assert fake.calls == [("POST", "/api/channels/5/toggle", {"enabled": True})]


def test_toggle_disable(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["--json", "channels", "toggle", "5", "--disable"], fake)
    assert rc == 0
    assert fake.calls == [("POST", "/api/channels/5/toggle", {"enabled": False})]


def test_toggle_requires_a_state(monkeypatch, capsys):
    fake = _FakeClient()
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, ["channels", "toggle", "5"], fake)
    assert exc.value.code == 2
    assert fake.calls == []


def test_toggle_rejects_non_integer_id(monkeypatch, capsys):
    fake = _FakeClient()
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, ["channels", "toggle", "abc", "--enable"], fake)
    assert exc.value.code == 2
    assert fake.calls == []
