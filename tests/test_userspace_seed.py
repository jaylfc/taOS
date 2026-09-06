"""Unit tests for userspace boot-seeding of first-party bundled apps."""
from __future__ import annotations

from pathlib import Path

import pytest

from tinyagentos.userspace.seed import _DEFAULT_SEED_DIR, seed_bundled_apps
from tinyagentos.userspace.store import UserspaceAppStore


def _write_seed_app(seed_dir: Path, app_id: str, version: str = "1.0.0") -> Path:
    app_dir = seed_dir / app_id
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "manifest.yaml").write_text(
        f"id: {app_id}\nname: Test App\nversion: {version}\n"
        "app_type: web\nentry: index.html\nicon: \"\"\npermissions:\n  - app.kv\n"
    )
    (app_dir / "index.html").write_text("<h1>hello</h1>")
    return app_dir


async def _store(tmp_path) -> UserspaceAppStore:
    s = UserspaceAppStore(tmp_path / "userspace_apps.db")
    await s.init()
    return s


@pytest.mark.asyncio
async def test_seed_installs_app_as_first_party(tmp_path):
    seed_dir = tmp_path / "seed"
    apps_root = tmp_path / "apps"
    _write_seed_app(seed_dir, "my-app")
    store = await _store(tmp_path)
    try:
        await seed_bundled_apps(store, apps_root, seed_dir)

        row = await store.get("my-app")
        assert row is not None
        assert row["trust"] == "first-party"
        assert row["version"] == "1.0.0"
        assert row["app_type"] == "web"
        assert "app.kv" in row["permissions_requested"]
        assert (apps_root / "my-app" / "index.html").exists()
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_seed_is_idempotent_for_same_version(tmp_path):
    seed_dir = tmp_path / "seed"
    apps_root = tmp_path / "apps"
    _write_seed_app(seed_dir, "my-app", version="1.0.0")
    store = await _store(tmp_path)
    try:
        await seed_bundled_apps(store, apps_root, seed_dir)
        first = await store.get("my-app")
        installed_at = first["installed_at"]

        await seed_bundled_apps(store, apps_root, seed_dir)
        second = await store.get("my-app")

        assert second["trust"] == "first-party"
        assert second["version"] == "1.0.0"
        assert second["installed_at"] == installed_at
        assert len([a for a in await store.list_installed() if a["app_id"] == "my-app"]) == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_seed_reseeds_on_version_bump(tmp_path):
    seed_dir = tmp_path / "seed"
    apps_root = tmp_path / "apps"
    _write_seed_app(seed_dir, "my-app", version="1.0.0")
    store = await _store(tmp_path)
    try:
        await seed_bundled_apps(store, apps_root, seed_dir)
        assert (await store.get("my-app"))["version"] == "1.0.0"

        (seed_dir / "my-app" / "manifest.yaml").write_text(
            "id: my-app\nname: Test App\nversion: 2.0.0\n"
            "app_type: web\nentry: index.html\nicon: \"\"\npermissions:\n  - app.kv\n"
        )
        await seed_bundled_apps(store, apps_root, seed_dir)

        row = await store.get("my-app")
        assert row["version"] == "2.0.0"
        assert row["trust"] == "first-party"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_seed_reseeds_non_first_party_same_version(tmp_path):
    seed_dir = tmp_path / "seed"
    apps_root = tmp_path / "apps"
    _write_seed_app(seed_dir, "my-app", version="1.0.0")
    store = await _store(tmp_path)
    try:
        await store.install(
            app_id="my-app",
            name="Impostor",
            version="1.0.0",
            app_type="web",
            entry="index.html",
            icon="",
            permissions_requested=[],
            trust="community",
        )

        await seed_bundled_apps(store, apps_root, seed_dir)

        row = await store.get("my-app")
        assert row["trust"] == "first-party"
        assert row["version"] == "1.0.0"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_seed_missing_seed_dir_is_noop(tmp_path):
    apps_root = tmp_path / "apps"
    store = await _store(tmp_path)
    try:
        await seed_bundled_apps(store, apps_root, tmp_path / "missing-seed")
        assert await store.list_installed() == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_real_bundled_welcome_app_seeds(tmp_path):
    apps_root = tmp_path / "apps"
    store = await _store(tmp_path)
    try:
        await seed_bundled_apps(store, apps_root, _DEFAULT_SEED_DIR)

        row = await store.get("taos-welcome")
        assert row is not None
        assert row["trust"] == "first-party"
        assert row["version"] == "1.0.0"
        assert "app.kv" in row["permissions_requested"]
        assert "app.notify" in row["permissions_requested"]
        assert (apps_root / "taos-welcome" / "index.html").exists()
    finally:
        await store.close()