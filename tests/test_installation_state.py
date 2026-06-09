"""Unit tests for the InstallationState helper.

Covers the four states (running / installed / stale / not_installed)
and the union-of-cache-plus-live-probe logic for the registry and
backend catalog.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from tinyagentos.installation_state import InstallationState


# ---------------------------------------------------------------------------
# Fake registry + backend catalog that match the contract without pulling
# in the real classes. Keeps these tests isolated from catalog file I/O.
# ---------------------------------------------------------------------------


@dataclass
class FakeManifest:
    id: str
    type: str
    version: str = "1.0"
    variants: list[dict] = field(default_factory=list)


class FakeRegistry:
    def __init__(self, manifests: list[FakeManifest], installed: list[dict]):
        self._manifests = {m.id: m for m in manifests}
        self._installed = installed

    def get(self, app_id: str) -> FakeManifest | None:
        return self._manifests.get(app_id)

    def list_available(self, type_filter: str | None = None) -> list[FakeManifest]:
        ms = list(self._manifests.values())
        return [m for m in ms if not type_filter or m.type == type_filter]

    def list_installed(self) -> list[dict]:
        return list(self._installed)

    def is_installed(self, app_id: str) -> bool:
        return any(e.get("id") == app_id for e in self._installed)


@dataclass
class FakeBackendEntry:
    name: str
    type: str
    status: str = "ok"


class FakeCatalog:
    def __init__(self, entries: list[FakeBackendEntry], models: list[dict]):
        self._entries = entries
        self._models = models

    def backends(self) -> list[FakeBackendEntry]:
        return list(self._entries)

    def all_models(self) -> list[dict]:
        return list(self._models)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_service_running_when_backend_catalog_has_matching_entry():
    reg = FakeRegistry(
        manifests=[FakeManifest("stable-diffusion-cpp", "service")],
        installed=[{"id": "stable-diffusion-cpp", "version": "1.0"}],
    )
    catalog = FakeCatalog(
        entries=[FakeBackendEntry("local-sd-cpp", "sd-cpp")],
        models=[],
    )
    state = InstallationState(reg, catalog)
    assert state.is_installed("stable-diffusion-cpp") is True
    assert state.state("stable-diffusion-cpp") == "running"


def test_service_stale_when_cache_says_installed_but_catalog_empty():
    reg = FakeRegistry(
        manifests=[FakeManifest("stable-diffusion-cpp", "service")],
        installed=[{"id": "stable-diffusion-cpp", "version": "1.0"}],
    )
    catalog = FakeCatalog(entries=[], models=[])
    state = InstallationState(reg, catalog)
    assert state.is_installed("stable-diffusion-cpp") is True
    assert state.state("stable-diffusion-cpp") == "stale"


def test_service_not_installed_when_neither_cache_nor_live():
    reg = FakeRegistry(
        manifests=[FakeManifest("stable-diffusion-cpp", "service")],
        installed=[],
    )
    catalog = FakeCatalog(entries=[], models=[])
    state = InstallationState(reg, catalog)
    assert state.is_installed("stable-diffusion-cpp") is False
    assert state.state("stable-diffusion-cpp") == "not_installed"


def test_service_live_without_cache_is_running():
    """User manually started a service — it shows up as running even
    without a corresponding installed.json row."""
    reg = FakeRegistry(
        manifests=[FakeManifest("stable-diffusion-cpp", "service")],
        installed=[],
    )
    catalog = FakeCatalog(
        entries=[FakeBackendEntry("local-sd-cpp", "sd-cpp")],
        models=[],
    )
    state = InstallationState(reg, catalog)
    assert state.is_installed("stable-diffusion-cpp") is True
    assert state.state("stable-diffusion-cpp") == "running"


def test_model_running_when_catalog_advertises_variant():
    reg = FakeRegistry(
        manifests=[FakeManifest(
            "dreamshaper-8-lcm",
            "model",
            variants=[{"id": "iq4_nl-gguf"}, {"id": "iq2_xs-gguf"}],
        )],
        installed=[],
    )
    catalog = FakeCatalog(
        entries=[],
        models=[{"name": "dreamshaper-8-lcm-iq4_nl-gguf"}],
    )
    state = InstallationState(reg, catalog)
    assert state.is_installed("dreamshaper-8-lcm") is True
    assert state.state("dreamshaper-8-lcm") == "running"


def test_model_not_matched_when_no_variant_name_appears():
    reg = FakeRegistry(
        manifests=[FakeManifest(
            "dreamshaper-8-lcm",
            "model",
            variants=[{"id": "iq4_nl-gguf"}],
        )],
        installed=[],
    )
    catalog = FakeCatalog(
        entries=[],
        models=[{"name": "unrelated-model"}],
    )
    state = InstallationState(reg, catalog)
    assert state.is_installed("dreamshaper-8-lcm") is False


def test_agent_type_falls_back_to_cache_without_stale_flag():
    """Agent frameworks have no live probe surface — cache is source of
    truth and state() reports 'installed' not 'stale'."""
    reg = FakeRegistry(
        manifests=[FakeManifest("smolagents", "agent")],
        installed=[{"id": "smolagents", "version": "0.1"}],
    )
    catalog = FakeCatalog(entries=[], models=[])
    state = InstallationState(reg, catalog)
    assert state.is_installed("smolagents") is True
    # Even with an empty catalog, agents stay 'installed' because they
    # don't expose a live probe surface yet.
    assert state.state("smolagents") == "installed"


def test_list_installed_tags_cache_rows_with_state():
    reg = FakeRegistry(
        manifests=[
            FakeManifest("stable-diffusion-cpp", "service"),
            FakeManifest("smolagents", "agent"),
        ],
        installed=[
            {"id": "stable-diffusion-cpp", "version": "1.0"},
            {"id": "smolagents", "version": "0.1"},
        ],
    )
    catalog = FakeCatalog(
        entries=[FakeBackendEntry("local-sd-cpp", "sd-cpp")],
        models=[],
    )
    state = InstallationState(reg, catalog)
    rows = state.list_installed()
    states = {r["id"]: r["state"] for r in rows}
    assert states["stable-diffusion-cpp"] == "running"
    assert states["smolagents"] == "installed"
    sources = {r["id"]: r["source"] for r in rows}
    assert sources["stable-diffusion-cpp"] == "cache"


def test_list_installed_includes_live_only_services():
    """A manually-started service shows up in list_installed even
    without a cache row."""
    reg = FakeRegistry(
        manifests=[FakeManifest("stable-diffusion-cpp", "service")],
        installed=[],
    )
    catalog = FakeCatalog(
        entries=[FakeBackendEntry("local-sd-cpp", "sd-cpp")],
        models=[],
    )
    state = InstallationState(reg, catalog)
    rows = state.list_installed()
    assert len(rows) == 1
    assert rows[0]["id"] == "stable-diffusion-cpp"
    assert rows[0]["state"] == "running"
    assert rows[0]["source"] == "live"


def test_without_catalog_falls_back_to_cache_only():
    """When no catalog is wired (tests / startup race), the helper
    behaves exactly like the registry."""
    reg = FakeRegistry(
        manifests=[FakeManifest("smolagents", "agent")],
        installed=[{"id": "smolagents", "version": "0.1"}],
    )
    state = InstallationState(reg, backend_catalog=None)
    assert state.is_installed("smolagents") is True
    assert state.state("smolagents") == "installed"
    rows = state.list_installed()
    assert [r["id"] for r in rows] == ["smolagents"]


def test_installed_count_matches_list_length():
    reg = FakeRegistry(
        manifests=[
            FakeManifest("a", "agent"),
            FakeManifest("b", "service"),
        ],
        installed=[
            {"id": "a", "version": "1"},
            {"id": "b", "version": "1"},
        ],
    )
    catalog = FakeCatalog(entries=[], models=[])
    state = InstallationState(reg, catalog)
    assert state.installed_count() == len(state.list_installed())
