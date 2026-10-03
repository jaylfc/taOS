"""MCP marketplace: manifest format, curated registry, install flow.

Red-first coverage for taOS #134.  The three assertions the card names are:

* a manifest parses (and a manifest with an invalid permission set is
  **rejected**, not silently coerced);
* the install flow produces a server config the **existing** MCP loader picks
  up — asserted against ``MCPSupervisor._resolve_cmd`` and, in one test, by
  actually spawning the process through the supervisor;
* a failed install leaves the store untouched.

The install command is executed through an injected ``runner`` so nothing here
touches the network.
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading
from pathlib import Path

import pytest
import pytest_asyncio
import yaml
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from tinyagentos.mcp.marketplace import (
    MARKETPLACE_ID_PREFIX,
    InvalidPermissionSet,
    MCPMarketplace,
    MCPMarketplaceError,
    MCPRegistry,
    MCPRegistryManifest,
    default_registry_dir,
    validate_permissions,
)
from tinyagentos.mcp.registry import MCPServerStore
from tinyagentos.mcp.supervisor import MCPSupervisor


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

FETCH_MANIFEST = {
    "id": "mcp-fetch",
    "name": "Fetch",
    "description": "Fetch a URL as markdown",
    "version": "2025.4.7",
    "author": "modelcontextprotocol",
    "categories": ["web"],
    "transport": "stdio",
    "permissions": ["network"],
    "install": {"method": "uvx", "package": "mcp-server-fetch"},
    "run": {"command": ["uvx", "mcp-server-fetch"]},
}

DOCKER_MANIFEST = {
    "id": "mcp-docker-demo",
    "name": "Docker demo",
    "version": "0.1.0",
    "author": "taos",
    "transport": "stdio",
    "permissions": ["network", "secrets"],
    "install": {
        "method": "docker",
        "package": "example/demo",
        "command": ["docker", "pull", "example/demo:latest"],
    },
    "run": {"command": ["docker", "run", "-i", "--rm", "example/demo"]},
}


def _write_manifest(directory: Path, name: str, data: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


class RecordingRunner:
    """Stand-in for the install-command runner."""

    def __init__(self, returncode: int = 0):
        self.calls: list[list[str]] = []
        self.returncode = returncode

    async def __call__(self, argv: list[str]) -> tuple[int, str, str]:
        self.calls.append(list(argv))
        if self.returncode != 0:
            return self.returncode, "", "boom"
        return 0, "installed\n", ""


@pytest_asyncio.fixture
async def store(tmp_path: Path):
    s = MCPServerStore(tmp_path / "mcp.db")
    await s.init()
    yield s
    await s.close()


@pytest.fixture
def registry_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "registry"
    _write_manifest(directory, "mcp-fetch.yaml", FETCH_MANIFEST)
    _write_manifest(directory, "mcp-docker-demo.yaml", DOCKER_MANIFEST)
    return directory


# ---------------------------------------------------------------------------
# Manifest format
# ---------------------------------------------------------------------------

class TestManifestParsing:
    def test_parses_a_registry_manifest(self):
        manifest = MCPRegistryManifest.model_validate(FETCH_MANIFEST)
        assert manifest.id == "mcp-fetch"
        assert manifest.version == "2025.4.7"
        assert manifest.permissions == ["network"]
        assert manifest.launch_argv() == ["uvx", "mcp-server-fetch"]

    def test_from_file_names_the_file_on_error(self, tmp_path: Path):
        path = _write_manifest(tmp_path, "bad.yaml", {**FETCH_MANIFEST, "permissions": ["root"]})
        with pytest.raises(ValidationError):
            MCPRegistryManifest.from_file(path)

    def test_command_string_is_split_into_argv(self):
        manifest = MCPRegistryManifest.model_validate({
            **FETCH_MANIFEST,
            "run": {"command": "uvx mcp-server-fetch --flag value"},
        })
        assert manifest.launch_argv() == ["uvx", "mcp-server-fetch", "--flag", "value"]

    def test_numeric_version_is_coerced_to_string(self):
        manifest = MCPRegistryManifest.model_validate({**FETCH_MANIFEST, "version": 2.0})
        assert manifest.version == "2.0"

    @pytest.mark.parametrize(
        "bad_id",
        ["../escape", "Bad Id", "UPPER", "", "_leading", "a" * 65, "..", ".", "-"],
    )
    def test_invalid_id_is_rejected(self, bad_id):
        with pytest.raises(ValidationError):
            MCPRegistryManifest.model_validate({**FETCH_MANIFEST, "id": bad_id})

    def test_stdio_manifest_without_run_command_is_rejected(self):
        with pytest.raises(ValidationError):
            MCPRegistryManifest.model_validate({**FETCH_MANIFEST, "run": {}})

    @pytest.mark.parametrize("transport", ["sse", "http"])
    def test_non_stdio_transport_is_rejected(self, transport):
        """The supervisor launches stdio servers only.

        Accepting sse/http would register a config with no resolvable `cmd`,
        which the start route can only 500 on.
        """
        with pytest.raises(ValidationError) as excinfo:
            MCPRegistryManifest.model_validate({
                **FETCH_MANIFEST, "transport": transport,
            })
        assert "not supported" in str(excinfo.value)

    def test_workspace_placeholder_needs_a_workspace_to_expand(self):
        manifest = MCPRegistryManifest.model_validate({
            **FETCH_MANIFEST,
            "run": {"command": ["serve"], "args": ["{workspace}", "--flag"]},
        })
        assert manifest.run.uses_workspace() is True
        with pytest.raises(ValueError, match="workspace path is required"):
            manifest.launch_argv()
        assert manifest.launch_argv("/ws") == ["serve", "/ws", "--flag"]
        assert manifest.server_config("/ws")["cmd"] == ["serve", "/ws", "--flag"]

    def test_manifest_without_a_placeholder_ignores_the_workspace(self):
        manifest = MCPRegistryManifest.model_validate(FETCH_MANIFEST)
        assert manifest.run.uses_workspace() is False
        assert manifest.launch_argv() == ["uvx", "mcp-server-fetch"]
        assert manifest.launch_argv("/ws") == ["uvx", "mcp-server-fetch"]

    def test_workspace_placeholder_in_an_env_key_or_value_counts(self):
        for env in ({"{workspace}": "1"}, {"HOME": "{workspace}"}):
            manifest = MCPRegistryManifest.model_validate({
                **FETCH_MANIFEST, "run": {"command": ["serve"], "env": env},
            })
            assert manifest.run.uses_workspace() is True, env
            config = manifest.server_config("/ws")
            assert all("{workspace}" not in key for key in config["env"])
            assert all("{workspace}" not in value for value in config["env"].values())


class TestPermissionValidation:
    def test_valid_set_is_sorted_and_deduplicated(self):
        assert validate_permissions(
            ["filesystem:write", "filesystem:read", "network", "network"]
        ) == ["filesystem:read", "filesystem:write", "network"]

    def test_empty_set_is_allowed(self):
        assert validate_permissions(None) == []
        assert validate_permissions([]) == []

    @pytest.mark.parametrize(
        "perms",
        [
            ["root"],                      # not in the vocabulary
            ["filesystem:execute"],        # unknown scope
            ["*"],                         # wildcard
            ["all"],                       # wildcard, spelled out
            ["filesystem:write"],          # write without the matching read
            ["network", 7],                # non-string entry
            [""],                          # empty entry
        ],
    )
    def test_invalid_permission_set_is_rejected(self, perms):
        with pytest.raises(InvalidPermissionSet):
            validate_permissions(perms)

    def test_string_instead_of_list_is_rejected(self):
        with pytest.raises(InvalidPermissionSet):
            validate_permissions("network")

    def test_manifest_validation_rejects_an_invalid_permission_set(self):
        with pytest.raises(ValidationError) as excinfo:
            MCPRegistryManifest.model_validate({**FETCH_MANIFEST, "permissions": ["root"]})
        assert "unknown permission" in str(excinfo.value)


# ---------------------------------------------------------------------------
# The curated registry
# ---------------------------------------------------------------------------

class TestCuratedRegistry:
    def test_shipped_registry_parses_and_is_non_empty(self):
        registry = MCPRegistry(default_registry_dir())
        manifests = registry.list()
        assert len(manifests) >= 5, "the curated starter registry should ship several entries"
        assert registry.errors == [], f"shipped manifests must all validate: {registry.errors}"

    def test_every_shipped_entry_is_complete_and_verified(self):
        registry = MCPRegistry(default_registry_dir())
        for manifest in registry.list():
            assert manifest.name
            assert manifest.description
            assert manifest.author
            assert manifest.categories, f"{manifest.id} has no category to browse by"
            assert manifest.verified, f"{manifest.id} is not marked verified"
            assert manifest.run.command, f"{manifest.id} has no run command"
            # The config the installer writes must be launchable as-is — with a
            # placeholder-free argv it needs no workspace, and with one the
            # workspace must actually be substituted.
            if manifest.run.uses_workspace():
                config = manifest.server_config(f"/ws/{manifest.id}")
                assert all("{workspace}" not in arg for arg in config["cmd"])
                assert config["cmd"][-1] == f"/ws/{manifest.id}" or \
                    f"/ws/{manifest.id}" in config["cmd"]
            else:
                assert manifest.server_config()["cmd"] == manifest.launch_argv()

    def test_shipped_entry_ids_do_not_collide_with_the_app_catalog(self):
        """The curated registry must not reuse an id an app manifest owns.

        ``mcp_servers`` is one namespace shared by every installer: the Store
        registers a bundled plugin under its app id (with no config), and the
        marketplace registers a curated entry under its manifest id.  An id both
        surfaces own means the two install paths overwrite each other's row — a
        later Store install replaces the marketplace's launch config with the
        Store's empty one (leaving a server whose command no longer resolves),
        and a marketplace uninstall deletes a row the Store still reports as
        installed.  The curated entries therefore live in their own namespace,
        and this scan is what keeps it that way.
        """
        registry = MCPRegistry(default_registry_dir())
        shipped = {m.id for m in registry.list()}
        assert shipped, "the shipped registry is empty"

        app_catalog = default_registry_dir().parent
        app_ids: set[str] = set()
        for manifest_path in app_catalog.rglob("manifest.yaml"):
            data = yaml.safe_load(manifest_path.read_text()) or {}
            if isinstance(data, dict) and data.get("id"):
                app_ids.add(str(data["id"]))
        assert app_ids, "the app-catalog scan found no manifests — the path is wrong"

        collisions = sorted(shipped & app_ids)
        assert collisions == [], (
            "curated ids collide with app-catalog ids (one mcp_servers "
            f"namespace, two installers): {collisions}"
        )

        # …and the namespace is explicit rather than accidentally non-colliding:
        # a new curated entry is added under the prefix, not next to it.
        outside = sorted(i for i in shipped if not i.startswith(MARKETPLACE_ID_PREFIX))
        assert outside == [], (
            f"curated ids outside the {MARKETPLACE_ID_PREFIX!r} namespace: {outside}"
        )

    def test_search_matches_name_description_and_category(self, registry_dir: Path):
        registry = MCPRegistry(registry_dir)
        assert [m.id for m in registry.list(query="fetch")] == ["mcp-fetch"]
        assert [m.id for m in registry.list(query="markdown")] == ["mcp-fetch"]
        assert [m.id for m in registry.list(query="web")] == ["mcp-fetch"]
        assert registry.list(query="nothing-matches-this") == []

    def test_category_filter(self, registry_dir: Path):
        registry = MCPRegistry(registry_dir)
        assert [m.id for m in registry.list(category="web")] == ["mcp-fetch"]
        assert [m.id for m in registry.list(category="WEB")] == ["mcp-fetch"]
        assert registry.list(category="absent") == []

    def test_categories_are_the_union_of_entries(self, registry_dir: Path):
        registry = MCPRegistry(registry_dir)
        assert registry.categories() == ["web"]

    def test_malformed_entry_is_skipped_and_recorded(self, registry_dir: Path):
        _write_manifest(registry_dir, "broken.yaml", {**FETCH_MANIFEST, "id": "mcp-broken", "permissions": ["root"]})
        registry = MCPRegistry(registry_dir)
        # The good entries still list …
        assert {m.id for m in registry.list()} == {"mcp-fetch", "mcp-docker-demo"}
        # … and the bad one is visible rather than silently missing.
        assert len(registry.errors) == 1
        assert registry.errors[0]["path"].endswith("broken.yaml")

    def test_duplicate_id_is_recorded(self, registry_dir: Path):
        _write_manifest(registry_dir, "dupe.yaml", FETCH_MANIFEST)
        registry = MCPRegistry(registry_dir)
        assert len(registry.list()) == 2
        assert any("duplicate id" in e["error"] for e in registry.errors)

    def test_manifest_in_a_subdirectory_is_found(self, tmp_path: Path):
        entry_dir = tmp_path / "registry" / "mcp-fetch"
        entry_dir.mkdir(parents=True)
        (entry_dir / "manifest.yaml").write_text(yaml.safe_dump(FETCH_MANIFEST))
        registry = MCPRegistry(tmp_path / "registry")
        assert registry.get("mcp-fetch") is not None

    def test_unreadable_entry_is_recorded_not_raised(self, registry_dir: Path):
        """An unreadable file must degrade to a recorded error, not a 500."""
        if os.geteuid() == 0:
            pytest.skip("root ignores file mode bits")
        victim = _write_manifest(registry_dir, "locked.yaml", {**FETCH_MANIFEST, "id": "mcp-locked"})
        victim.chmod(0o000)
        try:
            registry = MCPRegistry(registry_dir)
            assert {m.id for m in registry.list()} == {"mcp-fetch", "mcp-docker-demo"}
            assert any(e["path"].endswith("locked.yaml") for e in registry.errors)
            assert any("PermissionError" in e["error"] for e in registry.errors)
        finally:
            victim.chmod(0o600)

    def test_unreadable_directory_is_not_an_error(self, tmp_path: Path):
        if os.geteuid() == 0:
            pytest.skip("root ignores directory mode bits")
        locked = tmp_path / "locked-registry"
        locked.mkdir()
        locked.chmod(0o000)
        try:
            registry = MCPRegistry(locked)
            assert registry.list() == []
            assert registry.errors == []
        finally:
            locked.chmod(0o700)

    def test_concurrent_first_reads_do_not_deadlock(self, registry_dir: Path):
        """The lazy-load lock must be reentrant and check the flag under it.

        Regression guard for the double-checked-locking shape: with a
        non-reentrant lock (or a check taken outside the locked section) the
        first concurrent readers either deadlock here or each re-parse the
        directory.
        """
        registry = MCPRegistry(registry_dir)
        results: list[list[str]] = []
        errors: list[BaseException] = []

        def read() -> None:
            try:
                results.append([m.id for m in registry.list()])
            except BaseException as exc:  # pragma: no cover - failure path
                errors.append(exc)

        threads = [threading.Thread(target=read) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert not errors, errors
        assert not [t for t in threads if t.is_alive()], "lazy load deadlocked"
        assert results == [["mcp-docker-demo", "mcp-fetch"]] * 8

    def test_missing_directory_is_reported(self, tmp_path: Path):
        """A missing registry dir lists nothing, but says why.

        The deployment this exists for is one without the ``app-catalog`` tree
        (``default_registry_dir`` resolves relative to the checkout): the
        marketplace must not look like a curated registry with zero entries.
        """
        registry = MCPRegistry(tmp_path / "does-not-exist")
        assert registry.list() == []
        assert len(registry.errors) == 1
        assert registry.errors[0]["path"].endswith("does-not-exist")
        assert "not found" in registry.errors[0]["error"]

    def test_empty_directory_is_not_an_error(self, tmp_path: Path):
        registry = MCPRegistry(tmp_path / "empty-registry")
        (tmp_path / "empty-registry").mkdir()
        assert registry.list() == []
        assert registry.errors == []


# ---------------------------------------------------------------------------
# Install flow
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestInstallFlow:
    async def test_install_registers_a_config_the_loader_can_resolve(self, registry_dir, store):
        marketplace = MCPMarketplace(
            registry=MCPRegistry(registry_dir), store=store, runner=RecordingRunner(),
        )
        result = await marketplace.install("mcp-fetch")

        assert result["status"] == "installed"
        server = await store.get_server("mcp-fetch")
        assert server is not None
        assert server["version"] == "2025.4.7"
        assert server["transport"] == "stdio"
        assert server["config"]["cmd"] == ["uvx", "mcp-server-fetch"]
        assert server["config"]["permissions"] == ["network"]

        # The pre-existing loader picks the command up with no extra wiring.
        supervisor = MCPSupervisor(store=store, catalog=None, notif_store=None)
        assert supervisor._resolve_cmd("mcp-fetch", server) == ["uvx", "mcp-server-fetch"]

    async def test_servers_without_an_install_command_skip_the_runner(self, registry_dir, store):
        runner = RecordingRunner()
        marketplace = MCPMarketplace(
            registry=MCPRegistry(registry_dir), store=store, runner=runner,
        )
        await marketplace.install("mcp-fetch")
        assert runner.calls == []

    async def test_install_command_is_executed_when_declared(self, registry_dir, store):
        runner = RecordingRunner()
        marketplace = MCPMarketplace(
            registry=MCPRegistry(registry_dir), store=store, runner=runner,
        )
        result = await marketplace.install("mcp-docker-demo")
        assert runner.calls == [["docker", "pull", "example/demo:latest"]]
        assert result["install_output"] == "installed\n"

    async def test_dry_run_resolves_without_executing(self, registry_dir, store):
        runner = RecordingRunner()
        marketplace = MCPMarketplace(
            registry=MCPRegistry(registry_dir), store=store, runner=runner,
        )
        await marketplace.install("mcp-docker-demo", run_install_command=False)
        assert runner.calls == []
        assert (await store.get_server("mcp-docker-demo")) is not None

    async def test_failed_install_command_leaves_the_store_untouched(self, registry_dir, store):
        marketplace = MCPMarketplace(
            registry=MCPRegistry(registry_dir), store=store,
            runner=RecordingRunner(returncode=1),
        )
        with pytest.raises(MCPMarketplaceError) as excinfo:
            await marketplace.install("mcp-docker-demo")
        assert excinfo.value.status_code == 502
        assert "boom" in str(excinfo.value)
        assert await store.get_server("mcp-docker-demo") is None

    async def test_unknown_entry_is_a_404(self, registry_dir, store):
        marketplace = MCPMarketplace(registry=MCPRegistry(registry_dir), store=store)
        with pytest.raises(MCPMarketplaceError) as excinfo:
            await marketplace.install("nope")
        assert excinfo.value.status_code == 404

    async def test_a_manifest_with_an_invalid_permission_set_is_not_installable(
        self, registry_dir, store,
    ):
        """End-to-end rejection: a bad permission set never becomes a server."""
        _write_manifest(
            registry_dir, "bad-perms.yaml",
            {**FETCH_MANIFEST, "id": "mcp-bad-perms", "permissions": ["root"]},
        )
        marketplace = MCPMarketplace(registry=MCPRegistry(registry_dir), store=store)
        with pytest.raises(MCPMarketplaceError) as excinfo:
            await marketplace.install("mcp-bad-perms")
        assert excinfo.value.status_code == 404
        assert await store.get_server("mcp-bad-perms") is None

    async def test_reinstall_is_a_409(self, registry_dir, store):
        marketplace = MCPMarketplace(
            registry=MCPRegistry(registry_dir), store=store, runner=RecordingRunner(),
        )
        await marketplace.install("mcp-fetch")
        with pytest.raises(MCPMarketplaceError) as excinfo:
            await marketplace.install("mcp-fetch")
        assert excinfo.value.status_code == 409

    async def test_install_refuses_an_id_owned_by_another_installer(self, registry_dir, store):
        """A row the marketplace did not write is reported, never replaced.

        ``mcp_servers`` is shared with the app Store, which registers a bundled
        plugin under its app id with no config.  The install must leave that row
        exactly as it is and say why, instead of the bare "already installed"
        (which reads as *this* entry being present).
        """
        await store.register_server("mcp-fetch", "0.1.0", "stdio", {})
        marketplace = MCPMarketplace(
            registry=MCPRegistry(registry_dir), store=store, runner=RecordingRunner(),
        )
        with pytest.raises(MCPMarketplaceError) as excinfo:
            await marketplace.install("mcp-fetch")
        assert excinfo.value.status_code == 409
        assert "outside the marketplace" in str(excinfo.value)
        server = await store.get_server("mcp-fetch")
        assert server["version"] == "0.1.0"
        assert server["config"] == {}

    async def test_uninstall_refuses_a_row_owned_by_another_installer(self, registry_dir, store):
        """Removing a Store-owned row would desync the Store's installed.json."""
        await store.register_server("mcp-fetch", "0.1.0", "stdio", {})
        marketplace = MCPMarketplace(
            registry=MCPRegistry(registry_dir), store=store, runner=RecordingRunner(),
        )
        with pytest.raises(MCPMarketplaceError) as excinfo:
            await marketplace.uninstall("mcp-fetch")
        assert excinfo.value.status_code == 409
        assert await store.get_server("mcp-fetch") is not None

    async def test_browse_and_detail_report_the_install_origin(self, registry_dir, store):
        marketplace = MCPMarketplace(
            registry=MCPRegistry(registry_dir), store=store, runner=RecordingRunner(),
        )
        await store.register_server("mcp-fetch", "0.1.0", "stdio", {})
        entries = {e["id"]: e for e in await marketplace.browse()}
        assert entries["mcp-fetch"]["installed"] is True
        assert entries["mcp-fetch"]["installed_by_marketplace"] is False
        detail = await marketplace.detail("mcp-fetch")
        assert detail["installed"] is True
        assert detail["installed_by_marketplace"] is False

        await store.delete_server("mcp-fetch")
        await marketplace.install("mcp-fetch")
        entries = {e["id"]: e for e in await marketplace.browse()}
        assert entries["mcp-fetch"]["installed_by_marketplace"] is True
        assert (await marketplace.detail("mcp-fetch"))["installed_by_marketplace"] is True

    async def test_uninstall_removes_the_server(self, registry_dir, store):
        marketplace = MCPMarketplace(
            registry=MCPRegistry(registry_dir), store=store, runner=RecordingRunner(),
        )
        await marketplace.install("mcp-fetch")
        result = await marketplace.uninstall("mcp-fetch")
        assert result["status"] == "uninstalled"
        assert await store.get_server("mcp-fetch") is None

    async def test_uninstall_of_a_missing_server_is_a_404(self, registry_dir, store):
        marketplace = MCPMarketplace(registry=MCPRegistry(registry_dir), store=store)
        with pytest.raises(MCPMarketplaceError) as excinfo:
            await marketplace.uninstall("mcp-fetch")
        assert excinfo.value.status_code == 404

    async def test_browse_annotates_installed_state(self, registry_dir, store):
        marketplace = MCPMarketplace(
            registry=MCPRegistry(registry_dir), store=store, runner=RecordingRunner(),
        )
        entries = {e["id"]: e for e in await marketplace.browse()}
        assert entries["mcp-fetch"]["installed"] is False
        await marketplace.install("mcp-fetch")
        entries = {e["id"]: e for e in await marketplace.browse()}
        assert entries["mcp-fetch"]["installed"] is True
        assert entries["mcp-fetch"]["running"] is False

    async def test_detail_reports_the_installed_config(self, registry_dir, store):
        marketplace = MCPMarketplace(
            registry=MCPRegistry(registry_dir), store=store, runner=RecordingRunner(),
        )
        detail = await marketplace.detail("mcp-fetch")
        assert detail["installed"] is False
        assert "installed_config" not in detail
        await marketplace.install("mcp-fetch")
        detail = await marketplace.detail("mcp-fetch")
        assert detail["installed"] is True
        assert detail["installed_config"]["cmd"] == ["uvx", "mcp-server-fetch"]

    async def test_the_install_runner_is_bounded_and_kills_a_hung_command(self, registry_dir, store):
        """A never-exiting install command must not hold the request open."""
        from functools import partial

        from tinyagentos.mcp.marketplace import _subprocess_runner

        sleeper = [sys.executable, "-c", "import time; time.sleep(30)"]
        _write_manifest(registry_dir, "mcp-slow.yaml", {
            "id": "mcp-slow",
            "name": "Slow",
            "version": "1.0.0",
            "author": "taos",
            "categories": ["test"],
            "transport": "stdio",
            "permissions": [],
            "install": {"method": "script", "command": sleeper},
            "run": {"command": sleeper},
        })
        marketplace = MCPMarketplace(
            registry=MCPRegistry(registry_dir), store=store,
            runner=partial(_subprocess_runner, timeout=0.3),
        )
        with pytest.raises(MCPMarketplaceError) as excinfo:
            await marketplace.install("mcp-slow")
        assert excinfo.value.status_code == 502
        assert "timed out" in str(excinfo.value)
        assert await store.get_server("mcp-slow") is None

    async def test_install_provisions_the_workspace_a_manifest_needs(self, tmp_path, store):
        """`{workspace}` is expanded to a freshly created, private directory."""
        workspace_root = tmp_path / "mcp-servers"
        marketplace = MCPMarketplace(
            registry=MCPRegistry(default_registry_dir()), store=store,
            workspace_root=workspace_root,
        )
        result = await marketplace.install("mcp-community-filesystem", run_install_command=False)
        expected = str(workspace_root / "mcp-community-filesystem")
        assert result["workspace"] == expected
        assert Path(expected).is_dir()
        assert result["config"]["cmd"][-1] == expected
        assert "{workspace}" not in " ".join(result["config"]["cmd"])

        server = await store.get_server("mcp-community-filesystem")
        supervisor = MCPSupervisor(store=store, catalog=None, notif_store=None)
        cmd = supervisor._resolve_cmd("mcp-community-filesystem", server)
        assert cmd is not None
        assert cmd[-1] == expected

    async def test_install_refuses_a_workspace_entry_without_a_root(self, store):
        marketplace = MCPMarketplace(
            registry=MCPRegistry(default_registry_dir()), store=store,
        )
        with pytest.raises(MCPMarketplaceError) as excinfo:
            await marketplace.install("mcp-community-filesystem", run_install_command=False)
        assert excinfo.value.status_code == 500
        assert await store.get_server("mcp-community-filesystem") is None

    async def test_a_failed_install_leaves_no_workspace_behind(self, registry_dir, store, tmp_path):
        """A failed install must leave neither a store row nor a directory."""
        workspace_root = tmp_path / "mcp-servers"
        _write_manifest(registry_dir, "mcp-ws.yaml", {
            "id": "mcp-ws",
            "name": "Workspace demo",
            "version": "1.0.0",
            "author": "taos",
            "categories": ["test"],
            "transport": "stdio",
            "permissions": [],
            "install": {"method": "script", "command": ["false"]},
            "run": {"command": ["serve"], "args": ["{workspace}"]},
        })
        marketplace = MCPMarketplace(
            registry=MCPRegistry(registry_dir), store=store,
            runner=RecordingRunner(returncode=1), workspace_root=workspace_root,
        )
        with pytest.raises(MCPMarketplaceError):
            await marketplace.install("mcp-ws")
        assert await store.get_server("mcp-ws") is None
        assert not (workspace_root / "mcp-ws").exists()

    async def test_the_install_command_cannot_use_the_workspace_placeholder(self, registry_dir, store, tmp_path):
        _write_manifest(registry_dir, "mcp-bad-install.yaml", {
            "id": "mcp-bad-install",
            "name": "Bad install",
            "version": "1.0.0",
            "author": "taos",
            "categories": ["test"],
            "transport": "stdio",
            "permissions": [],
            "install": {"method": "script", "command": ["install-into", "{workspace}"]},
            "run": {"command": ["serve"]},
        })
        marketplace = MCPMarketplace(
            registry=MCPRegistry(registry_dir), store=store,
            workspace_root=tmp_path / "mcp-servers",
        )
        with pytest.raises(MCPMarketplaceError) as excinfo:
            await marketplace.install("mcp-bad-install")
        assert excinfo.value.status_code == 400
        assert await store.get_server("mcp-bad-install") is None

    async def test_browse_keeps_the_workspace_placeholder(self, store):
        marketplace = MCPMarketplace(
            registry=MCPRegistry(default_registry_dir()), store=store,
            workspace_root=None,
        )
        entry = next(e for e in await marketplace.browse() if e["id"] == "mcp-community-filesystem")
        assert entry["uses_workspace"] is True
        assert entry["command"][-1] == "{workspace}"

    async def test_installed_server_is_launchable_by_the_supervisor(self, tmp_path: Path, store):
        """The strongest form of "loadable": the supervisor actually spawns it."""
        registry_dir = tmp_path / "registry"
        _write_manifest(registry_dir, "mcp-sleeper.yaml", {
            "id": "mcp-sleeper",
            "name": "Sleeper",
            "description": "test fixture",
            "version": "1.0.0",
            "author": "taos",
            "categories": ["test"],
            "transport": "stdio",
            "permissions": [],
            "install": {"method": "none"},
            "run": {
                "command": [sys.executable, "-c", "import time; time.sleep(30)"],
            },
        })
        marketplace = MCPMarketplace(registry=MCPRegistry(registry_dir), store=store)
        await marketplace.install("mcp-sleeper", run_install_command=False)

        supervisor = MCPSupervisor(store=store, catalog=None, notif_store=None)
        try:
            assert await supervisor.start("mcp-sleeper") is True
            status = supervisor.get_status("mcp-sleeper")
            assert status["running"] is True
            assert status["pid"]
        finally:
            await supervisor.stop("mcp-sleeper")
        assert supervisor.get_status("mcp-sleeper")["running"] is False

    async def test_declared_env_reaches_the_launched_server(self, tmp_path: Path, store):
        """A manifest's ``run.env`` must survive into the spawned process.

        Regression guard for the install contract: the config the marketplace
        writes carries an ``env`` block, and a supervisor that spawned with the
        inherited environment only would silently drop it (red on the base
        branch: the child printed ``PROBE=<unset>``).
        """
        registry_dir = tmp_path / "registry"
        _write_manifest(registry_dir, "mcp-env.yaml", {
            "id": "mcp-env",
            "name": "Env probe",
            "description": "test fixture",
            "version": "1.0.0",
            "author": "taos",
            "categories": ["test"],
            "transport": "stdio",
            "permissions": [],
            "install": {"method": "none"},
            "run": {
                "command": [
                    sys.executable, "-c",
                    "import os, time; print('PROBE=' + os.environ.get('TAOS_PROBE', '<unset>'), flush=True); time.sleep(10)",
                ],
                "env": {"TAOS_PROBE": "yes"},
            },
        })
        marketplace = MCPMarketplace(registry=MCPRegistry(registry_dir), store=store)
        result = await marketplace.install("mcp-env", run_install_command=False)
        assert result["config"]["env"] == {"TAOS_PROBE": "yes"}

        supervisor = MCPSupervisor(store=store, catalog=None, notif_store=None)
        try:
            assert await supervisor.start("mcp-env") is True
            for _ in range(40):
                if any("PROBE=" in e["line"] for e in supervisor.logs("mcp-env")):
                    break
                await asyncio.sleep(0.1)
            lines = [e["line"] for e in supervisor.logs("mcp-env")]
        finally:
            await supervisor.stop("mcp-env")
        assert any("PROBE=yes" in line for line in lines), f"child env not applied: {lines}"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def app_client(tmp_path: Path, registry_dir: Path):
    """Minimal FastAPI app with only the marketplace router wired."""
    from fastapi import FastAPI

    from tinyagentos.routes.mcp_marketplace import router as marketplace_router

    mini_app = FastAPI()

    # This bare app has no AuthMiddleware, so simulate an already-authenticated
    # admin request (request.state.is_admin) -- these tests exercise the
    # marketplace handlers, not the admin authz gate (test_authz_gate.py and
    # tests/test_global_routers_authz.py own that).
    @mini_app.middleware("http")
    async def _fake_admin_auth(request, call_next):
        request.state.is_admin = True
        request.state.via = "session"
        return await call_next(request)

    mini_app.include_router(marketplace_router)

    mcp_store = MCPServerStore(tmp_path / "mcp.db")
    await mcp_store.init()
    mini_app.state.mcp_store = mcp_store
    mini_app.state.mcp_marketplace = MCPMarketplace(
        registry=MCPRegistry(registry_dir), store=mcp_store, runner=RecordingRunner(),
    )

    transport = ASGITransport(app=mini_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, mini_app

    await mcp_store.close()


@pytest.mark.asyncio
class TestMarketplaceRoutes:
    async def test_list_and_search(self, app_client):
        client, _ = app_client
        resp = await client.get("/api/mcp/marketplace/servers")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 2
        assert body["categories"] == ["web"]
        assert {s["id"] for s in body["servers"]} == {"mcp-fetch", "mcp-docker-demo"}

        resp = await client.get("/api/mcp/marketplace/servers", params={"q": "markdown"})
        assert [s["id"] for s in resp.json()["servers"]] == ["mcp-fetch"]

        resp = await client.get("/api/mcp/marketplace/servers", params={"category": "absent"})
        assert resp.json()["servers"] == []

    async def test_categories_route(self, app_client):
        client, _ = app_client
        resp = await client.get("/api/mcp/marketplace/categories")
        assert resp.status_code == 200
        assert resp.json() == {"categories": ["web"]}

    async def test_detail_route(self, app_client):
        client, _ = app_client
        resp = await client.get("/api/mcp/marketplace/servers/mcp-fetch")
        assert resp.status_code == 200
        assert resp.json()["id"] == "mcp-fetch"
        assert resp.json()["installed"] is False

        resp = await client.get("/api/mcp/marketplace/servers/absent")
        assert resp.status_code == 404
        assert "not found" in resp.json()["error"]

    async def test_install_then_uninstall(self, app_client):
        client, app = app_client
        resp = await client.post("/api/mcp/marketplace/servers/mcp-fetch/install")
        assert resp.status_code == 201
        assert resp.json()["config"]["cmd"] == ["uvx", "mcp-server-fetch"]
        assert await app.state.mcp_store.get_server("mcp-fetch") is not None

        resp = await client.post("/api/mcp/marketplace/servers/mcp-fetch/install")
        assert resp.status_code == 409

        resp = await client.delete("/api/mcp/marketplace/servers/mcp-fetch")
        assert resp.status_code == 200
        assert await app.state.mcp_store.get_server("mcp-fetch") is None

    async def test_install_unknown_entry_is_404(self, app_client):
        client, _ = app_client
        resp = await client.post("/api/mcp/marketplace/servers/absent/install")
        assert resp.status_code == 404

    async def test_reload_reports_registry_errors(self, app_client):
        client, app = app_client
        registry_dir = app.state.mcp_marketplace.registry.registry_dir
        _write_manifest(registry_dir, "broken.yaml", {**FETCH_MANIFEST, "id": "mcp-broken", "permissions": ["root"]})
        resp = await client.post("/api/mcp/marketplace/reload")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["count"] == 2
        assert len(body["errors"]) == 1

    async def test_missing_marketplace_is_503(self):
        from fastapi import FastAPI

        from tinyagentos.routes.mcp_marketplace import router as marketplace_router

        bare = FastAPI()
        bare.include_router(marketplace_router)
        transport = ASGITransport(app=bare)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/mcp/marketplace/servers")
            assert resp.status_code == 503


def test_merged_env_merges_over_the_controller_environment(monkeypatch):
    """A config's env is merged, never used to replace os.environ."""
    from tinyagentos.mcp.supervisor import _merged_env

    monkeypatch.setenv("TAOS_MERGE_BASE", "base")
    assert _merged_env(None) is None
    assert _merged_env({}) is None
    assert _merged_env({"env": {}}) is None

    merged = _merged_env({"env": {"TAOS_MERGE_BASE": "override", "TAOS_MERGE_EXTRA": "x"}})
    assert merged is not None
    assert merged["TAOS_MERGE_BASE"] == "override"
    assert merged["TAOS_MERGE_EXTRA"] == "x"
    # PATH survives: replacing the environment would break npx/uvx launches.
    assert "PATH" in merged

    # A non-string value (reachable through PUT /api/mcp/servers/{id}/config)
    # is skipped rather than crashing the spawn.
    filtered = _merged_env({"env": {"OK": "1", "BAD": 7}})
    assert filtered is not None
    assert filtered["OK"] == "1"
    assert "BAD" not in filtered


def test_marketplace_routes_are_registered_by_create_app(tmp_data_dir):
    from tinyagentos.app import create_app

    app = create_app(data_dir=tmp_data_dir)
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/mcp/marketplace/servers" in paths
    assert "/api/mcp/marketplace/servers/{manifest_id}/install" in paths
    assert "/api/mcp/marketplace/servers/{manifest_id}" in paths
    assert "/api/mcp/marketplace/categories" in paths
    assert "/api/mcp/marketplace/reload" in paths
    assert "/api/mcp/marketplace/servers" in paths
    assert app.state.mcp_marketplace is not None
    # The shipped registry is reachable through the app-level instance.
    assert len(app.state.mcp_marketplace.registry.list()) >= 5
    # …and it can provision a workspace for the entries that need one.
    assert app.state.mcp_marketplace.workspace_root == tmp_data_dir / "mcp-servers"


def _dependency_names(dependant) -> set[str]:
    """Every dependency callable name reachable from a route's Dependant."""
    names: set[str] = set()
    stack = [dependant]
    while stack:
        node = stack.pop()
        call = getattr(node, "call", None)
        name = getattr(call, "__name__", None)
        if name:
            names.add(name)
        stack.extend(getattr(node, "dependencies", []) or [])
    return names


def test_marketplace_routes_require_admin(tmp_data_dir):
    """The mutating marketplace routes must carry the admin gate.

    The browse reads stay open (any signed-in user may look), so only the
    install/uninstall/reload trio is asserted here.
    """
    from tinyagentos.app import create_app

    app = create_app(data_dir=tmp_data_dir)
    gated = {
        ("POST", "/api/mcp/marketplace/servers/{manifest_id}/install"),
        ("DELETE", "/api/mcp/marketplace/servers/{manifest_id}"),
        ("POST", "/api/mcp/marketplace/reload"),
    }
    seen = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/mcp/marketplace"):
            continue
        for method in getattr(route, "methods", set()) or set():
            if method in {"POST", "PUT", "PATCH", "DELETE"}:
                seen.add((method, path))
                names = _dependency_names(route.dependant)
                assert names & {"require_admin", "_require_admin"}, (
                    f"{method} {path} has no admin dependency (got {names})"
                )
    assert seen == gated, f"unexpected mutating marketplace routes: {seen ^ gated}"
