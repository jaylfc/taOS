"""Tests for the stream-chat optional userspace app manifest and seeding.

Mirrors the patterns in test_package.py (network permission validation) and
test_seed.py (real bundled app seeding). No live network required.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from tinyagentos.userspace.package import parse_manifest, PackageError
from tinyagentos.userspace.seed import seed_bundled_apps, _DEFAULT_SEED_DIR
from tinyagentos.userspace.store import UserspaceAppStore

_STREAM_CHAT_MANIFEST = """
id: stream-chat
name: Stream Chat
version: 1.0.0
app_type: web
entry: index.html
icon: ""
permissions:
  - app.kv
  - network:https://io.socialstream.ninja
  - network:wss://io.socialstream.ninja
"""

# Location of the stream-chat optional package (not in the auto-seed dir).
_OPTIONAL_DIR = Path(__file__).resolve().parents[2] / "tinyagentos" / "userspace" / "optional"
_STREAM_CHAT_DIR = _OPTIONAL_DIR / "stream-chat"


async def _make_store(tmp_path: Path) -> UserspaceAppStore:
    store = UserspaceAppStore(tmp_path / "userspace_apps.db")
    await store.init()
    return store


# ---------------------------------------------------------------------------
# Package is under optional/, not seed/
# ---------------------------------------------------------------------------


def test_stream_chat_lives_in_optional_not_seed():
    """stream-chat must be in optional/, not in the auto-seed dir."""
    seed_dir = _DEFAULT_SEED_DIR
    assert not (seed_dir / "stream-chat").exists(), (
        "stream-chat must not be in the seed dir -- it is a private/optional app"
    )
    assert _STREAM_CHAT_DIR.exists(), (
        "stream-chat must exist under tinyagentos/userspace/optional/"
    )


def test_stream_chat_optional_manifest_valid():
    """The optional stream-chat manifest must be parseable."""
    manifest_path = _STREAM_CHAT_DIR / "manifest.yaml"
    assert manifest_path.exists()
    m = parse_manifest(manifest_path.read_text("utf-8"))
    assert m["id"] == "stream-chat"
    assert m["version"] == "1.0.0"


# ---------------------------------------------------------------------------
# Manifest parse
# ---------------------------------------------------------------------------


def test_stream_chat_manifest_parses():
    m = parse_manifest(_STREAM_CHAT_MANIFEST)
    assert m["id"] == "stream-chat"
    assert m["name"] == "Stream Chat"
    assert m["version"] == "1.0.0"
    assert m["app_type"] == "web"
    assert m["entry"] == "index.html"


def test_stream_chat_network_permissions_accepted():
    m = parse_manifest(_STREAM_CHAT_MANIFEST)
    assert "network:https://io.socialstream.ninja" in m["permissions"]
    assert "network:wss://io.socialstream.ninja" in m["permissions"]


def test_stream_chat_kv_permission_present():
    m = parse_manifest(_STREAM_CHAT_MANIFEST)
    assert "app.kv" in m["permissions"]


def test_stream_chat_no_wildcard_origins():
    # Wildcard host (network:*) must be rejected by the parser.
    with pytest.raises(PackageError):
        parse_manifest(
            "id: x\nname: X\nversion: 1.0.0\napp_type: web\n"
            "permissions:\n  - 'network:*'\n"
        )


def test_stream_chat_no_path_in_network_origin():
    # Paths in network origins must be rejected (CSP injection risk).
    with pytest.raises(PackageError):
        parse_manifest(
            "id: x\nname: X\nversion: 1.0.0\napp_type: web\n"
            "permissions:\n  - 'network:https://io.socialstream.ninja/sse/abc'\n"
        )


# ---------------------------------------------------------------------------
# stream-chat must NOT auto-seed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_chat_not_auto_seeded(tmp_path):
    """seed_bundled_apps must NOT install stream-chat -- it is optional only."""
    apps_root = tmp_path / "apps"
    store = await _make_store(tmp_path)

    await seed_bundled_apps(store, apps_root)

    row = await store.get("stream-chat")
    assert row is None, (
        "stream-chat should not be auto-seeded; it is in optional/, not seed/"
    )
    await store.close()


# ---------------------------------------------------------------------------
# stream-chat is still installable on demand via the optional dir
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_chat_installable_on_demand(tmp_path):
    """Explicitly seeding from the optional dir must install stream-chat correctly."""
    apps_root = tmp_path / "apps"
    store = await _make_store(tmp_path)

    await seed_bundled_apps(store, apps_root, _OPTIONAL_DIR)

    row = await store.get("stream-chat")
    assert row is not None, "stream-chat not found after explicit optional install"
    assert row["trust"] == "first-party"
    assert row["version"] == "1.0.0"
    assert "app.kv" in row["permissions_requested"]
    assert "network:https://io.socialstream.ninja" in row["permissions_requested"]
    assert "network:wss://io.socialstream.ninja" in row["permissions_requested"]
    await store.close()


@pytest.mark.asyncio
async def test_stream_chat_on_demand_install_is_idempotent(tmp_path):
    """Installing stream-chat from optional/ twice must not change installed_at or trust."""
    apps_root = tmp_path / "apps"
    store = await _make_store(tmp_path)

    await seed_bundled_apps(store, apps_root, _OPTIONAL_DIR)
    first = await store.get("stream-chat")

    await seed_bundled_apps(store, apps_root, _OPTIONAL_DIR)
    second = await store.get("stream-chat")

    assert first["installed_at"] == second["installed_at"]
    assert second["trust"] == "first-party"
    await store.close()


# ---------------------------------------------------------------------------
# CSP correctness for stream-chat network permissions
# ---------------------------------------------------------------------------


def test_bundle_csp_includes_both_ssn_origins():
    """_bundle_csp must emit both https and wss SSN origins in connect-src."""
    from tinyagentos.routes.userspace_apps import _bundle_csp
    csp = _bundle_csp([
        "https://io.socialstream.ninja",
        "wss://io.socialstream.ninja",
    ])
    assert "https://io.socialstream.ninja" in csp
    assert "wss://io.socialstream.ninja" in csp
    assert "connect-src 'self'" in csp


def test_bundle_csp_img_src_allows_remote():
    """img-src must permit https: so avatar and emote images can load."""
    from tinyagentos.routes.userspace_apps import _bundle_csp
    csp = _bundle_csp([])
    assert "img-src 'self' https:" in csp


def test_bundle_csp_img_src_no_wildcard_connect():
    """Widening img-src to https: must not affect connect-src (stays 'self' with no grants)."""
    from tinyagentos.routes.userspace_apps import _bundle_csp
    csp = _bundle_csp([])
    assert "connect-src 'self';" in csp
