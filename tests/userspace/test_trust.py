"""Tests for P3b trust-aware CSP, broker capabilities, and install endpoint.

Security invariant: the public /install endpoint MUST always write trust='community'.
First-party trust is only reachable through an internal/trusted path (store.install
with trust='first-party'), simulating what P4 boot-seeding and P2 signature
verification will do.
"""
import io
import zipfile

import pytest

from tinyagentos.userspace.broker import GATED_CAPS, handle_capability
from tinyagentos.userspace.data_store import UserspaceDataStore
from tinyagentos.userspace.store import UserspaceAppStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

WEB_MANIFEST = (
    "id: studio\nname: Studio\nversion: 1.0.0\napp_type: web\n"
    "entry: index.html\nicon: icon.png\npermissions: []\n"
)


def _zip(manifest: str = WEB_MANIFEST) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.yaml", manifest)
        z.writestr("index.html", "<h1>studio</h1>")
        z.writestr("icon.png", "x")
    return buf.getvalue()


async def _data_store(tmp_path):
    s = UserspaceDataStore(tmp_path / "d.db")
    await s.init()
    return s


# ---------------------------------------------------------------------------
# Store -- trust column
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_install_default_trust_is_community(tmp_path):
    store = UserspaceAppStore(tmp_path / "u.db")
    await store.init()
    await store.install(
        app_id="a", name="A", version="1", app_type="web",
        entry="index.html", icon="", permissions_requested=[],
    )
    row = await store.get("a")
    assert row["trust"] == "community"
    await store.close()


@pytest.mark.asyncio
async def test_install_first_party_trust(tmp_path):
    store = UserspaceAppStore(tmp_path / "u.db")
    await store.init()
    await store.install(
        app_id="studio", name="Studio", version="1", app_type="web",
        entry="index.html", icon="", permissions_requested=[], trust="first-party",
    )
    row = await store.get("studio")
    assert row["trust"] == "first-party"
    await store.close()


@pytest.mark.asyncio
async def test_migration_adds_trust_column_to_existing_db(tmp_path):
    """Existing databases (pre-trust column) get the column added with 'community' default."""
    import aiosqlite
    db_path = tmp_path / "old.db"
    # Create a database that looks like it was created before the trust column existed.
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
                container_port INTEGER
            );
        """)
        await db.execute(
            "INSERT INTO userspace_apps "
            "(app_id, name, version, app_type, entry, icon, "
            "permissions_requested, permissions_granted, enabled, installed_at) "
            "VALUES ('old', 'Old', '1', 'web', 'index.html', '', '[]', '[]', 1, 0)"
        )
        await db.commit()

    # Opening via UserspaceAppStore should run the migration.
    store = UserspaceAppStore(db_path)
    await store.init()
    row = await store.get("old")
    assert row is not None
    assert row["trust"] == "community"
    await store.close()


# ---------------------------------------------------------------------------
# Public install endpoint -- always community
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_public_install_endpoint_always_community(client):
    r = await client.post(
        "/api/userspace-apps/install",
        files={"package": ("studio.taosapp", _zip(), "application/zip")},
    )
    assert r.status_code == 200, r.text
    # Verify the stored record has community trust regardless of any manifest content.
    rows = (await client.get("/api/userspace-apps")).json()
    row = next((a for a in rows if a["app_id"] == "studio"), None)
    assert row is not None
    assert row["trust"] == "community"


# ---------------------------------------------------------------------------
# serve_bundle -- CSP by trust
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_serve_bundle_community_gets_tight_csp(client):
    await client.post(
        "/api/userspace-apps/install",
        files={"package": ("studio.taosapp", _zip(), "application/zip")},
    )
    r = await client.get("/api/userspace-apps/studio/bundle/index.html")
    assert r.status_code == 200
    csp = r.headers.get("content-security-policy", "")
    # Community CSP must include the sandbox directive and tight defaults.
    assert "sandbox" in csp
    assert "default-src 'none'" in csp


@pytest.mark.asyncio
async def test_serve_bundle_first_party_gets_relaxed_csp(client, app, tmp_path):
    # Seed a first-party app directly into the store (bypassing the public install
    # endpoint, which only ever writes community -- this is the trusted path).
    apps_dir = tmp_path / "apps" / "studio"
    apps_dir.mkdir(parents=True)
    (apps_dir / "index.html").write_text("<h1>studio</h1>")

    store = app.state.userspace_apps
    await store.install(
        app_id="studio", name="Studio", version="1", app_type="web",
        entry="index.html", icon="", permissions_requested=[], trust="first-party",
    )

    r = await client.get("/api/userspace-apps/studio/bundle/index.html")
    assert r.status_code == 200
    csp = r.headers.get("content-security-policy", "")
    # First-party CSP must still sandbox (no allow-same-origin -- critical).
    assert "sandbox" in csp
    assert "allow-same-origin" not in csp
    assert "default-src 'none'" in csp


# ---------------------------------------------------------------------------
# Broker route -- capability grants by trust
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_broker_first_party_gated_cap_succeeds_without_explicit_grant(client, app, tmp_path):
    """A first-party app can call any gated cap without a prior /permissions grant."""
    apps_dir = tmp_path / "apps" / "studio"
    apps_dir.mkdir(parents=True)
    (apps_dir / "index.html").write_text("<h1>studio</h1>")

    store = app.state.userspace_apps
    await store.install(
        app_id="studio", name="Studio", version="1", app_type="web",
        entry="index.html", icon="", permissions_requested=[], trust="first-party",
    )

    # app.memory.search is gated. No /permissions call was made -- should still succeed.
    # The memory service is not wired in the test app so it returns an empty list.
    r = await client.post(
        "/api/userspace-apps/studio/broker",
        json={"capability": "app.memory.search", "args": {"q": "x"}},
    )
    assert r.status_code == 200
    assert "error" not in r.json() or r.json().get("error") != "permission_denied"


@pytest.mark.asyncio
async def test_broker_community_gated_cap_denied_without_grant(client):
    """Community apps still require explicit permission grants for gated capabilities."""
    await client.post(
        "/api/userspace-apps/install",
        files={"package": ("studio.taosapp", _zip(), "application/zip")},
    )
    r = await client.post(
        "/api/userspace-apps/studio/broker",
        json={"capability": "app.net", "args": {"path": "/ping"}},
    )
    assert r.json()["error"] == "permission_denied"


@pytest.mark.asyncio
async def test_broker_community_gated_cap_with_grant(client):
    """Community app with explicit grant can reach a gated cap (existing behaviour preserved)."""
    # The package must REQUEST app.memory: set_permissions only grants caps the
    # manifest declared (an app cannot be escalated to caps it never requested).
    manifest = WEB_MANIFEST.replace("permissions: []", "permissions: [app.memory]")
    await client.post(
        "/api/userspace-apps/install",
        files={"package": ("studio.taosapp", _zip(manifest), "application/zip")},
    )
    await client.post(
        "/api/userspace-apps/studio/permissions",
        json={"granted": ["app.memory"]},
    )
    r = await client.post(
        "/api/userspace-apps/studio/broker",
        json={"capability": "app.memory.search", "args": {"q": "x"}},
    )
    # memory service not wired in test, so result is [] not an error
    assert "error" not in r.json() or r.json().get("error") != "permission_denied"


# ---------------------------------------------------------------------------
# broker.py unit -- all GATED_CAPS succeed when granted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_broker_all_gated_caps_granted_for_first_party(tmp_path):
    """Passing the full GATED_CAPS set as granted lets every gated namespace through."""
    ds = await _data_store(tmp_path)
    for cap_ns in GATED_CAPS:
        # Each namespace has at least one sub-cap. Use the simplest one that
        # can be exercised without external services.
        capability = f"{cap_ns}.search" if cap_ns == "app.memory" else cap_ns
        out = await handle_capability(
            "fp-app", capability, {},
            granted=set(GATED_CAPS),
            data_store=ds,
            app_dir=tmp_path / "fp-app",
            services={},
        )
        # Should not be permission_denied (may be another error due to missing
        # service, but that's fine -- the gate passed).
        assert out.get("error") != "permission_denied", (
            f"GATED_CAPS grant did not bypass gate for {capability}: {out}"
        )
    await ds.close()


@pytest.mark.asyncio
async def test_public_install_cannot_overwrite_first_party(client, app):
    """A public install of an id already installed as first-party is rejected,
    so it cannot overwrite a trusted bundle or inherit first-party privileges."""
    await app.state.userspace_apps.install(
        app_id="studio", name="Studio", version="1.0.0", app_type="web",
        entry="index.html", icon="", permissions_requested=[], trust="first-party",
    )
    r = await client.post(
        "/api/userspace-apps/install",
        files={"package": ("studio.taosapp", _zip(), "application/zip")},
    )
    assert r.status_code == 409
    row = await app.state.userspace_apps.get("studio")
    assert row["trust"] == "first-party"


@pytest.mark.asyncio
async def test_upsert_updates_trust_on_reinstall(tmp_path):
    """The install UPSERT updates trust (not only on first insert), so a later
    install with a different trust never retains a stale elevated trust."""
    store = UserspaceAppStore(tmp_path / "u.db")
    await store.init()
    await store.install(app_id="a", name="A", version="1", app_type="web",
                        entry="index.html", icon="", permissions_requested=[], trust="first-party")
    assert (await store.get("a"))["trust"] == "first-party"
    await store.install(app_id="a", name="A", version="2", app_type="web",
                        entry="index.html", icon="", permissions_requested=[], trust="community")
    assert (await store.get("a"))["trust"] == "community"
    await store.close()
