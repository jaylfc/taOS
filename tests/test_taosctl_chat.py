"""Tests for `taosctl chat`: each verb hits the right method, path, query
params and JSON body on the chat HTTP routes."""
from __future__ import annotations

import pytest

from tinyagentos.cli.taosctl import __main__ as cli_main
from tinyagentos.cli.taosctl.commands import iter_noun_modules


class _FakeClient:
    def __init__(self):
        self.calls = []
        self.base_url = "http://x"
        self.token = "t"

    def get(self, path, params=None):
        self.calls.append(("GET", path, params))
        return {"ok": True}

    def post(self, path, body=None, params=None, json=None):
        self.calls.append(("POST", path, body))
        return {"ok": True}

    def put(self, path, body=None, json=None):
        self.calls.append(("PUT", path, body))
        return {"ok": True}

    def patch(self, path, body=None, json=None):
        self.calls.append(("PATCH", path, body))
        return {"ok": True}

    def delete(self, path, params=None):
        self.calls.append(("DELETE", path, params))
        return {"ok": True}


def _run(monkeypatch, argv):
    fake = _FakeClient()
    monkeypatch.setattr(cli_main, "TaosClient", lambda **k: fake)
    rc = cli_main.main(["--json", "chat", *argv])
    assert rc == 0
    assert len(fake.calls) == 1
    return fake.calls[0]


def test_chat_noun_is_discovered():
    assert "chat" in {m.NOUN for m in iter_noun_modules()}


# ---- channels ----------------------------------------------------------------

def test_channels_list_defaults(monkeypatch):
    method, path, params = _run(monkeypatch, ["channels"])
    assert (method, path) == ("GET", "/api/chat/channels")
    assert params == {"member": None, "archived": None, "project_id": None}


def test_channels_list_filters(monkeypatch):
    method, path, params = _run(
        monkeypatch, ["channels", "--member", "alpha", "--archived", "--project", "prj-1"])
    assert (method, path) == ("GET", "/api/chat/channels")
    assert params == {"member": "alpha", "archived": "true", "project_id": "prj-1"}


def test_channels_list_active_sends_archived_false(monkeypatch):
    _, _, params = _run(monkeypatch, ["channels", "--active"])
    assert params["archived"] == "false"


def test_channel_get_url_encodes_id(monkeypatch):
    method, path, _ = _run(monkeypatch, ["channel", "a/b c"])
    assert (method, path) == ("GET", "/api/chat/channels/a%2Fb%20c")


def test_create_channel_body(monkeypatch):
    method, path, body = _run(monkeypatch, [
        "create-channel", "general", "--type", "group", "--topic", "hi",
        "--member", "alpha", "--member", "user", "--project", "prj-1",
    ])
    assert (method, path) == ("POST", "/api/chat/channels")
    assert body == {"name": "general", "type": "group", "topic": "hi",
                    "members": ["alpha", "user"], "project_id": "prj-1"}


def test_create_channel_minimal_body_only_name(monkeypatch):
    _, _, body = _run(monkeypatch, ["create-channel", "general"])
    assert body == {"name": "general"}


def test_update_channel_patches_only_given_fields(monkeypatch):
    method, path, body = _run(monkeypatch, [
        "update-channel", "ch1", "--name", "renamed", "--max-hops", "3",
    ])
    assert (method, path) == ("PATCH", "/api/chat/channels/ch1")
    assert body == {"name": "renamed", "max_hops": 3}


def test_delete_channel(monkeypatch):
    method, path, _ = _run(monkeypatch, ["delete-channel", "ch1"])
    assert (method, path) == ("DELETE", "/api/chat/channels/ch1")


@pytest.mark.parametrize("verb,suffix", [("members", "members"), ("mute", "muted")])
def test_member_and_mute_bodies(monkeypatch, verb, suffix):
    method, path, body = _run(monkeypatch, [verb, "ch1", "add", "alpha"])
    assert (method, path) == ("POST", f"/api/chat/channels/ch1/{suffix}")
    assert body == {"action": "add", "slug": "alpha"}


def test_members_rejects_unknown_action(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(cli_main, "TaosClient", lambda **k: fake)
    with pytest.raises(SystemExit):
        cli_main.main(["chat", "members", "ch1", "kick", "alpha"])
    assert fake.calls == []


# ---- reading -----------------------------------------------------------------

def test_messages_with_limit_and_before(monkeypatch):
    method, path, params = _run(
        monkeypatch, ["messages", "ch1", "--limit", "10", "--before", "123.5"])
    assert (method, path) == ("GET", "/api/chat/channels/ch1/messages")
    assert params == {"limit": 10, "before": 123.5}


def test_message_get(monkeypatch):
    method, path, _ = _run(monkeypatch, ["message", "m1"])
    assert (method, path) == ("GET", "/api/chat/messages/m1")


def test_threads_and_thread(monkeypatch):
    method, path, _ = _run(monkeypatch, ["threads", "ch1"])
    assert (method, path) == ("GET", "/api/chat/channels/ch1/threads")
    method, path, params = _run(monkeypatch, ["thread", "ch1", "m1", "--limit", "5"])
    assert (method, path) == ("GET", "/api/chat/channels/ch1/threads/m1/messages")
    assert params == {"limit": 5}


def test_pins(monkeypatch):
    method, path, _ = _run(monkeypatch, ["pins", "ch1"])
    assert (method, path) == ("GET", "/api/chat/channels/ch1/pins")


def test_search(monkeypatch):
    method, path, params = _run(monkeypatch, ["search", "hello", "--channel", "ch1"])
    assert (method, path) == ("GET", "/api/chat/search")
    assert params == {"q": "hello", "channel_id": "ch1", "limit": None}


def test_unread(monkeypatch):
    method, path, _ = _run(monkeypatch, ["unread"])
    assert (method, path) == ("GET", "/api/chat/unread")


def test_mark_read_with_and_without_message(monkeypatch):
    method, path, body = _run(monkeypatch, ["mark-read", "ch1"])
    assert (method, path, body) == ("POST", "/api/chat/channels/ch1/mark-read", {})
    _, _, body = _run(monkeypatch, ["mark-read", "ch1", "--message-id", "m9"])
    assert body == {"message_id": "m9"}


# ---- writing -----------------------------------------------------------------

def test_send_default_author(monkeypatch):
    method, path, body = _run(monkeypatch, ["send", "ch1", "hello there"])
    assert (method, path) == ("POST", "/api/chat/messages")
    assert body == {"channel_id": "ch1", "content": "hello there", "author_id": "user"}


def test_send_in_thread_as_agent(monkeypatch):
    _, _, body = _run(monkeypatch, [
        "send", "ch1", "reply", "--author", "alpha", "--author-type", "agent",
        "--thread", "m1",
    ])
    assert body == {"channel_id": "ch1", "content": "reply", "author_id": "alpha",
                    "author_type": "agent", "thread_id": "m1"}


def test_edit(monkeypatch):
    method, path, body = _run(monkeypatch, ["edit", "m1", "fixed"])
    assert (method, path, body) == ("PATCH", "/api/chat/messages/m1", {"content": "fixed"})


def test_delete(monkeypatch):
    method, path, _ = _run(monkeypatch, ["delete", "m1"])
    assert (method, path) == ("DELETE", "/api/chat/messages/m1")


def test_pin_and_unpin(monkeypatch):
    method, path, _ = _run(monkeypatch, ["pin", "m1"])
    assert (method, path) == ("POST", "/api/chat/messages/m1/pin")
    method, path, _ = _run(monkeypatch, ["unpin", "m1"])
    assert (method, path) == ("DELETE", "/api/chat/messages/m1/pin")


def test_react(monkeypatch):
    method, path, body = _run(monkeypatch, ["react", "m1", "+1", "--author", "alpha"])
    assert (method, path) == ("POST", "/api/chat/messages/m1/reactions")
    assert body == {"emoji": "+1", "author_id": "alpha"}


def test_unreact_encodes_emoji_and_sends_author_query(monkeypatch):
    method, path, params = _run(monkeypatch, ["unreact", "m1", "\U0001F44D"])
    assert method == "DELETE"
    assert path == "/api/chat/messages/m1/reactions/%F0%9F%91%8D"
    assert params == {"author_id": "user"}
