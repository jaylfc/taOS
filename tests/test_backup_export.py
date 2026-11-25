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
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/123456789012345678/discord-webhook-token-abcdef"
SLACK_WEBHOOK_URL = "https://hooks.slack.com/services/T00000000/B00000000/slack-webhook-token-abcdef"
GENERIC_WEBHOOK_URL = "https://hooks.example.com/notify/hook-secret-token-abcdef"
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


class TestExportHardening:
    # jaylfc's pre-merge points on PR #4560 (comment 7172175976), points 1-3
    # at the sanitize layer.

    def test_webhook_urls_redacted_wholesale(self):
        redactions: list[dict] = []
        data = {"webhooks": [
            {"type": "discord", "url": DISCORD_WEBHOOK_URL},
            {"type": "slack", "url": SLACK_WEBHOOK_URL},
            {"type": "generic", "url": GENERIC_WEBHOOK_URL},
        ]}
        out = sanitize_for_export(data, "config", redactions, known_values=[])
        assert [e["url"] for e in out["webhooks"]] == ["[REDACTED]"] * 3
        assert [e["type"] for e in out["webhooks"]] == ["discord", "slack", "generic"]
        paths = {r["path"] for r in redactions}
        assert "config.webhooks[0].url" in paths
        assert "config.webhooks[1].url" in paths
        assert "config.webhooks[2].url" in paths

    def test_webhook_url_shape_masked_in_free_text(self):
        redactions: list[dict] = []
        out = sanitize_for_export(
            {"note": f"alerts go to {DISCORD_WEBHOOK_URL} and {SLACK_WEBHOOK_URL}"},
            "config", redactions, known_values=[],
        )
        assert "discord-webhook-token-abcdef" not in out["note"]
        assert "slack-webhook-token-abcdef" not in out["note"]
        # Host stays readable, like connection-string masking keeps the host.
        assert "discord.com" in out["note"]
        assert "hooks.slack.com" in out["note"]
        assert redactions

    def test_whole_word_key_pin_hash_salt_redacted(self):
        data = {
            "FOO_KEY": "foo-key-value",
            "MY_PIN": "123456",
            "pin_hash": "pin-hash-value",
            "session_hash": "session-hash-value",
            "password_salt": "salt-value",
        }
        out = sanitize_for_export(data, "config", [], known_values=[])
        for k in data:
            assert out[k] == "[REDACTED]", k

    def test_substring_lookalikes_not_redacted(self):
        # Whole-word matching is the guard: these contain key/pin/hash as
        # substrings of longer words and must survive untouched.
        data = {
            "keyboard_layout": "us",
            "monkey_name": "george",
            "keylime_pie": "dessert",
            "hashbrown_style": "crispy",
            "pinned_view": "home",
        }
        out = sanitize_for_export(data, "config", [], known_values=[])
        assert out == data

    def test_absolute_paths_relativised_to_data_dir(self):
        redactions: list[dict] = []
        data = {
            "taosmd_dir": "/srv/taos/taosmd",
            "agents": [{"name": "a", "workspace": "/srv/taos/agents/a"}],
        }
        out = sanitize_for_export(
            data, "config", redactions, known_values=[], data_dir="/srv/taos"
        )
        assert out["taosmd_dir"] == "taosmd"
        assert out["agents"][0]["workspace"] == "agents/a"
        paths = {r["path"] for r in redactions}
        assert "config.taosmd_dir" in paths
        assert "config.agents[0].workspace" in paths

    def test_absolute_path_outside_data_dir_stripped_to_basename(self):
        redactions: list[dict] = []
        data = {"agents": [{"name": "a", "workspace": "/home/someone/private/ws"}]}
        out = sanitize_for_export(
            data, "agents", redactions, known_values=[], data_dir="/srv/taos"
        )
        assert out["agents"][0]["workspace"] == "ws"
        assert "/home/someone" not in json.dumps(out)
        assert redactions and redactions[0]["path"] == "agents.agents[0].workspace"

    def test_relative_paths_untouched(self):
        data = {"taosmd_dir": "taosmd", "workspace": "agents/a"}
        out = sanitize_for_export(
            data, "config", [], known_values=[], data_dir="/srv/taos"
        )
        assert out == data


@pytest.mark.asyncio
class TestBuildExportBundleHardening:
    async def test_webhook_urls_absent_from_bundle_bytes(self, tmp_path):
        config = AppConfig(
            webhooks=[
                {"type": "discord", "url": DISCORD_WEBHOOK_URL},
                {"type": "slack", "url": SLACK_WEBHOOK_URL},
            ],
            config_path=tmp_path / "config.yaml",
        )
        bundle = await build_export_bundle(config=config)
        raw = json.dumps(bundle)
        assert DISCORD_WEBHOOK_URL not in raw
        assert SLACK_WEBHOOK_URL not in raw
        assert "discord-webhook-token-abcdef" not in raw
        assert "slack-webhook-token-abcdef" not in raw
        hooks = bundle["config"]["webhooks"]
        assert [h["url"] for h in hooks] == ["[REDACTED]", "[REDACTED]"]
        assert [h["type"] for h in hooks] == ["discord", "slack"]

    async def test_absolute_paths_not_verbatim_and_recorded(self, tmp_path):
        config = AppConfig(
            agents=[{"name": "agent-one", "workspace": "/home/someone/private/ws"}],
            taosmd_dir=str(tmp_path / "taosmd-data"),
            config_path=tmp_path / "config.yaml",
        )
        bundle = await build_export_bundle(config=config)
        raw = json.dumps(bundle)
        assert str(tmp_path / "taosmd-data") not in raw
        assert "/home/someone/private/ws" not in raw
        assert bundle["config"]["taosmd_dir"] == "taosmd-data"
        assert bundle["agents"]["active"][0]["workspace"] == "ws"
        paths = {e["path"] for e in bundle["manifest"]["excluded"]}
        assert "config.taosmd_dir" in paths
        assert "agents.active[0].workspace" in paths

    async def test_manifest_marks_all_users_data(self, tmp_path):
        config = AppConfig(config_path=tmp_path / "config.yaml")
        bundle = await build_export_bundle(config=config)
        assert bundle["manifest"]["contains_all_users_data"] is True


@pytest.mark.asyncio
class TestExportSizeCap:
    async def test_serialize_raises_over_cap(self, monkeypatch):
        from tinyagentos import backup_export

        monkeypatch.setattr(backup_export, "MAX_EXPORT_BYTES", 8, raising=False)
        with pytest.raises(backup_export.ExportTooLargeError):
            backup_export.serialize_export_bundle({"manifest": {"format": "x"}})

    async def test_route_returns_413_over_cap(self, client, app, monkeypatch):
        await app.state.agent_registry.init()
        await app.state.desktop_settings.init()
        monkeypatch.setattr(
            "tinyagentos.backup_export.MAX_EXPORT_BYTES", 8, raising=False
        )
        resp = await client.get("/api/settings/export")
        assert resp.status_code == 413
        assert "error" in resp.json()


@pytest.mark.asyncio
class TestExportScrubCompleteness:
    # CodeRabbit review 6064158878 on PR #4560: three spots where secret
    # material bypassed the scrub entirely.

    def test_mapping_keys_containing_secrets_are_scrubbed(self):
        redactions: list[dict] = []
        data = {"lookup": {STORE_SECRET: "value", "ordinary": "keep"}}
        out = sanitize_for_export(
            data, "config", redactions, known_values=[STORE_SECRET]
        )
        assert STORE_SECRET not in json.dumps(out)
        # Redaction paths are built from key names; they must not leak
        # the secret into manifest.excluded either.
        assert STORE_SECRET not in json.dumps(redactions)
        assert out["lookup"]["ordinary"] == "keep"

    def test_rewritten_path_result_is_scrubbed(self):
        # The basename of an outside-data-dir workspace can itself be a
        # stored secret; the rewrite branch must not emit it raw.
        data = {"agents": [{"name": "a", "workspace": f"/home/admin/{STORE_SECRET}"}]}
        out = sanitize_for_export(
            data, "agents", [], known_values=[STORE_SECRET], data_dir="/srv/taos"
        )
        assert STORE_SECRET not in json.dumps(out)
        assert out["agents"][0]["workspace"] == "[REDACTED]"

    async def test_secret_reference_description_scrubbed(self, tmp_path):
        config = AppConfig(config_path=tmp_path / "config.yaml")
        secrets = SecretsStore(tmp_path / "secrets.db")
        await secrets.init()
        await secrets.add(
            "PROVIDER_KEY", STORE_SECRET, category="api-keys",
            description=f"the value is {STORE_SECRET} ok", agents=["agent-one"],
        )
        bundle = await build_export_bundle(config=config, secrets=secrets)
        await secrets.close()
        assert STORE_SECRET not in json.dumps(bundle)
        ref = bundle["secret_references"][0]
        assert ref["name"] == "PROVIDER_KEY"
        assert ref["category"] == "api-keys"
        assert ref["description"] == "[REDACTED]"



class TestExportFollowups:
    # Post-merge follow-ups to PR #4560, promised in comment 7174241555:
    # jaylfc's two nits (comment 7173541194) and CodeRabbit's sanitised-key
    # collision case (in-thread follow-up 4700327493).

    def test_path_shaped_keys_rewritten_relative_to_data_dir(self):
        # A mapping KEY that is itself an absolute path gets the same
        # treatment as a path value: relativised under the data dir, and
        # the rewrite recorded in the manifest.
        redactions: list[dict] = []
        data = {"profiles": {"/srv/taos/agents/a": {"model": "m1"}}}
        out = sanitize_for_export(
            data, "config", redactions, known_values=[], data_dir="/srv/taos"
        )
        assert out == {"profiles": {"agents/a": {"model": "m1"}}}
        assert "/srv/taos/agents/a" not in json.dumps(out)
        paths = {r["path"] for r in redactions}
        assert "config.profiles.agents/a" in paths

    def test_path_shaped_key_outside_data_dir_reduced_to_basename(self):
        redactions: list[dict] = []
        data = {"/home/someone/private/ws": {"model": "m1"}}
        out = sanitize_for_export(
            data, "config", redactions, known_values=[], data_dir="/srv/taos"
        )
        assert out == {"ws": {"model": "m1"}}
        assert "/home/someone" not in json.dumps(out)
        assert redactions

    def test_secret_bearing_path_key_scrubbed_before_rewrite(self):
        # A key that is both a path and secret-bearing: the credential
        # scrub runs first, so no secret material survives in either the
        # key or the redaction paths, and the masked remainder is still
        # treated as a path.
        redactions: list[dict] = []
        data = {f"/srv/taos/{SHAPE_VALUE}": "v", f"/home/admin/{STORE_SECRET}": "w"}
        out = sanitize_for_export(
            data, "config", redactions,
            known_values=[STORE_SECRET], data_dir="/srv/taos",
        )
        raw = json.dumps(out) + json.dumps(redactions)
        assert SHAPE_VALUE not in raw
        assert STORE_SECRET not in raw
        # The second key's value is redacted in its own right: the key
        # tokenises as secret-bearing (rule 2), collision or not.
        assert out == {"[REDACTED]": "v", "[REDACTED] (2)": "[REDACTED]"}

    def test_env_section_allow_list(self):
        # Jay's case: an env var with an innocent name keeps its value
        # through the denylist. Inside an ``env`` section only allow-listed
        # names (locale / timezone / terminal presentation vars) survive;
        # everything else is redacted and recorded.
        redactions: list[dict] = []
        data = {"agents": [{"name": "a", "env": {
            "INNOCENT_NAME": "innocent-name-secret-value",
            "FOO_KEY": "foo-key-value",
            "TZ": "Europe/Oslo",
            "LANG": "en_US.UTF-8",
            "LC_ALL": "C.UTF-8",
        }}]}
        out = sanitize_for_export(data, "config", redactions, known_values=[])
        env = out["agents"][0]["env"]
        assert env["INNOCENT_NAME"] == "[REDACTED]"
        assert env["FOO_KEY"] == "[REDACTED]"
        assert env["TZ"] == "Europe/Oslo"
        assert env["LANG"] == "en_US.UTF-8"
        assert env["LC_ALL"] == "C.UTF-8"
        assert "innocent-name-secret-value" not in json.dumps(out)
        paths = {r["path"] for r in redactions}
        assert "config.agents[0].env.INNOCENT_NAME" in paths

    def test_env_allow_list_applies_at_any_depth(self):
        # MCP-server-shaped config: env nested under a server's config.
        data = {"servers": [{"config": {"env": {
            "CUSTOM_FLAG": "custom-secret-value", "TERM": "xterm-256color",
        }}}]}
        out = sanitize_for_export(data, "config", [], known_values=[])
        env = out["servers"][0]["config"]["env"]
        assert env["CUSTOM_FLAG"] == "[REDACTED]"
        assert env["TERM"] == "xterm-256color"

    def test_sanitised_key_collision_keeps_both_entries(self):
        # CodeRabbit 4700327493's shape: a literal "[REDACTED]" key plus a
        # key that sanitises to "[REDACTED]". Before the fix the second
        # write silently overwrote the first and "keep" was lost. (The
        # second entry's value is redacted in its own right -- its key
        # tokenises as secret-bearing -- but the entry itself survives.)
        redactions: list[dict] = []
        data = {"[REDACTED]": "keep", f"lookup-{STORE_SECRET}": "second-value"}
        out = sanitize_for_export(
            data, "config", redactions, known_values=[STORE_SECRET]
        )
        assert out == {"[REDACTED]": "keep", "[REDACTED] (2)": "[REDACTED]"}
        assert any("collid" in r["reason"] for r in redactions)

    def test_collision_with_plain_values_keeps_both(self):
        # Same collision, but the sanitising key is not secret-bearing
        # (the known value tokenises innocently), so both values survive
        # verbatim under the plain and suffixed keys.
        known = "quuxly-wibble-12345"
        data = {"[REDACTED]": "keep", f"lookup-{known}": "second-value"}
        out = sanitize_for_export(data, "config", [], known_values=[known])
        assert out == {"[REDACTED]": "keep", "[REDACTED] (2)": "second-value"}

    def test_two_secret_keys_colliding_both_kept(self):
        redactions: list[dict] = []
        data = {f"a-{STORE_SECRET}": "v1", f"b-{STORE_SECRET}": "v2"}
        out = sanitize_for_export(
            data, "config", redactions, known_values=[STORE_SECRET]
        )
        assert out == {"[REDACTED]": "[REDACTED]", "[REDACTED] (2)": "[REDACTED]"}
        assert STORE_SECRET not in json.dumps(out)


@pytest.mark.asyncio
class TestBuildExportBundleFollowups:
    async def test_agent_env_innocent_name_redacted_in_bundle(self, tmp_path):
        config = AppConfig(
            agents=[{"name": "agent-one", "env": {
                "INNOCENT_NAME": "innocent-name-secret-value",
                "TZ": "Europe/Oslo",
            }}],
            config_path=tmp_path / "config.yaml",
        )
        bundle = await build_export_bundle(config=config)
        raw = json.dumps(bundle)
        assert "innocent-name-secret-value" not in raw
        env = bundle["agents"]["active"][0]["env"]
        assert env["INNOCENT_NAME"] == "[REDACTED]"
        assert env["TZ"] == "Europe/Oslo"
        paths = {e["path"] for e in bundle["manifest"]["excluded"]}
        assert "agents.active[0].env.INNOCENT_NAME" in paths
