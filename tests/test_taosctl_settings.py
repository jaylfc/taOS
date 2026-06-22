"""Tests for taosctl settings command group: dispatch, endpoint paths, and
argument wiring."""
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
        return {"ok": True}

    def post(self, path, body=None, params=None, json=None):
        payload = body if body is not None else json
        self.calls.append(("POST", path, payload))
        return {"ok": True}

    def put(self, path, body=None, json=None):
        payload = body if body is not None else json
        self.calls.append(("PUT", path, payload))
        return {"ok": True}

    def delete(self, path, params=None):
        self.calls.append(("DELETE", path))
        return {"ok": True}


def _run(monkeypatch, argv, fake):
    monkeypatch.setattr(cli_main, "TaosClient", lambda **k: fake)
    return cli_main.main(argv)


def test_storage_hits_correct_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["settings", "storage"], fake)
    assert rc == 0
    assert ("GET", "/api/settings/storage") in fake.calls


def test_llm_proxy_hits_correct_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["settings", "llm-proxy"], fake)
    assert rc == 0
    assert ("GET", "/api/settings/llm-proxy") in fake.calls


def test_backup_schedule_hits_correct_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["settings", "backup-schedule"], fake)
    assert rc == 0
    assert ("GET", "/api/settings/backup-schedule") in fake.calls


def test_set_backup_schedule_sends_frequency(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["settings", "set-backup-schedule", "daily"], fake)
    assert rc == 0
    assert ("PUT", "/api/settings/backup-schedule", {"frequency": "daily"}) in fake.calls


def test_webhooks_hits_correct_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["settings", "webhooks"], fake)
    assert rc == 0
    assert ("GET", "/api/settings/webhooks") in fake.calls


def test_add_webhook_sends_body(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["settings", "add-webhook", "--url", "https://hook.example.com", "--type", "generic"], fake)
    assert rc == 0
    assert ("POST", "/api/settings/webhooks", {"url": "https://hook.example.com", "type": "generic"}) in fake.calls


def test_add_webhook_includes_optional_fields(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["settings", "add-webhook", "--url", "https://t.me/bot", "--type", "telegram", "--bot-token", "tok123", "--chat-id", "chat456"], fake)
    assert rc == 0
    call = next(c for c in fake.calls if c[0] == "POST")
    assert call[2] == {"url": "https://t.me/bot", "type": "telegram", "bot_token": "tok123", "chat_id": "chat456"}


def test_remove_webhook_hits_correct_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["settings", "remove-webhook", "2"], fake)
    assert rc == 0
    assert ("DELETE", "/api/settings/webhooks/2") in fake.calls


def test_notification_prefs_hits_correct_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["settings", "notification-prefs"], fake)
    assert rc == 0
    assert ("GET", "/api/settings/notification-prefs") in fake.calls


def test_container_runtime_hits_correct_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["settings", "container-runtime"], fake)
    assert rc == 0
    assert ("GET", "/api/settings/container-runtime") in fake.calls


def test_set_container_runtime_sends_runtime(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["settings", "set-container-runtime", "docker"], fake)
    assert rc == 0
    assert ("PUT", "/api/settings/container-runtime", {"runtime": "docker"}) in fake.calls


def test_branches_hits_correct_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["settings", "branches"], fake)
    assert rc == 0
    assert ("GET", "/api/settings/branches") in fake.calls


def test_update_check_hits_correct_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["settings", "update-check"], fake)
    assert rc == 0
    assert ("GET", "/api/settings/update-check") in fake.calls


def test_update_status_hits_correct_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["settings", "update-status"], fake)
    assert rc == 0
    assert ("GET", "/api/settings/update-status") in fake.calls


def test_set_platform_sends_body(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["settings", "set-platform", "--poll-interval", "30", "--retention-days", "7"], fake)
    assert rc == 0
    assert ("PUT", "/api/settings/platform", {"poll_interval": 30, "retention_days": 7}) in fake.calls


def test_set_platform_includes_catalog_repo(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["settings", "set-platform", "--poll-interval", "60", "--retention-days", "14", "--catalog-repo", "https://github.com/org/repo"], fake)
    assert rc == 0
    call = next(c for c in fake.calls if c[0] == "PUT" and "platform" in c[1])
    assert call[2]["catalog_repo"] == "https://github.com/org/repo"


def test_config_hits_correct_endpoint(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["settings", "config"], fake)
    assert rc == 0
    assert ("GET", "/api/config") in fake.calls


def test_set_config_sends_yaml(monkeypatch):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["settings", "set-config", "--yaml", "server:\n  port: 6969"], fake)
    assert rc == 0
    assert ("PUT", "/api/config", {"yaml": "server:\n  port: 6969"}) in fake.calls
