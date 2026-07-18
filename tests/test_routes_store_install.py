"""Endpoint tests for tinyagentos/routes/store_install.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinyagentos.agent_image import base_image_alias
from tinyagentos.catalog.resolver import DeviceCapability


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_model_manifest(
    manifest_id: str = "test-model",
    variant_id: str = "v1",
    backend_id = "llama-cpp",
    targets: tuple[str, ...] = ("cpu",),
    min_ram_mb: int = 512,
) -> MagicMock:
    m = MagicMock()
    m.id = manifest_id
    m.type = "model"
    m.variants = [
        {
            "id": variant_id,
            "size_mb": 100,
            "download_url": "https://example/model.bin",
            "requires": {
                "backends": [
                    {"id": backend_id, "targets": list(targets), "min_ram_mb": min_ram_mb},
                ],
            },
        },
    ]
    m.context_window = 4096
    m.hardware_tiers = {}
    m.install = {}
    m.version = "1.0.0"
    return m


def _make_service_manifest(
    manifest_id: str = "test-service",
    method: str = "docker",
) -> MagicMock:
    m = MagicMock()
    m.id = manifest_id
    m.type = "service"
    m.install = {"method": method, "ports": [8080]}
    m.requires = {}
    m.hardware_tiers = {}
    m.version = "1.0.0"
    return m


def _make_agent_framework_manifest(
    manifest_id: str = "hermes",
    name: str = "Hermes Agent Gateway",
    script: str = "scripts/install.sh",
) -> MagicMock:
    m = MagicMock()
    m.id = manifest_id
    m.name = name
    m.type = "agent-framework"
    m.install = {"method": "script", "script": script}
    m.requires = {}
    m.hardware_tiers = {}
    m.version = "1.0.0"
    return m


def _make_registry(*manifests: MagicMock) -> MagicMock:
    lookup = {m.id: m for m in manifests}

    reg = MagicMock()
    reg.get = MagicMock(side_effect=lambda app_id: lookup.get(app_id))
    reg.get_app = MagicMock(side_effect=lambda app_id: lookup.get(app_id))
    reg.mark_installed = MagicMock()
    reg.mark_uninstalled = MagicMock()
    reg.list_installed = MagicMock(return_value=[])
    reg.list_available = MagicMock(return_value=[])
    return reg


def _make_installed_apps() -> MagicMock:
    store = MagicMock()
    store.install = AsyncMock(return_value=None)
    store.uninstall = AsyncMock(return_value=True)
    store.list_installed = AsyncMock(return_value=[])
    store.get_runtime_location = AsyncMock(return_value=None)
    store.update_runtime_location = AsyncMock(return_value=None)
    store.remove_runtime_location = AsyncMock(return_value=None)
    return store


def _cpu_cap(
    ram_mb: int = 32768,
    installed_backends: tuple[str, ...] = (),
) -> DeviceCapability:
    return DeviceCapability(
        device_id="local",
        targets=("cpu",),
        total_ram_mb=ram_mb,
        total_vram_mb=0,
        free_disk_mb=100_000,
        installed_backends=installed_backends,
    )


# ---------------------------------------------------------------------------
# GET /api/store/install-progress/by-app/{app_id}
# ---------------------------------------------------------------------------


class TestInstallProgressByApp:
    @pytest.mark.asyncio
    async def test_returns_200_when_no_entries(self, client):
        resp = await client.get("/api/store/install-progress/by-app/nonexistent")
        assert resp.status_code == 200
        data = resp.json()
        assert data["app_id"] == "nonexistent"
        assert data["active"] is None

    @pytest.mark.asyncio
    async def test_returns_most_recent_entry(self, client):
        from tinyagentos.install_progress import get_global_store

        store = get_global_store()
        entry = store.start(app_id="some-app", target_remote=None)
        store.update(entry.install_id, state="downloading", bytes_downloaded=50, bytes_total=100)

        resp = await client.get("/api/store/install-progress/by-app/some-app")
        assert resp.status_code == 200
        data = resp.json()
        assert data["app_id"] == "some-app"
        assert data["active"] is not None
        assert data["active"]["state"] == "downloading"
        assert data["active"]["bytes_downloaded"] == 50

        store.finish(entry.install_id, success=True)

    @pytest.mark.asyncio
    async def test_uses_state_install_progress_store_when_set(self, client):
        mock_store = MagicMock()
        mock_entry = MagicMock()
        mock_entry.to_dict.return_value = {"install_id": "x", "state": "queued"}
        mock_store.list_by_app = MagicMock(return_value=[mock_entry])
        client._transport.app.state.install_progress_store = mock_store

        resp = await client.get("/api/store/install-progress/by-app/myapp")
        assert resp.status_code == 200
        mock_store.list_by_app.assert_called_once_with("myapp")


# ---------------------------------------------------------------------------
# GET /api/store/install-progress/{install_id}
# ---------------------------------------------------------------------------


class TestInstallProgressById:
    @pytest.mark.asyncio
    async def test_returns_404_for_unknown_id(self, client):
        resp = await client.get("/api/store/install-progress/does-not-exist")
        assert resp.status_code == 404
        assert "error" in resp.json()

    @pytest.mark.asyncio
    async def test_returns_entry_for_valid_id(self, client):
        from tinyagentos.install_progress import get_global_store

        store = get_global_store()
        entry = store.start(app_id="tracked-app", target_remote=None)

        resp = await client.get(f"/api/store/install-progress/{entry.install_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["install_id"] == entry.install_id
        assert data["app_id"] == "tracked-app"

        store.finish(entry.install_id, success=True)


# ---------------------------------------------------------------------------
# POST /api/store/install-v2
# ---------------------------------------------------------------------------


class TestInstallV2:
    @pytest.mark.asyncio
    async def test_manifest_not_found_progress_marked_failed(self, client):
        """When registry.get returns None, progress store is marked failed
        before falling through to the legacy path."""
        mock_progress = MagicMock()
        mock_progress.start = MagicMock(return_value=MagicMock(
            install_id="test-123", app_id="unknown-app",
        ))
        mock_progress.finish = MagicMock()
        client._transport.app.state.install_progress_store = mock_progress

        reg = MagicMock()
        reg.get = MagicMock(return_value=None)
        reg.get_app = MagicMock(return_value=None)
        client._transport.app.state.registry = reg
        client._transport.app.state.installed_apps = _make_installed_apps()

        # Legacy path runs real installer; mock it so we only verify progress.
        mock_installer = MagicMock()
        mock_installer.install = AsyncMock(
            return_value={"success": False, "error": "no image"},
        )
        mock_installer.start = AsyncMock(return_value={"success": True})
        with patch(
            "tinyagentos.installers.docker_installer.DockerInstaller",
            return_value=mock_installer,
        ):
            await client.post("/api/store/install-v2", json={
                "manifest_id": "unknown-app",
            })
        mock_progress.finish.assert_called_once_with(
            "test-123", success=False, error="manifest not found in registry",
        )

    @pytest.mark.asyncio
    async def test_resolve_error_returns_422(self, client):
        """When the resolver cannot satisfy requirements, return 422."""
        manifest = _make_model_manifest(min_ram_mb=999_999)
        reg = _make_registry(manifest)
        client._transport.app.state.registry = reg

        tiny = _cpu_cap(ram_mb=512)
        with patch(
            "tinyagentos.routes.store_install.get_device_capability",
            new=AsyncMock(return_value=tiny),
        ):
            resp = await client.post("/api/store/install-v2", json={
                "manifest_id": "test-model",
                "variant_id": "v1",
            })
        assert resp.status_code == 422
        body = resp.json()
        assert "near_miss" in body
        assert "suggestions" in body
        assert "install_id" in body

    @pytest.mark.asyncio
    async def test_install_chain_backend_then_model(self, client):
        """Happy path: backend is installed first, then the model."""
        manifest = _make_model_manifest(backend_id="llama-cpp")
        backend_manifest = _make_service_manifest("llama-cpp", method="download")
        reg = _make_registry(manifest, backend_manifest)
        client._transport.app.state.registry = reg
        client._transport.app.state.installed_apps = _make_installed_apps()

        cap = _cpu_cap(installed_backends=("llama-cpp",))
        # llama-cpp is already installed, so resolver returns "use", not "install_chain"
        with patch(
            "tinyagentos.routes.store_install.get_device_capability",
            new=AsyncMock(return_value=cap),
        ), patch(
            "tinyagentos.routes.store_install.get_installer",
        ) as mock_get:
            model_inst = MagicMock()
            model_inst.install = AsyncMock(return_value={"success": True})
            mock_get.return_value = model_inst

            resp = await client.post("/api/store/install-v2", json={
                "manifest_id": "test-model",
                "variant_id": "v1",
            })
        assert resp.status_code == 200
        body = resp.json()
        assert any(s["step"] == "model" and s["status"] == "installed" for s in body["chain"])
        assert "install_id" in body

    @pytest.mark.asyncio
    async def test_backend_install_failure_returns_500(self, client):
        """When the backend installer fails, return 500 with error detail."""
        manifest = _make_model_manifest(backend_id="llama-cpp")
        backend_manifest = _make_service_manifest("llama-cpp", method="download")
        reg = _make_registry(manifest, backend_manifest)
        client._transport.app.state.registry = reg
        client._transport.app.state.installed_apps = _make_installed_apps()

        cap = _cpu_cap()  # no backends installed -> triggers install_chain
        with patch(
            "tinyagentos.routes.store_install.get_device_capability",
            new=AsyncMock(return_value=cap),
        ), patch(
            "tinyagentos.routes.store_install.get_installer",
        ) as mock_get:
            backend_inst = MagicMock()
            backend_inst.install = AsyncMock(
                return_value={"success": False, "error": "build failed"},
            )
            mock_get.return_value = backend_inst

            resp = await client.post("/api/store/install-v2", json={
                "manifest_id": "test-model",
                "variant_id": "v1",
            })
        assert resp.status_code == 500
        assert "backend" in resp.json()["error"].lower()

    @pytest.mark.asyncio
    async def test_model_install_failure_returns_500(self, client):
        """When the model installer fails, return 500 with error detail."""
        manifest = _make_model_manifest(backend_id="llama-cpp")
        backend_manifest = _make_service_manifest("llama-cpp", method="download")
        reg = _make_registry(manifest, backend_manifest)
        client._transport.app.state.registry = reg
        client._transport.app.state.installed_apps = _make_installed_apps()

        cap = _cpu_cap(installed_backends=("llama-cpp",))
        with patch(
            "tinyagentos.routes.store_install.get_device_capability",
            new=AsyncMock(return_value=cap),
        ), patch(
            "tinyagentos.routes.store_install.get_installer",
        ) as mock_get:
            model_inst = MagicMock()
            model_inst.install = AsyncMock(
                return_value={"success": False, "error": "download failed"},
            )
            mock_get.return_value = model_inst

            resp = await client.post("/api/store/install-v2", json={
                "manifest_id": "test-model",
                "variant_id": "v1",
            })
        assert resp.status_code == 500
        body = resp.json()
        assert "model install failed" in body["error"]

    @pytest.mark.asyncio
    async def test_unknown_backend_returns_500(self, client):
        """A backend not in _BACKEND_TO_METHOD returns 500, not an exception."""
        manifest = _make_model_manifest(backend_id="totally-unknown-backend")
        reg = _make_registry(manifest)
        client._transport.app.state.registry = reg
        client._transport.app.state.installed_apps = _make_installed_apps()

        cap = _cpu_cap(installed_backends=("totally-unknown-backend",))
        with patch(
            "tinyagentos.routes.store_install.get_device_capability",
            new=AsyncMock(return_value=cap),
        ):
            resp = await client.post("/api/store/install-v2", json={
                "manifest_id": "test-model",
                "variant_id": "v1",
            })
        assert resp.status_code == 500
        assert "_BACKEND_TO_METHOD" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_non_model_manifest_uses_legacy_path(self, client):
        """Non-model manifests (type=service) use the legacy install path."""
        svc = _make_service_manifest("my-service", method="docker")
        reg = _make_registry(svc)
        client._transport.app.state.registry = reg
        client._transport.app.state.installed_apps = _make_installed_apps()

        mock_installer = MagicMock()
        mock_installer.install = AsyncMock(
            return_value={"success": True, "host_port": 31234},
        )
        mock_installer.start = AsyncMock(return_value={"success": True})

        with patch(
            "tinyagentos.installers.docker_installer.DockerInstaller",
            return_value=mock_installer,
        ):
            resp = await client.post("/api/store/install-v2", json={
                "manifest_id": "my-service",
            })
        assert resp.status_code == 200
        body = resp.json()
        assert body["app_id"] == "my-service"
        assert body["status"] == "installed"

    @pytest.mark.asyncio
    async def test_unresolvable_variant_returns_422(self, client):
        """When the requested variant_id doesn't match any variant, the resolver
        returns a ResolveErr and the route surfaces 422 (not 500)."""
        manifest = _make_model_manifest(variant_id="real-variant")
        reg = _make_registry(manifest)
        client._transport.app.state.registry = reg

        cap = _cpu_cap(installed_backends=("llama-cpp",))
        with patch(
            "tinyagentos.routes.store_install.get_device_capability",
            new=AsyncMock(return_value=cap),
        ):
            resp = await client.post("/api/store/install-v2", json={
                "manifest_id": "test-model",
                "variant_id": "nonexistent-variant",
            })
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_response_includes_compat(self, client):
        """Successful install-v2 response includes compat classification."""
        manifest = _make_model_manifest(backend_id="llama-cpp")
        backend_manifest = _make_service_manifest("llama-cpp", method="download")
        reg = _make_registry(manifest, backend_manifest)
        client._transport.app.state.registry = reg
        client._transport.app.state.installed_apps = _make_installed_apps()

        cap = _cpu_cap(installed_backends=("llama-cpp",))
        with patch(
            "tinyagentos.routes.store_install.get_device_capability",
            new=AsyncMock(return_value=cap),
        ), patch(
            "tinyagentos.routes.store_install.get_installer",
        ) as mock_get:
            model_inst = MagicMock()
            model_inst.install = AsyncMock(return_value={"success": True})
            mock_get.return_value = model_inst

            resp = await client.post("/api/store/install-v2", json={
                "manifest_id": "test-model",
                "variant_id": "v1",
            })
        assert resp.status_code == 200
        body = resp.json()
        assert "compat" in body
        assert "chain" in body

    # ------------------------------------------------------------------
    # Code-signing tests (#647)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_tampered_manifest_rejected_403(self, client):
        """When registry.verify_manifest_signature returns False, the
        install is rejected with 403."""
        from tinyagentos.store_signing import generate_signing_keypair

        manifest = _make_model_manifest()
        reg = _make_registry(manifest)
        reg.verify_manifest_signature = MagicMock(return_value=False)
        client._transport.app.state.registry = reg

        # Provide a signing keypair so the code-signing gate is active.
        _, pub = generate_signing_keypair()
        client._transport.app.state.store_signing_pubkey = pub

        resp = await client.post("/api/store/install-v2", json={
            "manifest_id": "test-model",
            "variant_id": "v1",
        })
        assert resp.status_code == 403
        body = resp.json()
        assert "manifest signature verification failed" == body["error"]
        assert "install_id" in body

    @pytest.mark.asyncio
    async def test_valid_signature_allows_install(self, client):
        """When registry.verify_manifest_signature returns True, the
        install proceeds past the signing gate."""
        from tinyagentos.store_signing import generate_signing_keypair

        manifest = _make_model_manifest()
        reg = _make_registry(manifest)
        reg.verify_manifest_signature = MagicMock(return_value=True)
        reg.mark_installed = MagicMock()
        client._transport.app.state.registry = reg
        client._transport.app.state.installed_apps = _make_installed_apps()

        _, pub = generate_signing_keypair()
        client._transport.app.state.store_signing_pubkey = pub

        cap = _cpu_cap(installed_backends=("llama-cpp",))
        with patch(
            "tinyagentos.routes.store_install.get_device_capability",
            new=AsyncMock(return_value=cap),
        ), patch(
            "tinyagentos.routes.store_install.get_installer",
        ) as mock_get:
            model_inst = MagicMock()
            model_inst.install = AsyncMock(return_value={"success": True})
            mock_get.return_value = model_inst

            resp = await client.post("/api/store/install-v2", json={
                "manifest_id": "test-model",
                "variant_id": "v1",
            })
        assert resp.status_code == 200
        body = resp.json()
        assert "chain" in body

    @pytest.mark.asyncio
    async def test_real_registry_detects_post_load_tampering(self, client):
        """End-to-end test: a real AppRegistry re-reads the manifest from
        disk at install time and rejects a manifest that was tampered with
        after catalog load with 403.

        Unlike the mocked tests above that stub verify_manifest_signature,
        this exercises the full path: sign-at-load, mutate-the-file,
        verify-at-install.
        """
        import tempfile
        from pathlib import Path

        from tinyagentos.registry import AppRegistry
        from tinyagentos.store_signing import generate_signing_keypair

        # 1. Create a catalog directory with one service manifest on disk.
        catalog_dir = Path(tempfile.mkdtemp())
        svc_dir = catalog_dir / "services" / "test-svc"
        svc_dir.mkdir(parents=True)
        manifest_path = svc_dir / "manifest.yaml"
        manifest_path.write_text(
            "id: test-svc\n"
            "name: Test Service\n"
            "type: service\n"
            "version: \"1.0\"\n"
            "install:\n"
            "  method: download\n",
        )

        # 2. Build a real AppRegistry with a signing key.
        priv, pub = generate_signing_keypair()
        installed_path = Path(tempfile.mkstemp(suffix=".json")[1])
        reg = AppRegistry(
            catalog_dir=catalog_dir,
            installed_path=installed_path,
            signing_key=priv,
        )
        # Load the catalog to populate _signatures.
        reg._ensure_loaded()
        assert reg.get_signature("test-svc") is not None, (
            "expected a stored signature for test-svc"
        )

        # 3. Wire the real registry into the app state.
        client._transport.app.state.registry = reg
        client._transport.app.state.store_signing_pubkey = pub
        client._transport.app.state.installed_apps = _make_installed_apps()

        # 4. Before tampering: the signature should verify and the install
        # proceeds past the gate.  (The legacy installer may fail because
        # there is no real download URL, but the HTTP status is NOT 403.)
        resp = await client.post("/api/store/install-v2", json={
            "manifest_id": "test-svc",
        })
        assert resp.status_code != 403, (
            f"expected install to pass the signing gate, got 403: {resp.json()}"
        )

        # 5. Tamper with the manifest on disk.
        manifest_path.write_text(
            "id: test-svc\n"
            "name: EVIL Service\n"
            "type: service\n"
            "version: \"1.0\"\n"
            "install:\n"
            "  method: download\n",
        )

        # 6. Now the install MUST be rejected with 403.
        resp = await client.post("/api/store/install-v2", json={
            "manifest_id": "test-svc",
        })
        assert resp.status_code == 403, (
            f"expected 403 after tampering, got {resp.status_code}: {resp.json()}"
        )
        body = resp.json()
        assert body["error"] == "manifest signature verification failed"
        assert "install_id" in body

    @pytest.mark.asyncio
    async def test_no_signing_key_skips_verification(self, client):
        """When store_signing_pubkey is not set, the signing gate is
        skipped and the install proceeds normally."""
        manifest = _make_model_manifest()
        reg = _make_registry(manifest)
        reg.mark_installed = MagicMock()
        client._transport.app.state.registry = reg
        client._transport.app.state.installed_apps = _make_installed_apps()

        # No store_signing_pubkey — simulates a taOS instance without
        # signing configured (graceful degradation).
        client._transport.app.state.store_signing_pubkey = None

        cap = _cpu_cap(installed_backends=("llama-cpp",))
        with patch(
            "tinyagentos.routes.store_install.get_device_capability",
            new=AsyncMock(return_value=cap),
        ), patch(
            "tinyagentos.routes.store_install.get_installer",
        ) as mock_get:
            model_inst = MagicMock()
            model_inst.install = AsyncMock(return_value={"success": True})
            mock_get.return_value = model_inst

            resp = await client.post("/api/store/install-v2", json={
                "manifest_id": "test-model",
                "variant_id": "v1",
            })
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/store/signing-pubkey
# ---------------------------------------------------------------------------


class TestSigningPubkey:
    @pytest.mark.asyncio
    async def test_returns_public_key_when_configured(self, client):
        from tinyagentos.store_signing import generate_signing_keypair

        _, pub = generate_signing_keypair()
        client._transport.app.state.store_signing_pubkey = pub

        resp = await client.get("/api/store/signing-pubkey")
        assert resp.status_code == 200
        body = resp.json()
        assert "public_key_pem" in body
        assert body["public_key_pem"] == pub.decode()

    @pytest.mark.asyncio
    async def test_returns_404_when_not_configured(self, client):
        client._transport.app.state.store_signing_pubkey = None

        resp = await client.get("/api/store/signing-pubkey")
        assert resp.status_code == 404
        body = resp.json()
        assert "error" in body


# ---------------------------------------------------------------------------
# POST /api/store/install-v2 -- agent-framework manifests (method: script) (#1582)
# ---------------------------------------------------------------------------


class TestAgentFrameworkInstall:
    """Installing an agent-framework manifest (hermes/openclaw's install.sh runs
    inside a per-agent LXC container at deploy time, not here) enables it for
    deploy, prefetches its base image in the background, and notifies -- it
    must never silently do nothing while reporting "installed" (#1582)."""

    @pytest.mark.asyncio
    async def test_marks_installed_without_running_the_container_script(self, client):
        manifest = _make_agent_framework_manifest("hermes")
        reg = _make_registry(manifest)
        client._transport.app.state.registry = reg
        installed_apps = _make_installed_apps()
        client._transport.app.state.installed_apps = installed_apps

        with patch(
            "tinyagentos.routes.store_install.ensure_image_present",
            new=AsyncMock(return_value=True),
        ):
            resp = await client.post("/api/store/install-v2", json={"manifest_id": "hermes"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["status"] == "installed"
        installed_apps.install.assert_called_once_with("hermes", "1.0.0", {})
        reg.mark_installed.assert_called_once_with("hermes", "1.0.0")

    @pytest.mark.asyncio
    async def test_triggers_base_image_prefetch_for_dedicated_framework(self, client):
        """hermes has a dedicated base image -- installing kicks off a
        background ensure_image_present() for it."""
        manifest = _make_agent_framework_manifest("hermes")
        reg = _make_registry(manifest)
        client._transport.app.state.registry = reg
        client._transport.app.state.installed_apps = _make_installed_apps()

        mock_ensure = AsyncMock(return_value=True)
        with patch("tinyagentos.routes.store_install.ensure_image_present", new=mock_ensure):
            resp = await client.post("/api/store/install-v2", json={"manifest_id": "hermes"})

        assert resp.status_code == 200
        assert resp.json()["prefetch"] == "started"
        mock_ensure.assert_called_once()
        alias, url = mock_ensure.call_args[0]
        assert alias == base_image_alias("hermes") == "taos-hermes-base"
        assert alias in url

    @pytest.mark.asyncio
    async def test_skips_prefetch_for_framework_without_dedicated_base(self, client):
        """agent-zero has no dedicated base image -- no prefetch is started;
        it falls back to the generic base at deploy time."""
        manifest = _make_agent_framework_manifest("agent-zero", name="Agent Zero")
        reg = _make_registry(manifest)
        client._transport.app.state.registry = reg
        client._transport.app.state.installed_apps = _make_installed_apps()

        mock_ensure = AsyncMock(return_value=True)
        with patch("tinyagentos.routes.store_install.ensure_image_present", new=mock_ensure):
            resp = await client.post("/api/store/install-v2", json={"manifest_id": "agent-zero"})

        assert resp.status_code == 200
        assert resp.json()["prefetch"] == "skipped"
        mock_ensure.assert_not_called()

    @pytest.mark.asyncio
    async def test_prefetch_failure_is_non_fatal(self, client):
        """A failed prefetch never blocks the install -- the framework stays
        enabled and a first deploy just falls back to a cold image build."""
        manifest = _make_agent_framework_manifest("hermes")
        reg = _make_registry(manifest)
        client._transport.app.state.registry = reg
        installed_apps = _make_installed_apps()
        client._transport.app.state.installed_apps = installed_apps

        with patch(
            "tinyagentos.routes.store_install.ensure_image_present",
            new=AsyncMock(side_effect=RuntimeError("network down")),
        ):
            resp = await client.post("/api/store/install-v2", json={"manifest_id": "hermes"})

        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        installed_apps.install.assert_called_once()
        reg.mark_installed.assert_called_once()

    @pytest.mark.asyncio
    async def test_emits_actionable_notification(self, client):
        manifest = _make_agent_framework_manifest("hermes", name="Hermes Agent Gateway")
        reg = _make_registry(manifest)
        client._transport.app.state.registry = reg
        client._transport.app.state.installed_apps = _make_installed_apps()

        mock_notifs = MagicMock()
        mock_notifs.add = AsyncMock()
        client._transport.app.state.notifications = mock_notifs

        with patch(
            "tinyagentos.routes.store_install.ensure_image_present",
            new=AsyncMock(return_value=True),
        ):
            resp = await client.post("/api/store/install-v2", json={"manifest_id": "hermes"})

        assert resp.status_code == 200
        mock_notifs.add.assert_called_once()
        _, kwargs = mock_notifs.add.call_args
        assert kwargs["source"] == "agent_framework"
        assert kwargs["level"] == "success"
        assert "Hermes Agent Gateway" in kwargs["title"]
        assert "Agents app" in kwargs["message"]
        assert kwargs["data"] == {"framework": "hermes"}

    @pytest.mark.asyncio
    async def test_pip_based_framework_unaffected(self, client):
        """Pip/docker-based agent-frameworks (smolagents, langroid, ...) are
        untouched -- they keep running the real installer, not the
        script-only enable+prefetch+notify path."""
        manifest = _make_agent_framework_manifest("smolagents")
        manifest.install = {"method": "pip", "package": "smolagents"}
        reg = _make_registry(manifest)
        client._transport.app.state.registry = reg
        installed_apps = _make_installed_apps()
        client._transport.app.state.installed_apps = installed_apps

        mock_installer = MagicMock()
        mock_installer.install = AsyncMock(return_value={"success": True})
        with patch(
            "tinyagentos.installers.pip_installer.PipInstaller",
            return_value=mock_installer,
        ):
            resp = await client.post("/api/store/install-v2", json={"manifest_id": "smolagents"})

        assert resp.status_code == 200
        mock_installer.install.assert_called_once()
        installed_apps.install.assert_called_once()


# ---------------------------------------------------------------------------
# POST /api/store/install-v2 -- non-framework method: script manifests (#1582)
# ---------------------------------------------------------------------------


class TestScriptBackendInstall:
    """The generic install.method: script path (backend/plugin manifests
    like ollama, tailscale) previously fell through to the default branch
    and silently marked the app installed without ever running the script.
    It must now actually run it, or fail loudly."""

    @pytest.mark.asyncio
    async def test_missing_script_returns_explicit_error_not_false_success(self, client):
        manifest = _make_service_manifest("some-service", method="script")
        manifest.install = {"method": "script", "script": "scripts/does-not-exist.sh"}
        reg = _make_registry(manifest)
        client._transport.app.state.registry = reg
        installed_apps = _make_installed_apps()
        client._transport.app.state.installed_apps = installed_apps

        resp = await client.post("/api/store/install-v2", json={"manifest_id": "some-service"})

        assert resp.status_code == 500
        assert "error" in resp.json()
        installed_apps.install.assert_not_called()
        reg.mark_installed.assert_not_called()

    @pytest.mark.asyncio
    async def test_successful_script_marks_installed(self, client):
        manifest = _make_service_manifest("some-service", method="script")
        manifest.install = {"method": "script", "script": "scripts/noop.sh"}
        reg = _make_registry(manifest)
        client._transport.app.state.registry = reg
        installed_apps = _make_installed_apps()
        client._transport.app.state.installed_apps = installed_apps

        mock_installer = MagicMock()
        mock_installer.install = AsyncMock(
            return_value={"success": True, "app_id": "some-service", "method": "script"},
        )
        with patch(
            "tinyagentos.routes.store_install.get_installer",
            return_value=mock_installer,
        ):
            resp = await client.post("/api/store/install-v2", json={"manifest_id": "some-service"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        mock_installer.install.assert_called_once_with("some-service", manifest.install)
        installed_apps.install.assert_called_once()
        reg.mark_installed.assert_called_once()

    @pytest.mark.asyncio
    async def test_failing_script_does_not_mark_installed_or_record_location(self, client):
        """rc!=0 from the script -> surfaced error, no install/mark_installed/
        runtime-location call at all (the exact rkllama-service bug: never
        claim success unless the script's own health gate passed)."""
        manifest = _make_service_manifest("rkllama", method="script")
        manifest.install = {"method": "script", "script": "scripts/install-rkllama.sh"}
        manifest.requires = {"ports": [7833]}
        reg = _make_registry(manifest)
        client._transport.app.state.registry = reg
        installed_apps = _make_installed_apps()
        client._transport.app.state.installed_apps = installed_apps

        mock_installer = MagicMock()
        mock_installer.install = AsyncMock(
            return_value={"success": False, "error": "rkllama HTTP API did not come up within 60s"},
        )
        with patch(
            "tinyagentos.routes.store_install.get_installer",
            return_value=mock_installer,
        ):
            resp = await client.post("/api/store/install-v2", json={"manifest_id": "rkllama"})

        assert resp.status_code == 500
        assert "did not come up" in resp.json()["error"]
        installed_apps.install.assert_not_called()
        installed_apps.update_runtime_location.assert_not_called()
        reg.mark_installed.assert_not_called()

    @pytest.mark.asyncio
    async def test_successful_script_records_runtime_location_from_manifest_port(self, client):
        """On a verified-running exit 0, the service's declared port
        (manifest.requires.ports, e.g. rkllama's 7833) is recorded as its
        runtime location so it gets a Launchpad shortcut / proxy target."""
        manifest = _make_service_manifest("rkllama", method="script")
        manifest.install = {"method": "script", "script": "scripts/install-rkllama.sh"}
        manifest.requires = {"ports": [7833]}
        reg = _make_registry(manifest)
        client._transport.app.state.registry = reg
        installed_apps = _make_installed_apps()
        client._transport.app.state.installed_apps = installed_apps

        mock_installer = MagicMock()
        mock_installer.install = AsyncMock(
            return_value={"success": True, "app_id": "rkllama", "method": "script"},
        )
        with patch(
            "tinyagentos.routes.store_install.get_installer",
            return_value=mock_installer,
        ):
            resp = await client.post("/api/store/install-v2", json={"manifest_id": "rkllama"})

        assert resp.status_code == 200
        installed_apps.install.assert_called_once()
        reg.mark_installed.assert_called_once()
        installed_apps.update_runtime_location.assert_called_once_with(
            "rkllama", host="127.0.0.1", port=7833, backend="rkllama", ui_path="/",
        )


# ---------------------------------------------------------------------------
# POST /api/store/uninstall-v2
# ---------------------------------------------------------------------------


class TestUninstallV2:
    @pytest.mark.asyncio
    async def test_missing_app_id_returns_400(self, client):
        resp = await client.post("/api/store/uninstall-v2", json={})
        assert resp.status_code == 400
        assert "app_id required" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_uninstall_returns_200(self, client):
        client._transport.app.state.installed_apps = _make_installed_apps()
        client._transport.app.state.registry = _make_registry()

        resp = await client.post("/api/store/uninstall-v2", json={
            "app_id": "some-app",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["app_id"] == "some-app"
        assert body["status"] in ("uninstalled", "not_installed")

    @pytest.mark.asyncio
    async def test_uninstall_not_installed(self, client):
        store = _make_installed_apps()
        store.uninstall = AsyncMock(return_value=False)
        client._transport.app.state.installed_apps = store
        client._transport.app.state.registry = _make_registry()

        resp = await client.post("/api/store/uninstall-v2", json={
            "app_id": "never-installed",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "not_installed"

    @pytest.mark.asyncio
    async def test_uninstall_calls_registry_mark_uninstalled(self, client):
        client._transport.app.state.installed_apps = _make_installed_apps()
        reg = _make_registry()
        client._transport.app.state.registry = reg

        await client.post("/api/store/uninstall-v2", json={
            "app_id": "my-app",
        })
        reg.mark_uninstalled.assert_called_once_with("my-app")

    @pytest.mark.asyncio
    async def test_uninstall_lxc_container_failure_returns_500(self, client):
        """When LXC uninstall fails, return 500 with container_error."""
        svc = _make_service_manifest("lxc-app", method="lxc")
        reg = _make_registry(svc)
        client._transport.app.state.registry = reg

        store = _make_installed_apps()
        store.get_runtime_location = AsyncMock(
            return_value={"runtime_host": "127.0.0.1", "runtime_port": 8080},
        )
        client._transport.app.state.installed_apps = store

        mock_lxc = MagicMock()
        mock_lxc.uninstall = AsyncMock(
            return_value={"success": False, "error": "container not found"},
        )
        with patch(
            "tinyagentos.routes.store_install.LXCInstaller",
            return_value=mock_lxc,
        ):
            resp = await client.post("/api/store/uninstall-v2", json={
                "app_id": "lxc-app",
            })
        assert resp.status_code == 500
        body = resp.json()
        assert "container_error" in body


# ---------------------------------------------------------------------------
# GET /api/store/installed-v2
# ---------------------------------------------------------------------------


class TestListInstalled:
    @pytest.mark.asyncio
    async def test_returns_200_with_empty_list(self, client):
        store = _make_installed_apps()
        store.list_installed = AsyncMock(return_value=[])
        client._transport.app.state.installed_apps = store

        resp = await client.get("/api/store/installed-v2")
        assert resp.status_code == 200
        data = resp.json()
        assert "installed" in data
        assert data["installed"] == []

    @pytest.mark.asyncio
    async def test_returns_installed_items(self, client):
        store = _make_installed_apps()
        store.list_installed = AsyncMock(return_value=[
            {"app_id": "app-one", "installed_at": 1000, "version": "1.0", "metadata": "{}"},
            {"app_id": "app-two", "installed_at": 2000, "version": "2.0", "metadata": "{}"},
        ])
        client._transport.app.state.installed_apps = store

        resp = await client.get("/api/store/installed-v2")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["installed"]) == 2
        ids = [item["app_id"] for item in data["installed"]]
        assert "app-one" in ids
        assert "app-two" in ids

    @pytest.mark.asyncio
    async def test_annotates_with_runtime_location(self, client):
        store = _make_installed_apps()
        store.list_installed = AsyncMock(return_value=[
            {"app_id": "app-one", "installed_at": 1000, "version": "1.0", "metadata": "{}"},
        ])
        store.get_runtime_location = AsyncMock(
            return_value={
                "runtime_host": "127.0.0.1",
                "runtime_port": 8080,
                "backend": "docker",
            },
        )
        client._transport.app.state.installed_apps = store

        resp = await client.get("/api/store/installed-v2")
        assert resp.status_code == 200
        item = resp.json()["installed"][0]
        assert item["runtime_host"] == "127.0.0.1"
        assert item["runtime_port"] == 8080
        assert item["runtime_backend"] == "docker"

    @pytest.mark.asyncio
    async def test_null_runtime_location_when_not_set(self, client):
        store = _make_installed_apps()
        store.list_installed = AsyncMock(return_value=[
            {"app_id": "app-one", "installed_at": 1000, "version": "1.0", "metadata": "{}"},
        ])
        store.get_runtime_location = AsyncMock(return_value=None)
        client._transport.app.state.installed_apps = store

        resp = await client.get("/api/store/installed-v2")
        assert resp.status_code == 200
        item = resp.json()["installed"][0]
        assert item["runtime_host"] is None
        assert item["runtime_port"] is None
        assert item["runtime_backend"] is None
