"""Regression tests for lib-audit R2-34 (catalog ports duplication).

Background: 27 service manifests duplicated the port list under both
``requires.ports`` and ``install.ports``.  ``install.ports`` shipped in the
``"host:container"`` string form (e.g. ``"6333:6333"``) on a couple of
services and DockerInstaller crashed when the dead ``elif`` branch tried
``int("6333:6333")``.  Migrate to a single source of truth
(``install.ports``), parse ``host:container`` pairs, and emit
``extra_hosts: ["host.docker.internal:host-gateway"]`` whenever any env
value references ``host.docker.internal`` (so Linux Docker Engine, which
does not resolve the magic name by default, can still reach the host).
"""
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from tinyagentos.installers.docker_installer import DockerInstaller

_CATALOG_ROOT = Path(__file__).resolve().parents[2] / "app-catalog"


class TestQdrantHostContainerPorts:
    """docker_installer with the qdrant manifest must not crash on the
    ``"host:container"`` strings in ``install.ports`` and must emit a
    correct ``-p`` mapping for every declared port."""

    def test_qdrant_manifest_does_not_raise_on_install(self, tmp_path):
        manifest_path = _CATALOG_ROOT / "services" / "qdrant" / "manifest.yaml"
        manifest = yaml.safe_load(manifest_path.read_text())
        install_config = manifest["install"]

        # The fix puts install.ports in "host:container" string form on
        # qdrant, mirroring the rest of the catalog. Pre-fix the dead
        # elif branch in _generate_compose called int("6333:6333") and
        # raised ValueError.
        assert any(":" in str(p) for p in install_config["ports"]), (
            "qdrant manifest should declare host:container port strings"
        )

        installer = DockerInstaller(apps_dir=tmp_path)
        # _generate_compose is synchronous and does not shell out
        compose, host_port = installer._generate_compose("qdrant", install_config)
        service = compose["services"]["qdrant"]

        # Every declared install.ports entry produces exactly one -p mapping.
        assert "ports" in service, "compose must include a ports mapping"
        mappings = service["ports"]
        assert len(mappings) == len(install_config["ports"])

        # Each mapping must be of the form "<host>:<container>" with a
        # numeric container port drawn from the manifest and a host port
        # drawn from the managed pool (allocate_host_port handles the
        # pool + reservation rules).
        for mapping, declared in zip(mappings, install_config["ports"]):
            _, _, container_str = mapping.partition(":")
            container_port = int(container_str)
            declared_host_str, _, declared_container_str = str(declared).partition(":")
            assert container_port == int(declared_container_str)
            # The host half must be a managed-pool port, never the literal
            # "host:container" string from the manifest.
            host_str, _, _ = mapping.partition(":")
            assert ":" not in host_str, (
                f"host side of mapping {mapping!r} must not be host:container"
            )
            host_port = int(host_str)
            from tinyagentos.installers.port_allocator import (
                _POOL_END, _POOL_START, RESERVED_PORTS,
            )
            assert _POOL_START <= host_port < _POOL_END
            assert host_port not in RESERVED_PORTS
            # The host port must not be a literal core port the manifest
            # duplicated (e.g. "6333") -- it must come from allocate_host_port.
            assert host_port != int(declared_host_str)


class TestExtraHostsForHostDockerInternal:
    """Linux Docker Engine does not resolve ``host.docker.internal`` by
    default.  When a service's env references it (perplexica, open-webui),
    docker_installer must add ``extra_hosts`` so the container can reach
    the host via the gateway address."""

    def _render(self, installer, app_id, install_config):
        compose, _ = installer._generate_compose(app_id, install_config)
        return compose["services"][app_id]

    def test_perplexica_manifest_adds_extra_hosts(self, tmp_path):
        manifest_path = _CATALOG_ROOT / "services" / "perplexica" / "manifest.yaml"
        manifest = yaml.safe_load(manifest_path.read_text())
        installer = DockerInstaller(apps_dir=tmp_path)
        service = self._render(installer, "perplexica", manifest["install"])
        assert "extra_hosts" in service
        assert "host.docker.internal:host-gateway" in service["extra_hosts"]

    def test_open_webui_manifest_adds_extra_hosts(self, tmp_path):
        manifest_path = _CATALOG_ROOT / "services" / "open-webui" / "manifest.yaml"
        manifest = yaml.safe_load(manifest_path.read_text())
        installer = DockerInstaller(apps_dir=tmp_path)
        service = self._render(installer, "open-webui", manifest["install"])
        assert "extra_hosts" in service
        assert "host.docker.internal:host-gateway" in service["extra_hosts"]

    def test_no_extra_hosts_when_host_docker_internal_not_referenced(self, tmp_path):
        """Services whose env does not reference host.docker.internal must
        not gain an extra_hosts block (the gateway alias is unnecessary
        and may interact with strict firewall configs)."""
        installer = DockerInstaller(apps_dir=tmp_path)
        service = self._render(installer, "qdrant", {
            "image": "qdrant/qdrant:v1.13.0",
            "ports": ["6333:6333", "6334:6334"],
            "env": {},
        })
        assert "extra_hosts" not in service


class TestRequiresPortsRemovedFromCatalogManifests:
    """The audit (R2-34) requires a single source of truth for the port
    list.  Every service manifest must declare ``install.ports`` (or
    declare neither) but not both."""

    @pytest.mark.parametrize(
        "manifest_path",
        sorted((_CATALOG_ROOT / "services").rglob("manifest.yaml")),
        ids=lambda p: str(p.relative_to(_CATALOG_ROOT)),
    )
    def test_no_manifest_declares_both_keys(self, manifest_path):
        data = yaml.safe_load(manifest_path.read_text())
        if not isinstance(data, dict) or data.get("type") != "service":
            return
        req_ports = (data.get("requires") or {}).get("ports")
        inst_ports = (data.get("install") or {}).get("ports")
        if req_ports is not None and inst_ports is not None:
            pytest.fail(
                f"{manifest_path.relative_to(_CATALOG_ROOT)}: declares both "
                "requires.ports and install.ports -- pick one source of truth"
            )
