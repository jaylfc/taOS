"""SearXNG catalog manifest: unpinned image, pull-before-up, usable engines.

Two things are asserted against the shipped manifest:

* the image is the floating ``searxng/searxng:latest`` (SearXNG is never
  version-pinned), so the generated compose re-pulls on every ``up`` and every
  install / Store update lands on the current upstream release; and
* the rendered ``settings.yml`` actually returns results from a home IP: bing
  is enabled (it works there but ships disabled upstream) and the json format
  stays on so agents can query it programmatically.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from tinyagentos.installers.docker_installer import DockerInstaller
from tinyagentos.registry import AppManifest

MANIFEST = (
    Path(__file__).resolve().parents[1]
    / "app-catalog"
    / "services"
    / "searxng"
    / "manifest.yaml"
)


def _manifest() -> AppManifest:
    return AppManifest.from_file(MANIFEST)


def _rendered_settings(manifest: AppManifest, tmp_path: Path) -> dict:
    """Write the manifest's config_files through the real installer path."""
    installer = DockerInstaller(apps_dir=tmp_path)
    installer._write_config_files("searxng", manifest.install)
    return yaml.safe_load((tmp_path / "searxng" / "settings.yml").read_text())


def test_image_tracks_upstream_instead_of_a_pin() -> None:
    assert _manifest().install["image"] == "searxng/searxng:latest"


def test_unpinned_image_is_pulled_before_up(tmp_path: Path) -> None:
    compose, _ = DockerInstaller(apps_dir=tmp_path)._generate_compose(
        "searxng", _manifest().install
    )
    assert compose["services"]["searxng"]["pull_policy"] == "always"


def test_rendered_settings_enable_bing_and_json(tmp_path: Path) -> None:
    settings = _rendered_settings(_manifest(), tmp_path)
    assert settings["use_default_settings"] is True
    assert {"name": "bing", "disabled": False} in settings["engines"]
    assert "json" in settings["search"]["formats"]