"""Backend-driven view of app / model / service install state.

The app registry (``tinyagentos/registry.py``) owns ``installed.json``
as a durable breadcrumb: when a user clicks Install on the Store, we
write a row; when they Uninstall, we remove it. This file is reliable
as a record of user intent but drifts from runtime reality — a service
might be marked installed but its systemd unit is dead, or a model
might be marked missing but is actually sitting loaded on a live backend
after a manual `qmd serve` restart.

``InstallationState`` is the backend-driven view on top of that cache.
It joins the registry's persistent record with live probes of the
BackendCatalog (for services and models) so the Store / Models /
Dashboard UIs see what's actually usable right now, not what the
breadcrumb file last said.

Live probe policy by app type:

- **service**: the backend catalog is the source of truth. If any
  healthy entry advertises a URL that matches the service manifest's
  default ports or declared URL, the service is live. If the catalog
  has no entry but the breadcrumb says installed, the service is
  stale (reachable previously, not now).
- **model**: the catalog's ``all_models()`` flat list is the source
  of truth. Fuzzy match on manifest id + variant id (same matcher
  used by /api/models).
- **agent (framework)** and **plugin**: no live probe surface today;
  fall back to the registry cache. These move to live probing in a
  later pass once a per-type health endpoint exists.

Write operations (``mark_installed`` / ``mark_uninstalled``) stay on
the registry directly — the cache file is still authoritative for
"what the user asked us to install", which is a different question
from "what's running right now".
"""
from __future__ import annotations

from typing import Any, Iterable, Optional


# Static map from catalog service manifest id → set of backend types
# that satisfy it. The pragmatic alternative to making every manifest
# declare its backend_type field. Alias map wins over fuzzy match.
_SERVICE_BACKEND_ALIASES: dict[str, set[str]] = {
    "stable-diffusion-cpp": {"sd-cpp"},
    "fastsdcpp": {"sd-cpp"},
    "rk-llama-cpp": {"rkllama"},
    "ollama": {"ollama"},
    "open-webui": {"ollama"},
}


class InstallationState:
    """Joined view of the registry cache + live backend catalog."""

    def __init__(self, registry: Any, backend_catalog: Any | None = None):
        self._registry = registry
        self._catalog = backend_catalog

    # ------------------------------------------------------------------
    # Public read surface used by the Store / Models / Dashboard routes
    # ------------------------------------------------------------------

    def is_installed(self, app_id: str) -> bool:
        """True if the app is usable right now OR the cache says so.

        Union of live probe and cache. A service that's reachable but
        missing from installed.json still counts (rare — usually means
        the user installed it manually). A service in installed.json
        that's currently offline still counts as installed, but its
        ``state()`` will report ``stale`` so the UI can flag it.
        """
        if self._live_installed(app_id):
            return True
        return self._registry.is_installed(app_id)

    def state(self, app_id: str) -> str:
        """Fine-grained state. One of:

        - ``running`` — live probe succeeds; the app is usable now
        - ``installed`` — in the cache, but we have no live signal
                           (normal for agents/plugins that don't have
                           a probe surface yet)
        - ``stale`` — in the cache AND the probe surface exists, but
                       the probe says not reachable. Use this to
                       surface "reconnecting" UI rather than hiding.
        - ``not_installed`` — neither live nor cached
        """
        manifest = self._registry.get(app_id)
        if manifest is None:
            return "not_installed"

        in_cache = self._registry.is_installed(app_id)
        has_probe_surface = self._has_live_probe_surface(manifest.type)

        if self._live_installed(app_id):
            return "running"
        if in_cache:
            return "stale" if has_probe_surface else "installed"
        return "not_installed"

    def list_installed(self) -> list[dict]:
        """Union of cached installed entries + live-only entries.

        Every row has a ``state`` field so callers can filter or sort.
        Rows sourced from live probe only (no cache entry) are marked
        ``"state": "running"``.
        """
        cached = self._registry.list_installed()
        seen = {entry.get("id"): entry for entry in cached}

        out: list[dict] = []
        for entry in cached:
            app_id = entry.get("id")
            manifest = self._registry.get(app_id) if app_id else None
            live = self._live_installed(app_id) if app_id else False
            has_probe = (
                self._has_live_probe_surface(manifest.type) if manifest else False
            )
            state = "running" if live else ("stale" if has_probe else "installed")
            out.append({**entry, "state": state, "source": "cache"})

        # Live-only rows for apps the user never explicitly installed but
        # whose backend is advertising right now (manual installs, systemd
        # units started by hand).
        if self._catalog is not None:
            for app in self._live_only_apps(seen):
                out.append(app)

        return out

    def installed_count(self) -> int:
        return len(self.list_installed())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _has_live_probe_surface(self, app_type: str) -> bool:
        return app_type in ("service", "model") and self._catalog is not None

    def _live_installed(self, app_id: str) -> bool:
        if self._catalog is None or not app_id:
            return False
        manifest = self._registry.get(app_id)
        if manifest is None:
            return False
        if manifest.type == "service":
            return self._service_live(manifest)
        if manifest.type == "model":
            return self._model_live(manifest)
        return False

    def _service_live(self, manifest: Any) -> bool:
        """A service is live if any healthy backend in the catalog matches
        it. Match strategy, in order:

        1. Static alias map (``_SERVICE_BACKEND_ALIASES``) — common
           alias cases (e.g. stable-diffusion-cpp ↔ sd-cpp)
        2. Exact or substring match between manifest id and backend name
           or type
        3. Shared token (length ≥ 4) — catches "*-rknn-*" style naming
           without false-positives on generic tokens like ``cpp`` or
           ``api``
        """
        try:
            entries = self._catalog.backends()
        except Exception:
            return False

        aliases = _SERVICE_BACKEND_ALIASES.get(manifest.id, set())
        needle = manifest.id.lower()
        needle_tokens = {t for t in needle.split("-") if len(t) >= 4}

        for entry in entries:
            if entry.status != "ok":
                continue
            btype = (entry.type or "").lower()
            name = (entry.name or "").lower()

            if btype in aliases:
                return True
            if needle in name or needle in btype:
                return True
            if btype in needle or name.endswith(needle) or name.startswith(needle):
                return True

            entry_tokens = {t for t in (btype.split("-") + name.split("-")) if len(t) >= 4}
            if needle_tokens & entry_tokens:
                return True

        return False

    def _model_live(self, manifest: Any) -> bool:
        """A model is live if any healthy backend in the catalog advertises
        a name that matches the manifest id or any of its variants."""
        try:
            models = self._catalog.all_models()
        except Exception:
            return False
        manifest_id_l = manifest.id.lower()
        variant_ids_l = [
            (v.get("id") or "").lower()
            for v in (manifest.variants or [])
            if v.get("id")
        ]
        for m in models:
            name = (m.get("name") or m.get("id") or "").lower()
            if not name:
                continue
            if name == manifest_id_l or name.startswith(manifest_id_l):
                return True
            for vid in variant_ids_l:
                if vid and vid in name:
                    return True
        return False

    def _live_only_apps(self, seen_ids: dict[str, Any]) -> Iterable[dict]:
        """Yield apps that are live in the catalog but not in the cache.

        This handles the "user manually started a service outside the
        Store" case — the service shows up as running without needing
        the user to hit Install first.
        """
        if self._catalog is None:
            return []
        try:
            entries = self._catalog.backends()
        except Exception:
            return []
        yielded: set[str] = set()
        out: list[dict] = []
        for entry in entries:
            if entry.status != "ok":
                continue
            # Try to match an entry back to a catalog manifest by type/id
            for manifest in self._registry.list_available(type_filter="service"):
                if manifest.id in seen_ids or manifest.id in yielded:
                    continue
                if self._service_live(manifest):
                    out.append({
                        "id": manifest.id,
                        "version": manifest.version,
                        "state": "running",
                        "source": "live",
                    })
                    yielded.add(manifest.id)
        return out
