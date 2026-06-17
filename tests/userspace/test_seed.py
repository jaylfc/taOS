"""Tests for the P4a boot-seeding mechanism and the bundled taos-welcome app.

Covers:
- seed installs taos-welcome as first-party
- re-running seed is idempotent (no duplicate row, trust unchanged, version unchanged)
- bumping the bundled version causes a re-seed
- the seeded bundle is served by the bundle route with a first-party CSP
- the real bundled welcome app seeds correctly end-to-end
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
import pytest_asyncio

from tinyagentos.userspace.data_store import UserspaceDataStore
from tinyagentos.userspace.seed import seed_bundled_apps, _DEFAULT_SEED_DIR
from tinyagentos.userspace.store import UserspaceAppStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_app(seed_dir: Path, app_id: str, version: str = "1.0.0") -> Path:
    """Write a minimal valid app directory under seed_dir/{app_id}."""
    app_dir = seed_dir / app_id
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "manifest.yaml").write_text(
        f"id: {app_id}\nname: Test App\nversion: {version}\n"
        "app_type: web\nentry: index.html\nicon: \"\"\npermissions:\n  - app.kv\n"
    )
    (app_dir / "index.html").write_text("<h1>hello</h1>")
    return app_dir


async def _make_store(tmp_path: Path) -> UserspaceAppStore:
    store = UserspaceAppStore(tmp_path / "userspace_apps.db")
    await store.init()
    return store


# ---------------------------------------------------------------------------
# Core seeding behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seed_installs_app_as_first_party(tmp_path):
    seed_dir = tmp_path / "seed"
    apps_root = tmp_path / "apps"
    _write_app(seed_dir, "my-app")
    store = await _make_store(tmp_path)

    await seed_bundled_apps(store, apps_root, seed_dir)

    row = await store.get("my-app")
    assert row is not None
    assert row["trust"] == "first-party"
    assert row["version"] == "1.0.0"
    assert row["app_type"] == "web"
    assert "app.kv" in row["permissions_requested"]
    await store.close()


@pytest.mark.asyncio
async def test_seed_idempotent_same_version(tmp_path):
    """Running seed twice with the same version must not duplicate or change the row."""
    seed_dir = tmp_path / "seed"
    apps_root = tmp_path / "apps"
    _write_app(seed_dir, "my-app", version="1.0.0")
    store = await _make_store(tmp_path)

    await seed_bundled_apps(store, apps_root, seed_dir)
    first = await store.get("my-app")
    installed_at_first = first["installed_at"]

    await seed_bundled_apps(store, apps_root, seed_dir)
    second = await store.get("my-app")

    # Only one row (get still works), trust is unchanged.
    assert second["trust"] == "first-party"
    assert second["version"] == "1.0.0"
    # installed_at must not have changed on the idempotent run.
    assert second["installed_at"] == installed_at_first

    # Listing must show exactly one entry for this app.
    all_apps = await store.list_installed()
    matching = [a for a in all_apps if a["app_id"] == "my-app"]
    assert len(matching) == 1
    await store.close()


@pytest.mark.asyncio
async def test_seed_reseeds_on_version_bump(tmp_path):
    """When the bundled version changes, seeding updates the installed version."""
    seed_dir = tmp_path / "seed"
    apps_root = tmp_path / "apps"
    _write_app(seed_dir, "my-app", version="1.0.0")
    store = await _make_store(tmp_path)

    await seed_bundled_apps(store, apps_root, seed_dir)
    assert (await store.get("my-app"))["version"] == "1.0.0"

    # Bump the bundled version.
    (seed_dir / "my-app" / "manifest.yaml").write_text(
        "id: my-app\nname: Test App\nversion: 2.0.0\n"
        "app_type: web\nentry: index.html\nicon: \"\"\npermissions:\n  - app.kv\n"
    )
    await seed_bundled_apps(store, apps_root, seed_dir)

    row = await store.get("my-app")
    assert row["version"] == "2.0.0"
    assert row["trust"] == "first-party"
    await store.close()


@pytest.mark.asyncio
async def test_seed_missing_seed_dir_is_silent(tmp_path):
    """A non-existent seed_dir must not raise."""
    apps_root = tmp_path / "apps"
    store = await _make_store(tmp_path)
    await seed_bundled_apps(store, apps_root, tmp_path / "nonexistent")
    assert await store.list_installed() == []
    await store.close()


# ---------------------------------------------------------------------------
# Bundle route -- first-party CSP for seeded app
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seeded_app_served_with_first_party_csp(client, app, tmp_path):
    """The bundle route must return the first-party CSP for a seeded app."""
    seed_dir = tmp_path / "seed"
    apps_root = tmp_path / "apps"
    _write_app(seed_dir, "fp-app")
    store = app.state.userspace_apps

    await seed_bundled_apps(store, apps_root, seed_dir)

    r = await client.get("/api/userspace-apps/fp-app/bundle/index.html")
    assert r.status_code == 200
    csp = r.headers.get("content-security-policy", "")
    # Must still sandbox (no allow-same-origin -- critical security invariant).
    assert "sandbox" in csp
    assert "allow-same-origin" not in csp
    assert "default-src 'none'" in csp


# ---------------------------------------------------------------------------
# Real bundled welcome app
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_welcome_app_seeds(tmp_path):
    """The actual bundled taos-welcome app seeds correctly as first-party."""
    apps_root = tmp_path / "apps"
    store = await _make_store(tmp_path)

    await seed_bundled_apps(store, apps_root)  # uses _DEFAULT_SEED_DIR

    row = await store.get("taos-welcome")
    assert row is not None, "taos-welcome not found after seeding"
    assert row["trust"] == "first-party"
    assert row["version"] == "1.0.0"
    assert "app.kv" in row["permissions_requested"]
    assert "app.notify" in row["permissions_requested"]
    await store.close()


@pytest.mark.asyncio
async def test_real_welcome_app_is_idempotent(tmp_path):
    """Seeding the real welcome app twice must not change the record."""
    apps_root = tmp_path / "apps"
    store = await _make_store(tmp_path)

    await seed_bundled_apps(store, apps_root)
    first = await store.get("taos-welcome")

    await seed_bundled_apps(store, apps_root)
    second = await store.get("taos-welcome")

    assert first["installed_at"] == second["installed_at"]
    assert second["trust"] == "first-party"
    await store.close()


@pytest.mark.asyncio
async def test_real_welcome_bundle_served_with_first_party_csp(client, app, tmp_path):
    """The bundle route serves the real welcome app with first-party CSP."""
    apps_root = tmp_path / "apps"
    store = app.state.userspace_apps

    await seed_bundled_apps(store, apps_root)

    r = await client.get("/api/userspace-apps/taos-welcome/bundle/index.html")
    assert r.status_code == 200
    csp = r.headers.get("content-security-policy", "")
    assert "sandbox" in csp
    assert "allow-same-origin" not in csp
    assert "default-src 'none'" in csp


@pytest.mark.asyncio
async def test_seed_reseeds_non_first_party_id(tmp_path):
    """A community row claiming a seeded id (even at the same version) is re-seeded
    to first-party, not skipped by a version-only idempotency check."""
    seed_dir = tmp_path / "seed"
    apps_root = tmp_path / "apps"
    _write_app(seed_dir, "my-app", version="1.0.0")
    store = await _make_store(tmp_path)
    await store.install(app_id="my-app", name="Impostor", version="1.0.0",
                        app_type="web", entry="index.html", icon="",
                        permissions_requested=[], trust="community")
    assert (await store.get("my-app"))["trust"] == "community"

    await seed_bundled_apps(store, apps_root, seed_dir)

    assert (await store.get("my-app"))["trust"] == "first-party"
    await store.close()


@pytest.mark.asyncio
async def test_seed_reseed_removes_stale_files(tmp_path):
    """A version bump removes files that no longer exist in the new bundle."""
    seed_dir = tmp_path / "seed"
    apps_root = tmp_path / "apps"
    app_dir = _write_app(seed_dir, "my-app", version="1.0.0")
    (app_dir / "old.js").write_text("// v1 only")
    store = await _make_store(tmp_path)

    await seed_bundled_apps(store, apps_root, seed_dir)
    assert (apps_root / "my-app" / "old.js").exists()

    (app_dir / "old.js").unlink()
    (app_dir / "manifest.yaml").write_text(
        "id: my-app\nname: Test App\nversion: 2.0.0\n"
        "app_type: web\nentry: index.html\nicon: \"\"\npermissions:\n  - app.kv\n"
    )
    await seed_bundled_apps(store, apps_root, seed_dir)

    assert (await store.get("my-app"))["version"] == "2.0.0"
    assert not (apps_root / "my-app" / "old.js").exists()
    await store.close()
