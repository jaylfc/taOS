# tinyagentos/registry.py
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


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


@dataclass(frozen=True)
class _CatalogState:
    """Immutable snapshot of the catalog at one point in time.

    The entire snapshot is atomically replaced on reload so no reader can
    observe a mix of old and new state (e.g. a manifest from the new catalog
    paired with an old signatures dict that lacks its signature).
    """

    catalog: list[AppManifest]
    signatures: dict[str, str]
    manifest_dicts: dict[str, dict]
    signing_failures: frozenset[str] = frozenset()


class AppRegistry:
    """Catalog + signing registry for the app store.

    The catalog state (manifests, signatures, raw dicts) is bundled into a
    single ``_CatalogState`` snapshot that is atomically replaced on reload.
    This prevents TOCTOU bugs where a reader could see a mix of old and new
    state (e.g. a new manifest from _catalog paired with an old _signatures
    dict that is missing its signature).
    """

    def __init__(self, catalog_dir: Path, installed_path: Path, signing_key: bytes | None = None):
        self.catalog_dir = catalog_dir
        self.installed_path = installed_path
        self._signing_key = signing_key
        # Sentinel: None means catalog has not been loaded yet.  Deferred so
        # that boot does not pay for walking + parsing every manifest under
        # catalog_dir.
        self._state: _CatalogState | None = None
        self._catalog_lock = threading.Lock()

    # -- backward-compat aliases so existing callers read through the snapshot --
    @property
    def _catalog(self) -> list[AppManifest] | None:
        return self._state.catalog if self._state is not None else None

    @property
    def _signatures(self) -> dict[str, str]:
        return self._state.signatures if self._state is not None else {}

    @property
    def _manifest_dicts(self) -> dict[str, dict]:
        return self._state.manifest_dicts if self._state is not None else {}

    def _ensure_loaded(self) -> None:
        # Double-checked locking: cheap path when already loaded, lock only on first miss.
        if self._state is not None:
            return
        with self._catalog_lock:
            if self._state is None:
                self._load_catalog()

    def _load_catalog(self) -> None:
        catalog: list[AppManifest] = []
        signatures: dict[str, str] = {}
        manifest_dicts: dict[str, dict] = {}
        signing_failures: set[str] = set()
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

                            try:
                                sig = sign_manifest(raw_dict, self._signing_key)
                                signatures[catalog[-1].id] = sig
                                manifest_dicts[catalog[-1].id] = raw_dict
                            except Exception:
                                logger.exception(
                                    "failed to sign manifest %s — install gate will block it",
                                    catalog[-1].id,
                                )
                                signing_failures.add(catalog[-1].id)
                    except (yaml.YAMLError, KeyError):
                        pass  # skip invalid manifests
        # Single atomic assignment: the entire snapshot is replaced at once,
        # so readers never see a mix of old and new state (e.g. a new manifest
        # from the new catalog paired with an old signatures dict).
        self._state = _CatalogState(
            catalog=catalog,
            signatures=signatures,
            manifest_dicts=manifest_dicts,
            signing_failures=frozenset(signing_failures),
        )

    def reload(self) -> None:
        with self._catalog_lock:
            self._load_catalog()

    def is_signing_failure(self, app_id: str) -> bool:
        """Return True if *app_id* failed to sign during catalog load.

        When signing is configured and a manifest fails to sign, the
        install gate should block it rather than treating it as merely
        unsigned (which would be a bypass).
        """
        self._ensure_loaded()
        return app_id in self._state.signing_failures

    def set_signing_key(self, key: bytes | None) -> None:
        """Set (or clear) the signing key and reload the catalog.

        Call this after the keypair has been loaded (e.g. in the lifespan)
        so the catalog is signed with the actual key.  Passing ``None``
        disables signing.
        """
        self._signing_key = key
        self.reload()

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

        **Re-reads the manifest from disk at verify time**, then checks the
        stored Ed25519 signature (computed at catalog-load time) against the
        canonical bytes of the current on-disk YAML.  This detects post-boot
        catalog tampering — an attacker who modifies ``manifest.yaml`` after
        the server started will produce a mismatch and the install is blocked.

        Returns ``True`` only when the on-disk manifest successfully verifies
        against the stored Ed25519 signature.

        Returns ``False`` when:

        * no signature was stored for this app (unsigned — fail-closed), **or**
        * the on-disk manifest does not verify against the stored signature
          (tampered).

        This primitive is fail-closed on purpose: a future caller that does
        ``if not registry.verify_manifest_signature(...)`` gets the safe
        default.  Callers that need a fail-open policy for unsigned manifests
        (e.g. the install gate, which must not block catalog entries that
        predate the signing feature) must check ``get_signature(app_id)``
        first and short-circuit before calling this method.  See
        ``_verify_manifest_for_install`` in ``routes/store_install.py`` for
        the canonical fail-open pattern.
        """
        self._ensure_loaded()
        sig = self._signatures.get(app_id)
        if sig is None:
            # Never signed — fail-closed.  The absence of a signature
            # means there is nothing to verify against.  Callers that
            # want a fail-open policy for unsigned manifests must check
            # get_signature() first.
            return False
        manifest = self.get(app_id)
        if manifest is None or manifest.manifest_dir is None:
            return False
        manifest_path = manifest.manifest_dir / "manifest.yaml"
        if not manifest_path.exists():
            return False
        try:
            on_disk = yaml.safe_load(manifest_path.read_text())
        except (yaml.YAMLError, OSError):
            return False
        from tinyagentos.store_signing import verify_manifest_signature

        return verify_manifest_signature(on_disk, sig, public_pem)

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
