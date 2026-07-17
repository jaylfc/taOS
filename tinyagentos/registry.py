# tinyagentos/registry.py
from __future__ import annotations

import json
import threading
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
    # Weights license metadata, separate from `license` (the CODE license).
    # A manifest's runtime can be MIT while the model weights it pulls are
    # non-commercial (e.g. musicgen's CC-BY-NC 4.0 weights) -- these two
    # fields make that distinction explicit instead of leaving it implied by
    # `license` alone. weights_license is a free-text label ("CC-BY-NC 4.0");
    # license_class is "permissive" | "non-commercial" ("" = unknown/code-only).
    weights_license: str = ""
    license_class: str = ""
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
        return cls.from_dict(data, manifest_dir=path.parent)

    @classmethod
    def from_dict(cls, data: dict, manifest_dir: Path | None = None) -> AppManifest:
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
            weights_license=data.get("weights_license", ""),
            license_class=data.get("license_class", ""),
            requires=data.get("requires", {}),
            install=data.get("install", {}),
            hardware_tiers=data.get("hardware_tiers", {}),
            config_schema=data.get("config_schema", []),
            variants=data.get("variants", []),
            capabilities=data.get("capabilities", []),
            lifecycle=data.get("lifecycle", {}),
            manifest_dir=manifest_dir,
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
    def __init__(self, catalog_dir: Path, installed_path: Path, signing_key: bytes | None = None):
        self.catalog_dir = catalog_dir
        self.installed_path = installed_path
        self._signing_key = signing_key
        # Sentinel: None means catalog has not been loaded yet. Deferred so that
        # boot does not pay for walking + parsing every manifest under catalog_dir.
        self._catalog: list[AppManifest] | None = None
        # Manifest signatures keyed by app_id.  Populated during _load_catalog()
        # when a signing key is available.
        self._signatures: dict[str, str] = {}
        # Raw manifest dicts (stripped of _signature) keyed by app_id, used for
        # re-verifying at install time.
        self._manifest_dicts: dict[str, dict] = {}
        self._catalog_lock = threading.Lock()

    def _ensure_loaded(self) -> None:
        # Double-checked locking: cheap path when already loaded, lock only on first miss.
        if self._catalog is not None:
            return
        with self._catalog_lock:
            if self._catalog is None:
                self._load_catalog()

    def _load_catalog(self) -> None:
        catalog: list[AppManifest] = []
        signatures: dict[str, str] = {}
        manifest_dicts: dict[str, dict] = {}
        for type_dir in ("agents", "models", "services", "plugins"):
            base = self.catalog_dir / type_dir
            if not base.exists():
                continue
            for app_dir in sorted(base.iterdir()):
                manifest = app_dir / "manifest.yaml"
                if manifest.exists():
                    try:
                        raw_dict = yaml.safe_load(manifest.read_text())
                        catalog.append(AppManifest.from_dict(raw_dict, manifest_dir=app_dir))
                        if self._signing_key is not None:
                            from tinyagentos.store_signing import sign_manifest

                            sig = sign_manifest(raw_dict, self._signing_key)
                            signatures[catalog[-1].id] = sig
                            manifest_dicts[catalog[-1].id] = raw_dict
                    except (yaml.YAMLError, KeyError):
                        pass  # skip invalid manifests
        # Single atomic assignment: readers either see the old list or the fully built one.
        self._catalog = catalog
        self._signatures = signatures
        self._manifest_dicts = manifest_dicts

    def reload(self) -> None:
        with self._catalog_lock:
            self._load_catalog()

    def list_available(self, type_filter: str | None = None) -> list[AppManifest]:
        self._ensure_loaded()
        if type_filter:
            return [a for a in self._catalog if a.type == type_filter]
        return list(self._catalog)

    def get(self, app_id: str) -> AppManifest | None:
        self._ensure_loaded()
        return next((a for a in self._catalog if a.id == app_id), None)

    def get_signature(self, app_id: str) -> str | None:
        """Return the hex Ed25519 signature for *app_id*, or None."""
        self._ensure_loaded()
        return self._signatures.get(app_id)

    def get_manifest_dict(self, app_id: str) -> dict | None:
        """Return the raw manifest dict (without _signature field) for *app_id*."""
        self._ensure_loaded()
        return self._manifest_dicts.get(app_id)

    def verify_manifest_signature(self, app_id: str, public_pem: bytes) -> bool:
        """Re-verify the stored signature for *app_id* against *public_pem*.

        Returns False when the manifest has no signature or verification fails.
        This is called by install-v2 to detect post-boot catalog tampering.

        When the catalog was loaded without a signing key (e.g. a test
        instance or a deployment that hasn't enabled signing yet),
        ``install-v2`` skips this gate entirely by checking
        ``store_signing_pubkey`` on app state — so this method's False
        return does not inadvertently block installs in that case.
        """
        self._ensure_loaded()
        manifest_dict = self._manifest_dicts.get(app_id)
        sig = self._signatures.get(app_id)
        if manifest_dict is None or sig is None:
            # No signature stored (e.g. signing key not available at load time).
            return False
        from tinyagentos.store_signing import verify_manifest_signature

        return verify_manifest_signature(manifest_dict, sig, public_pem)

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
