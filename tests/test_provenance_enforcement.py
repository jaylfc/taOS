"""Route-level enforcement of the provenance -> capability ceiling.

Complements tests/test_app_provenance.py (the pure ceiling table) by checking
the ceiling is actually wired into the userspace broker, the bundle CSP, and
the app_permissions consent flow, without changing behaviour for the
pre-existing first-party / community trust levels.
"""
from __future__ import annotations

import pytest


async def _init_userspace_stores(app):
    store = app.state.userspace_apps
    if store._db is not None:
        await store.close()
    await store.init()
    data_store = app.state.userspace_data
    if data_store._db is not None:
        await data_store.close()
    await data_store.init()


async def _install(store, app_id, *, trust="community", provenance=None, permissions=None):
    await store.install(
        app_id=app_id, name=app_id, version="1.0.0", app_type="web",
        entry="index.html", icon="", permissions_requested=permissions or [],
        trust=trust, provenance=provenance,
    )


# ---------------------------------------------------------------------------
# Store -- provenance column
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_install_default_provenance_is_user_uploaded(app, tmp_data_dir):
    await _init_userspace_stores(app)
    store = app.state.userspace_apps
    await _install(store, "a")
    row = await store.get("a")
    assert row["provenance"] == "user-uploaded"


@pytest.mark.asyncio
async def test_install_first_party_trust_defaults_to_first_party_provenance(app, tmp_data_dir):
    await _init_userspace_stores(app)
    store = app.state.userspace_apps
    await _install(store, "studio", trust="first-party")
    row = await store.get("studio")
    assert row["provenance"] == "first-party"


@pytest.mark.asyncio
async def test_install_explicit_provenance_is_honoured(app, tmp_data_dir):
    await _init_userspace_stores(app)
    store = app.state.userspace_apps
    await _install(store, "agent-app", provenance="ai-generated")
    row = await store.get("agent-app")
    assert row["provenance"] == "ai-generated"


@pytest.mark.asyncio
async def test_migration_backfills_provenance_from_legacy_trust(tmp_path):
    """A pre-existing DB with a trust column but no provenance column gets one
    backfilled sensibly, matching the back-compat requirement: legacy
    first-party rows classify first-party, everything else user-uploaded."""
    import aiosqlite
    from tinyagentos.userspace.store import UserspaceAppStore

    db_path = tmp_path / "legacy.db"
    async with aiosqlite.connect(str(db_path)) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS userspace_apps (
                app_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                version TEXT NOT NULL DEFAULT '',
                app_type TEXT NOT NULL,
                entry TEXT NOT NULL DEFAULT 'index.html',
                icon TEXT NOT NULL DEFAULT '',
                permissions_requested TEXT NOT NULL DEFAULT '[]',
                permissions_granted TEXT NOT NULL DEFAULT '[]',
                enabled INTEGER NOT NULL DEFAULT 1,
                installed_at INTEGER NOT NULL,
                container_host TEXT,
                container_port INTEGER,
                trust TEXT NOT NULL DEFAULT 'community'
            );
        """)
        await db.execute(
            "INSERT INTO userspace_apps "
            "(app_id, name, version, app_type, entry, icon, permissions_requested, "
            "permissions_granted, enabled, installed_at, trust) "
            "VALUES ('legacy-fp', 'FP', '1', 'web', 'index.html', '', '[]', '[]', 1, 0, 'first-party')"
        )
        await db.execute(
            "INSERT INTO userspace_apps "
            "(app_id, name, version, app_type, entry, icon, permissions_requested, "
            "permissions_granted, enabled, installed_at, trust) "
            "VALUES ('legacy-community', 'C', '1', 'web', 'index.html', '', '[]', '[]', 1, 0, 'community')"
        )
        await db.commit()

    store = UserspaceAppStore(db_path)
    await store.init()
    assert (await store.get("legacy-fp"))["provenance"] == "first-party"
    assert (await store.get("legacy-community"))["provenance"] == "user-uploaded"
    await store.close()


# ---------------------------------------------------------------------------
# Broker -- ceiling honoured per tier, existing behaviour preserved
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_broker_storage_dispatch_scope_is_unchanged_by_ceiling(app, tmp_data_dir, client):
    """Documents a deliberate scope boundary: the raw broker dispatch
    (handle_capability) still treats app.kv/app.table/app.files as always-on
    for every provenance, exactly as it did before this change, for every
    tier including ai-generated/user-uploaded. Tightening that shared,
    heavily-tested dispatch path would break existing installed-app behaviour
    that several pre-existing tests rely on (e.g. tests/userspace/test_e2e.py
    and tests/userspace/test_routes.py::test_broker_enforces_granted both
    exercise free app.kv/app.table access with no grant on a community-trust
    app). The ceiling IS enforced for this tier at the consent-request layer
    (see test_request_consent_flags_storage_for_ai_generated_app below) and
    for the gated network/agent/llm/memory namespaces at the broker layer
    (see the first-party/user-uploaded tests above); closing this last gap so
    an ai-generated app's storage calls are denied at the broker itself,
    not just flagged for consent, is a follow-up."""
    await _init_userspace_stores(app)
    store = app.state.userspace_apps
    await _install(store, "agent-app", provenance="ai-generated", permissions=["app.kv"])

    r = await client.post(
        "/api/userspace-apps/agent-app/broker",
        json={"capability": "app.kv.set", "args": {"key": "k", "value": 1}},
    )
    assert r.json()["result"] is True


@pytest.mark.asyncio
async def test_broker_first_party_still_bypasses_gated_caps(app, tmp_data_dir, client):
    """Generalising trust -> provenance must not change the existing
    first-party bypass of gated capabilities (network/agent/llm/memory)."""
    await _init_userspace_stores(app)
    store = app.state.userspace_apps
    await _install(store, "studio", trust="first-party", provenance="first-party")

    r = await client.post(
        "/api/userspace-apps/studio/broker",
        json={"capability": "app.memory.search", "args": {"q": "x"}},
    )
    assert r.json().get("error") != "permission_denied"


@pytest.mark.asyncio
async def test_broker_user_uploaded_still_needs_grant_for_gated_caps(app, tmp_data_dir, client):
    """Unchanged from the pre-existing community behaviour: gated caps always
    need an explicit grant below first-party."""
    await _init_userspace_stores(app)
    store = app.state.userspace_apps
    await _install(store, "side-loaded", provenance="user-uploaded")

    r = await client.post(
        "/api/userspace-apps/side-loaded/broker",
        json={"capability": "app.net", "args": {"path": "/ping"}},
    )
    assert r.json()["error"] == "permission_denied"


# ---------------------------------------------------------------------------
# Bundle CSP -- tightened further for the unknown tier only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_provenance_gets_a_tighter_img_src(app, tmp_data_dir, client, tmp_path):
    await _init_userspace_stores(app)
    apps_dir = tmp_path / "apps" / "mystery"
    apps_dir.mkdir(parents=True)
    (apps_dir / "index.html").write_text("<h1>mystery</h1>")

    store = app.state.userspace_apps
    await _install(store, "mystery", provenance="unknown")

    r = await client.get("/api/userspace-apps/mystery/bundle/index.html")
    assert r.status_code == 200
    csp = r.headers.get("content-security-policy", "")
    assert "img-src 'self' data: blob:" in csp
    assert "https:" not in csp.split("img-src")[1].split(";")[0]


@pytest.mark.asyncio
async def test_user_uploaded_keeps_the_existing_img_src(app, tmp_data_dir, client, tmp_path):
    await _init_userspace_stores(app)
    apps_dir = tmp_path / "apps" / "normal"
    apps_dir.mkdir(parents=True)
    (apps_dir / "index.html").write_text("<h1>normal</h1>")

    store = app.state.userspace_apps
    await _install(store, "normal", provenance="user-uploaded")

    r = await client.get("/api/userspace-apps/normal/bundle/index.html")
    csp = r.headers.get("content-security-policy", "")
    assert "img-src 'self' https: data: blob:" in csp


# ---------------------------------------------------------------------------
# app_permissions -- provenance surfaced + honoured for classified apps
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_permissions_reports_provenance_and_ceiling(app, tmp_data_dir, client):
    await _init_userspace_stores(app)
    store = app.state.userspace_apps
    await _install(store, "agent-app", provenance="ai-generated")

    r = await client.get("/api/apps/agent-app/permissions")
    data = r.json()
    assert data["provenance"] == "ai-generated"
    assert data["ceiling"] == ["app.notify", "app.window"]


@pytest.mark.asyncio
async def test_get_permissions_provenance_null_for_untracked_app_id(app, tmp_data_dir, client):
    await _init_userspace_stores(app)
    r = await client.get("/api/apps/some-native-feature/permissions")
    data = r.json()
    assert data["provenance"] is None
    assert data["ceiling"] is None


@pytest.mark.asyncio
async def test_request_consent_flags_storage_for_ai_generated_app(app, tmp_data_dir, client):
    await _init_userspace_stores(app)
    store = app.state.userspace_apps
    await _install(store, "agent-app", provenance="ai-generated")

    r = await client.post(
        "/api/apps/agent-app/request-consent",
        json={"capabilities": ["app.kv", "app.notify", "app.net"]},
    )
    data = r.json()
    # app.notify is within the ai-generated ceiling (free); app.kv and app.net
    # are not, so both need consent -- unlike the untracked-app-id path, which
    # still treats app.kv as free (see test_routes_app_permissions.py).
    assert set(data["pending"]) == {"app.kv", "app.net"}


@pytest.mark.asyncio
async def test_request_consent_untracked_app_id_behaviour_unchanged(app, tmp_data_dir, client):
    """An app_id that isn't a classified userspace app keeps the exact
    pre-existing rule: free caps skip consent regardless of provenance."""
    await _init_userspace_stores(app)
    r = await client.post(
        "/api/apps/some-native-feature/request-consent",
        json={"capabilities": ["app.kv", "app.net"]},
    )
    assert r.json()["pending"] == ["app.net"]
