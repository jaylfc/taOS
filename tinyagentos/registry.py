# tinyagentos/registry.py
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import yaml


class AppState(str, Enum):
    """Possible states for an installed app."""
    AVAILABLE = "available"
    INSTALLED = "installed"
    RUNNING = "running"
    ERROR = "error"


@dataclass
class AppManifest:
    id: str
    name: str
    type: str                   # runtime classification: agent-framework | model | service | plugin
    version: str
    description: str = ""
    # Optional Store UI grouping. Defaults to empty; frontend falls back to type.
    # Lets services (type=service) surface under dev-tool, productivity, ai-app, etc.
    category: str = ""
    icon: str = ""
    homepage: str = ""
    license: str = ""
    requires: dict = field(default_factory=dict)
    install: dict = field(default_factory=dict)
    hardware_tiers: dict = field(default_factory=dict)
    config_schema: list = field(default_factory=list)
    variants: list = field(default_factory=list)   # models only
    capabilities: list = field(default_factory=list)
    lifecycle: dict = field(default_factory=dict)
    manifest_dir: Path | None = None

    @classmethod
    def from_file(cls, path: Path) -> AppManifest:
        data = yaml.safe_load(path.read_text())
        return cls(
            id=data["id"],
            name=data["name"],
            type=data["type"],
            version=data["version"],
            description=data.get("description", ""),
            category=data.get("category", ""),
            icon=data.get("icon", ""),
            homepage=data.get("homepage", ""),
            license=data.get("license", ""),
            requires=data.get("requires", {}),
            install=data.get("install", {}),
            hardware_tiers=data.get("hardware_tiers", {}),
            config_schema=data.get("config_schema", []),
            variants=data.get("variants", []),
            capabilities=data.get("capabilities", []),
            lifecycle=data.get("lifecycle", {}),
            manifest_dir=path.parent,
        )

    def is_compatible(self, profile_id: str) -> bool:
        if not self.hardware_tiers:
            return True  # no restrictions
        tier = self.hardware_tiers.get(profile_id)
        if tier is None:
            return False
        if isinstance(tier, str):
            return tier != "unsupported"
        if isinstance(tier, dict):
            return tier.get("recommended") is not None or tier.get("fallback") is not None
        return False


class AppRegistry:
    def __init__(self, catalog_dir: Path, installed_path: Path):
        self.catalog_dir = catalog_dir
        self.installed_path = installed_path
        # Sentinel: None means catalog has not been loaded yet. Deferred so that
        # boot does not pay for walking + parsing every manifest under catalog_dir.
        self._catalog: list[AppManifest] | None = None

    def _ensure_loaded(self) -> None:
        if self._catalog is None:
            self._load_catalog()

    def _load_catalog(self) -> None:
        self._catalog = []
        for type_dir in ("agents", "models", "services", "plugins"):
            base = self.catalog_dir / type_dir
            if not base.exists():
                continue
            for app_dir in sorted(base.iterdir()):
                manifest = app_dir / "manifest.yaml"
                if manifest.exists():
                    try:
                        self._catalog.append(AppManifest.from_file(manifest))
                    except (yaml.YAMLError, KeyError):
                        pass  # skip invalid manifests

    def reload(self) -> None:
        self._load_catalog()

    def list_available(self, type_filter: str | None = None) -> list[AppManifest]:
        self._ensure_loaded()
        if type_filter:
            return [a for a in self._catalog if a.type == type_filter]
        return list(self._catalog)

    def get(self, app_id: str) -> AppManifest | None:
        self._ensure_loaded()
        return next((a for a in self._catalog if a.id == app_id), None)

    def _read_installed(self) -> list[dict]:
        if not self.installed_path.exists():
            return []
        return json.loads(self.installed_path.read_text())

    def _write_installed(self, apps: list[dict]) -> None:
        self.installed_path.parent.mkdir(parents=True, exist_ok=True)
        self.installed_path.write_text(json.dumps(apps, indent=2))

    def list_installed(self) -> list[dict]:
        return self._read_installed()

    def is_installed(self, app_id: str) -> bool:
        return any(a["id"] == app_id for a in self._read_installed())

    def mark_installed(self, app_id: str, version: str, state: str = "installed") -> None:
        apps = self._read_installed()
        apps = [a for a in apps if a["id"] != app_id]
        apps.append({"id": app_id, "version": version, "state": state})
        self._write_installed(apps)

    def mark_uninstalled(self, app_id: str) -> None:
        apps = [a for a in self._read_installed() if a["id"] != app_id]
        self._write_installed(apps)
