"""Tests for the taosctl decisions command group."""
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
        self.calls.append(("GET", path, params))
        if self._raise:
            raise self._raise
        return {"items": []}

    def post(self, path, body=None, params=None, json=None):
        self.calls.append(("POST", path, body))
        if self._raise:
            raise self._raise
        return {"id": "d1"}


def _run(monkeypatch, argv, fake):
    monkeypatch.setattr(cli_main, "TaosClient", lambda **k: fake)
    return cli_main.main(argv)


def test_list_default_sends_limit(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["decisions", "list"], fake)
    assert rc == 0
    assert ("GET", "/api/decisions", {"limit": 200}) in fake.calls


def test_list_filters_status_and_project(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["decisions", "list", "--status", "pending",
                            "--project", "prj-1", "--limit", "5"], fake)
    assert rc == 0
    assert ("GET", "/api/decisions",
            {"limit": 5, "status": "pending", "project_id": "prj-1"}) in fake.calls


def test_list_rejects_nonpositive_limit(monkeypatch):
    fake = _FakeClient()
    with pytest.raises(SystemExit):
        _run(monkeypatch, ["decisions", "list", "--limit", "0"], fake)


def test_get_calls_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["decisions", "get", "d9"], fake)
    assert rc == 0
    assert ("GET", "/api/decisions/d9", None) in fake.calls


def test_history_calls_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["decisions", "history", "d9"], fake)
    assert rc == 0
    assert ("GET", "/api/decisions/d9/history", None) in fake.calls


def test_answer_single_value_is_a_string(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["decisions", "answer", "d9", "--value", "approve"], fake)
    assert rc == 0
    assert ("POST", "/api/decisions/d9/answer", {"value": "approve"}) in fake.calls


def test_answer_json_array_is_parsed(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["decisions", "answer", "d9", "--value", '["a","b"]',
                            "--answered-by", "jay"], fake)
    assert rc == 0
    assert ("POST", "/api/decisions/d9/answer",
            {"value": ["a", "b"], "answered_by": "jay"}) in fake.calls


def test_answer_object_like_value_stays_string(monkeypatch):
    # Only a value that parses to a JSON list is coerced; a JSON object parses
    # but is not a list, so it stays the literal string.
    fake = _FakeClient()
    rc = _run(monkeypatch, ["decisions", "answer", "d9", "--value", '{"x":1}'], fake)
    assert rc == 0
    assert ("POST", "/api/decisions/d9/answer", {"value": '{"x":1}'}) in fake.calls


def test_answer_bracketed_free_text_stays_string(monkeypatch):
    # A free-text answer that merely looks bracketed ("[hello world]") is not
    # valid JSON, so it is forwarded as the literal string, not an error.
    fake = _FakeClient()
    rc = _run(monkeypatch, ["decisions", "answer", "d9", "--value", "[hello world]"], fake)
    assert rc == 0
    assert ("POST", "/api/decisions/d9/answer",
            {"value": "[hello world]"}) in fake.calls


def test_post_minimal_free_text(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["decisions", "post", "--from-agent", "@taOS-dev",
                            "--question", "Which path?", "--type", "free_text"], fake)
    assert rc == 0
    posted = [c for c in fake.calls if c[0] == "POST" and c[1] == "/api/decisions"]
    assert posted, fake.calls
    body = posted[0][2]
    assert body["from_agent"] == "@taOS-dev"
    assert body["type"] == "free_text"
    assert body["options"] == []
    assert body["priority"] == "normal"
    # Unset optional fields are omitted, not sent as None.
    assert "project_id" not in body


def test_post_rejects_malformed_options_json(monkeypatch):
    # json_array argtype rejects bad JSON at parse time (clean error, no traceback).
    fake = _FakeClient()
    with pytest.raises(SystemExit):
        _run(monkeypatch, ["decisions", "post", "--from-agent", "@taOS-dev",
                           "--question", "Pick", "--type", "single_select",
                           "--options-json", '[{"label":"A"'], fake)


def test_post_rejects_non_array_options_json(monkeypatch):
    fake = _FakeClient()
    with pytest.raises(SystemExit):
        _run(monkeypatch, ["decisions", "post", "--from-agent", "@taOS-dev",
                           "--question", "Pick", "--type", "single_select",
                           "--options-json", '{"label":"A"}'], fake)


def test_list_rejects_unknown_status(monkeypatch):
    # `expired` is not a status the store writes, so it is not an allowed filter.
    fake = _FakeClient()
    with pytest.raises(SystemExit):
        _run(monkeypatch, ["decisions", "list", "--status", "expired"], fake)


def test_post_select_with_options_and_project(monkeypatch):
    fake = _FakeClient()
    opts = '[{"label":"A","value":"a","recommended":true,"rationale":"safest"}]'
    rc = _run(monkeypatch, ["decisions", "post", "--from-agent", "@taOS-dev",
                            "--question", "Pick", "--type", "single_select",
                            "--options-json", opts, "--project", "prj-7",
                            "--priority", "blocking"], fake)
    assert rc == 0
    body = [c for c in fake.calls if c[1] == "/api/decisions"][0][2]
    assert body["options"][0]["value"] == "a"
    assert body["project_id"] == "prj-7"
    assert body["priority"] == "blocking"


def test_api_error_maps_to_exit_2(monkeypatch, capsys):
    fake = _FakeClient()
    fake._raise = cli_client.ApiError(404, "not found")
    rc = _run(monkeypatch, ["decisions", "get", "nope"], fake)
    assert rc == 2
    assert "not found" in capsys.readouterr().err


def test_transport_error_maps_to_exit_1(monkeypatch, capsys):
    fake = _FakeClient()
    fake._raise = cli_client.TransportError("cannot reach http://x: refused")
    rc = _run(monkeypatch, ["decisions", "list"], fake)
    assert rc == 1
    assert "cannot reach" in capsys.readouterr().err
