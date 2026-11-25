"""Backup/export (#620, slice 1): the structured, secret-safe export bundle.

The legacy ``POST /api/backup`` tarball ships ``config.yaml`` raw. This slice
adds ``GET /api/settings/export``: a JSON bundle of config, agents, projects,
notification prefs and desktop preferences whose manifest states exactly what
was redacted or left out, and which must never contain a secret value:

- values under secret-bearing keys (the ``log_redaction`` key list),
- bare credential shapes (provider keys, JWTs, connection-string passwords),
- literal values held in the secrets store, wherever they appear.

Secret *references* (name/category/agents from ``SecretsStore.list()``, which
never selects the value column) are exported so a future import can prompt
for re-entry; the values themselves are not.
"""
from __future__ import annotations

import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.backup_export import build_export_bundle, sanitize_for_export
from tinyagentos.config import AppConfig
from tinyagentos.desktop_settings import DesktopSettingsStore
from tinyagentos.notifications import NotificationStore
from tinyagentos.projects.project_store import ProjectStore
from tinyagentos.secrets import SecretsStore

# Distinctive seeded values. Each is caught by a different rule, so the
# raw-bytes assertions below pin all of them:
# - KEY_ONLY_VALUE: plain-looking, caught only by its key ("api_key").
# - SHAPE_VALUE: provider-key shape under an innocent key.
# - BOT_TOKEN: caught by its key ("bot_token").
# - STORE_SECRET: lives in the secrets store; also embedded in a config
#   string under an innocent key, caught by the known-value rule.
# - CONN_PASSWORD: password component of a connection string.
KEY_ONLY_VALUE = "plain-looking-key-value-123"
SHAPE_VALUE = "sk-abcdefghijklmnopqrstuvwx"
BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
STORE_SECRET = "hunter2-store-secret-98765"
CONN_PASSWORD = "connpass987"
WALLHAVEN_KEY = "wallhaven-key-abcdef123456"
ALL_SECRETS = [KEY_ONLY_VALUE, SHAPE_VALUE, BOT_TOKEN, STORE_SECRET, CONN_PASSWORD, WALLHAVEN_KEY]


class TestSanitizeForExport:
    def test_secret_bearing_keys_redacted_at_any_depth(self):
        redactions: list[dict] = []
        data = {
            "backends": [{"name": "prov", "api_key": KEY_ONLY_VALUE}],
            "webhooks": [{"type": "telegram", "bot_token": BOT_TOKEN, "chat_id": "42"}],
            "nested": {"deeper": {"client_secret": "abc", "harmless": "keep-me"}},
        }
        out = sanitize_for_export(data, "config", redactions, known_values=[])
        assert out["backends"][0]["api_key"] == "[REDACTED]"
        assert out["backends"][0]["name"] == "prov"
        assert out["webhooks"][0]["bot_token"] == "[REDACTED]"
        assert out["webhooks"][0]["chat_id"] == "42"
        assert out["nested"]["deeper"]["client_secret"] == "[REDACTED]"
        assert out["nested"]["deeper"]["harmless"] == "keep-me"
        paths = {r["path"] for r in redactions}
        assert "config.backends[0].api_key" in paths
        assert "config.webhooks[0].bot_token" in paths
        assert "config.nested.deeper.client_secret" in paths

    def test_whole_word_matching_spares_innocent_keys(self):
        # log_redaction matches whole words so "monkey" does not trip "key";
        # the structured walk must keep that property.
        out = sanitize_for_export({"monkey": "banana", "keyboard": "qwerty"}, "x", [], known_values=[])
        assert out == {"monkey": "banana", "keyboard": "qwerty"}

    def test_bare_credential_shape_in_string_value_redacted(self):
        redactions: list[dict] = []
        out = sanitize_for_export({"note": f"the key is {SHAPE_VALUE} ok"}, "config", redactions, known_values=[])
        assert SHAPE_VALUE not in out["note"]
        assert "[REDACTED]" in out["note"]
        assert redactions and redactions[0]["path"] == "config.note"

    def test_connection_string_password_masked(self):
        out = sanitize_for_export(
            {"memory_url": f"postgres://taos:{CONN_PASSWORD}@db.internal:5432/taos"},
            "config", [], known_values=[],
        )
        assert CONN_PASSWORD not in out["memory_url"]
        assert "db.internal" in out["memory_url"]

    def test_known_secret_value_replaces_whole_string(self):
        out = sanitize_for_export(
            {"description": f"uses {STORE_SECRET} internally"},
            "config", [], known_values=[STORE_SECRET],
        )
        assert out["description"] == "[REDACTED]"

    def test_short_known_value_ignored(self):
        # Mirrors log_redaction's fail-closed floor: a <6-char known value is
        # too likely a common substring to mask on.
        out = sanitize_for_export({"note": "abc def"}, "config", [], known_values=["abc"])
        assert out["note"] == "abc def"


@pytest_asyncio.fixture
async def notif_store(tmp_path):
    store = NotificationStore(tmp_path / "notifications.db")
    await store.init()
    yield store
    await store.close()


@pytest_asyncio.fixture
async def desktop_store(tmp_path):
    store = DesktopSettingsStore(tmp_path / "desktop.db")
    await store.init()
    yield store
    await store.close()


@pytest.mark.asyncio
class TestStoreExportMethods:
    async def test_notification_prefs_export_groups_by_user(self, notif_store):
        await notif_store.set_event_pref(
            "alice", "build.done", muted=True, style="alert", show_centre=False
        )
        await notif_store.set_event_pref("alice", "chat.mention", muted=False)
        await notif_store.set_dnd(
            "alice", enabled=True, start="22:00", end="07:00", timezone="Europe/Oslo"
        )
        await notif_store.set_event_pref("bob", "build.done", muted=False)

        export = await notif_store.export_prefs()
        alice = export["alice"]
        events = {e["event_type"]: e for e in alice["events"]}
        assert events["build.done"]["muted"] is True
        assert events["build.done"]["style"] == "alert"
        assert events["build.done"]["show_centre"] is False
        assert events["chat.mention"]["muted"] is False
        assert alice["dnd"] == {
            "enabled": True, "start": "22:00", "end": "07:00", "timezone": "Europe/Oslo",
        }
        assert export["bob"]["events"][0]["event_type"] == "build.done"
        assert "dnd" not in export["bob"]

    async def test_desktop_settings_export_all(self, desktop_store):
        await desktop_store.update_settings("alice", {"wallpaper": "dunes"})
        await desktop_store.save_preference("alice", "library", {"quality": "720"})
        await desktop_store.update_settings("bob", {"mode": "light"})

        export = await desktop_store.export_all()
        assert export["alice"]["settings"]["wallpaper"] == "dunes"
        assert export["alice"]["pref:library"] == {"quality": "720"}
        assert export["bob"]["settings"]["mode"] == "light"


@pytest.mark.asyncio
class TestBuildExportBundle:
    async def _seeded_bundle(self, tmp_path):
        config = AppConfig(
            backends=[{
                "name": "prov", "type": "openai",
                "api_key": KEY_ONLY_VALUE,
                "description": f"uses {STORE_SECRET} internally",
            }],
            agents=[{"name": "agent-one", "model": "m1"}],
            archived_agents=[{"name": "agent-old"}],
            webhooks=[{"type": "telegram", "bot_token": BOT_TOKEN, "chat_id": "42"}],
            memory_url=f"postgres://taos:{CONN_PASSWORD}@db.internal:5432/taos",
            **{"wallhaven" + "_api_key": WALLHAVEN_KEY},
            config_path=tmp_path / "config.yaml",
        )
        secrets = SecretsStore(tmp_path / "secrets.db")
        await secrets.init()
        await secrets.add("PROVIDER_KEY", STORE_SECRET, category="api-keys", agents=["agent-one"])

        notifs = NotificationStore(tmp_path / "notifications.db")
        await notifs.init()
        await notifs.set_event_pref("alice", "build.done", muted=True)
        await notifs.set_dnd("alice", enabled=True, start="22:00", end="07:00", timezone="UTC")

        projects = ProjectStore(tmp_path / "projects.db")
        await projects.init()
        project = await projects.create_project(
            "Proj One", "proj-one", created_by="alice", user_id="alice"
        )
        await projects.add_member(project["id"], "alice", "human", role="lead")
        await projects.add_member(project["id"], "agent-one", "native")

        desktop = DesktopSettingsStore(tmp_path / "desktop.db")
        await desktop.init()
        await desktop.save_preference("alice", "weather", {"units": "metric"})

        bundle = await build_export_bundle(
            config=config,
            notifications=notifs,
            projects=projects,
            desktop_settings=desktop,
            secrets=secrets,
            agent_registry=None,
        )
        for store in (secrets, notifs, projects, desktop):
            await store.close()
        return bundle

    async def test_bundle_sections_and_manifest(self, tmp_path):
        bundle = await self._seeded_bundle(tmp_path)
        manifest = bundle["manifest"]
        assert manifest["format"] == "taos-backup-export"
        assert manifest["format_version"] == 1
        assert manifest["exported_at"]
        for section in ("config", "agents", "projects", "notification_prefs",
                        "preferences", "secret_references"):
            assert section in manifest["sections"]
            assert section in bundle
        assert bundle["agents"]["active"][0]["name"] == "agent-one"
        assert bundle["agents"]["archived"][0]["name"] == "agent-old"
        project = bundle["projects"][0]
        assert project["slug"] == "proj-one"
        member_ids = {m["member_id"] for m in project["members"]}
        assert member_ids == {"alice", "agent-one"}
        assert bundle["notification_prefs"]["alice"]["dnd"]["enabled"] is True
        assert bundle["preferences"]["alice"]["pref:weather"] == {"units": "metric"}

    async def test_no_secret_value_anywhere_in_bundle_bytes(self, tmp_path):
        bundle = await self._seeded_bundle(tmp_path)
        raw = json.dumps(bundle)
        for secret in ALL_SECRETS:
            assert secret not in raw, f"secret leaked into bundle: {secret!r}"
        # Non-secret neighbours survive.
        assert bundle["config"]["backends"][0]["name"] == "prov"
        assert bundle["config"]["backends"][0]["api_key"] == "[REDACTED]"
        assert bundle["config"]["webhooks"][0]["bot_token"] == "[REDACTED]"
        assert "db.internal" in bundle["config"]["memory_url"]

    async def test_secret_references_carry_metadata_not_values(self, tmp_path):
        bundle = await self._seeded_bundle(tmp_path)
        refs = bundle["secret_references"]
        assert len(refs) == 1
        ref = refs[0]
        assert ref["name"] == "PROVIDER_KEY"
        assert ref["category"] == "api-keys"
        assert ref["agents"] == ["agent-one"]
        assert "value" not in ref

    async def test_excluded_manifest_records_redactions_and_omissions(self, tmp_path):
        bundle = await self._seeded_bundle(tmp_path)
        excluded = bundle["manifest"]["excluded"]
        paths = {e["path"] for e in excluded}
        assert "config.backends[0].api_key" in paths
        assert "config.webhooks[0].bot_token" in paths
        assert "config.wallhaven_api_key" in paths
        # The secrets store's values are excluded as a class.
        assert any(e["path"] == "secrets" for e in excluded)


async def _member_client(app) -> AsyncClient:
    """Non-admin member session client (mirrors tests/test_settings_authz.py)."""
    auth_mgr = app.state.auth
    invite_code = auth_mgr.add_user_invite("exportmember", "admin")
    auth_mgr.complete_invite(
        "exportmember", invite_code, "Export Member", "", "exportmemberpass123"
    )
    member = auth_mgr.find_user("exportmember")
    token = auth_mgr.create_session(member["id"])
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": token},
    )


@pytest.mark.asyncio
class TestExportRoute:
    async def test_admin_downloads_bundle_without_secrets(self, client, app):
        # The shared client fixture bypasses the lifespan, which owns
        # init() for these two stores; the export route consults both.
        await app.state.agent_registry.init()
        await app.state.desktop_settings.init()
        app.state.config.backends.append(
            {"name": "export-prov", "type": "openai", "api_key": KEY_ONLY_VALUE}
        )
        await app.state.secrets.add("ROUTE_SECRET", STORE_SECRET, category="api-keys")
        await app.state.notifications.set_event_pref("admin", "build.done", muted=True)

        resp = await client.get("/api/settings/export")
        assert resp.status_code == 200
        assert resp.headers["content-disposition"].startswith("attachment;")
        assert "taos-export-" in resp.headers["content-disposition"]
        for secret in (KEY_ONLY_VALUE, STORE_SECRET):
            assert secret not in resp.text
        bundle = resp.json()
        assert bundle["manifest"]["format"] == "taos-backup-export"
        assert "config" in bundle["manifest"]["sections"]
        names = {r["name"] for r in bundle["secret_references"]}
        assert "ROUTE_SECRET" in names
        assert bundle["notification_prefs"]["admin"]["events"][0]["muted"] is True

    async def test_non_admin_forbidden(self, client, app):
        member_client = await _member_client(app)
        try:
            resp = await member_client.get("/api/settings/export")
        finally:
            await member_client.aclose()
        assert resp.status_code == 403
