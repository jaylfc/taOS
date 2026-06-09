"""Backend catalog, live view of what every backend has loaded and can serve.

The backend-driven discovery mechanism: subsystems that need to answer
"what's available right now?" (Images routing, Models API, scheduler
admission, agent skills) read from this catalog instead of scanning the
filesystem or trusting a config file.

The catalog is populated by periodic polling of registered backend adapters
plus event-driven refresh on specific triggers (backend restart, model
download complete). Data is cached in-memory with freshness timestamps;
stale entries are marked but not immediately removed so the UI can show
"reconnecting" instead of silently hiding things.

See docs/design/resource-scheduler.md §Backend-driven discovery.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from tinyagentos.scheduler.resource_shape import BackendResourceShape, get_default_shape

logger = logging.getLogger(__name__)

# Mapping from backend type → set of capabilities that type provides.
# This is a static lookup because the type is a protocol contract, not a
# runtime property: an ollama-compatible server speaks /api/tags and does
# chat + embed by definition. Which *models* it has loaded is still a
# live probe via the adapter.
BACKEND_CAPABILITIES: dict[str, set[str]] = {
    "rkllama": {"llm-chat", "embedding", "reranking"},
    "ollama": {"llm-chat", "embedding"},
    "llama-cpp": {"llm-chat", "embedding"},
    "vllm": {"llm-chat"},
    "exo": {"llm-chat"},
    "mlx": {"llm-chat"},
    "openai": {"llm-chat", "embedding"},
    "anthropic": {"llm-chat"},
    "sd-cpp": {"image-generation"},
}


@dataclass
class BackendEntry:
    """One backend as seen by the catalog right now."""
    name: str
    type: str
    url: str
    status: str                         # "ok" | "error" | "stale"
    capabilities: set[str]              # derived from type + live probe
    models: list[dict]                  # live list from the backend's API
    priority: int                       # user-set routing priority
    last_healthy: Optional[float] = None
    last_probed: float = field(default_factory=time.time)
    error: Optional[str] = None
    lifecycle_state: str = "running"    # "stopped"|"starting"|"running"|"draining"|"stopping"
    auto_manage: bool = False
    keep_alive_minutes: int = 10
    enabled: bool = True

    def has_model(self, model_id: str) -> bool:
        """Fuzzy match against advertised model names, handles prefix/suffix
        variation like ``dreamshaper-8-lcm`` vs ``dreamshaper-8-lcm-iq4_nl``.
        """
        if not self.models:
            return False
        needle = model_id.lower()
        for m in self.models:
            name = (m.get("name") or m.get("id") or "").lower()
            if not name:
                continue
            if name == needle or name.startswith(needle) or needle.startswith(name):
                return True
        return False

    def get_resource_shape(self) -> BackendResourceShape:
        """Return the hardware resource shape for this backend type.

        The shape declares which dimensions (NPU cores, GPU IDs, memory)
        this backend can allocate. Used by CoreAwareModelScheduler to make
        load decisions without hard-coding backend knowledge in the scheduler.

        Override in a subclass or replace the entry in the catalog to
        supply hardware-specific details (e.g. actual vram_mb from a probe).
        """
        return get_default_shape(self.type)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "url": self.url,
            "status": self.status,
            "capabilities": sorted(self.capabilities),
            "models": self.models,
            "priority": self.priority,
            "last_healthy": self.last_healthy,
            "last_probed": self.last_probed,
            "error": self.error,
            "lifecycle_state": self.lifecycle_state,
            "auto_manage": self.auto_manage,
            "keep_alive_minutes": self.keep_alive_minutes,
            "enabled": self.enabled,
        }


class BackendCatalog:
    """Periodically polls backend adapters and caches the live state.

    Usage:
        catalog = BackendCatalog(backends, probe_fn, interval=30)
        await catalog.start()
        # later...
        entries = catalog.backends_with_capability("image-generation")
        await catalog.stop()

    The ``probe_fn`` is a coroutine that takes a backend dict
    ``{name, type, url, priority, ...}`` and returns a dict shaped like the
    adapters in ``tinyagentos/backend_adapters.py``:
    ``{status, response_ms, models}``.
    """

    def __init__(
        self,
        backends: list[dict],
        probe_fn: Callable[[dict], Awaitable[dict]],
        interval_seconds: float = 30.0,
        stale_after_seconds: float = 120.0,
    ):
        self._backends_config = list(backends)
        self._probe_fn = probe_fn
        self._interval = interval_seconds
        self._stale_after = stale_after_seconds
        self._entries: dict[str, BackendEntry] = {}
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._initial_probe_done = asyncio.Event()
        # Subscribers called whenever the backend state meaningfully
        # changes (status flip or model set diff). Each subscriber is an
        # async callable taking no arguments. Failures are logged and
        # isolated, one bad subscriber can't break the poll loop.
        self._subscribers: list[Callable[[], Awaitable[None]]] = []
        self._last_signature: str = ""
        self._lifecycle_states: dict[str, str] = {}

    def subscribe(self, callback: Callable[[], Awaitable[None]]) -> None:
        """Register an async callback for backend state changes.

        The callback fires after every probe pass that produces a
        different catalog state from the previous one. Use this to
        trigger derived-state rebuilds (e.g. LiteLLM config reload,
        scheduler resource re-registration).
        """
        self._subscribers.append(callback)

    def _catalog_signature(self) -> str:
        """Stable signature of the current catalog state. Changes when a
        backend's status flips, its model list changes, or its URL
        changes. Used to decide whether to fire subscriber callbacks."""
        parts = []
        for name in sorted(self._entries.keys()):
            entry = self._entries[name]
            model_names = tuple(
                sorted((m.get("name") or m.get("id") or "") for m in entry.models)
            )
            parts.append((name, entry.status, entry.url, model_names))
        return repr(parts)

    async def start(self) -> None:
        """Kick off the background polling task and wait for the first pass."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._poll_loop(), name="backend-catalog-poll")
        await asyncio.wait_for(self._initial_probe_done.wait(), timeout=15.0)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def refresh(self) -> None:
        """Force a single probe pass immediately, used after a backend change."""
        async with self._lock:
            await self._probe_all()

    def backends(self) -> list[BackendEntry]:
        """Snapshot of all known backends."""
        return list(self._entries.values())

    def backends_with_capability(self, capability: str) -> list[BackendEntry]:
        """All healthy, enabled, running backends for this capability, ordered by priority."""
        matches = [
            e for e in self._entries.values()
            if e.status == "ok"
            and e.enabled
            and e.lifecycle_state == "running"
            and capability in e.capabilities
        ]
        matches.sort(key=lambda e: e.priority)
        return matches

    def set_lifecycle_state(self, name: str, state: str) -> None:
        """Called by LifecycleManager to update a backend's lifecycle state."""
        self._lifecycle_states[name] = state

    def get_lifecycle_state(self, name: str) -> str:
        return self._lifecycle_states.get(name, "running")

    def backends_startable_for_capability(self, capability: str) -> list[BackendEntry]:
        """Backends that are stopped+auto_manage=true and could serve this capability.

        Returns a BackendEntry for each matching backend. If a backend has never
        been successfully probed (no entry in _entries), a synthetic entry is
        constructed from config so cold-start backends are not silently dropped.
        """
        out = []
        for b in self._backends_config:
            if not b.get("enabled", True):
                continue
            if not b.get("auto_manage", False):
                continue
            state = self._lifecycle_states.get(b["name"], "running")
            if state != "stopped":
                continue
            caps = self._capabilities_for_type(b["type"])
            if capability not in caps:
                continue
            entry = self._entries.get(b["name"])
            if entry is None:
                entry = BackendEntry(
                    name=b["name"],
                    type=b["type"],
                    url=b["url"],
                    status="error",
                    capabilities=caps,
                    models=[],
                    priority=b.get("priority", 99),
                    lifecycle_state="stopped",
                    auto_manage=True,
                    keep_alive_minutes=b.get("keep_alive_minutes", 10),
                    enabled=b.get("enabled", True),
                )
            out.append(entry)
        return out

    def find_backend_for_model(
        self, capability: str, model_id: Optional[str] = None
    ) -> Optional[BackendEntry]:
        """Best healthy backend for the given capability (+ optional model).

        If ``model_id`` is supplied and ANY healthy backend has it loaded,
        prefer that backend. Otherwise return the highest-priority healthy
        backend offering the capability (may need to load the model on first
        call).
        """
        candidates = self.backends_with_capability(capability)
        if not candidates:
            return None
        if model_id:
            exact = [b for b in candidates if b.has_model(model_id)]
            if exact:
                return exact[0]
        return candidates[0]

    def all_models(self, capability: Optional[str] = None) -> list[dict]:
        """Flat list of all loaded models across all backends. Used to join
        against the on-disk catalog for the "downloaded" marker in /api/models.
        """
        out: list[dict] = []
        for entry in self._entries.values():
            if entry.status != "ok":
                continue
            if capability and capability not in entry.capabilities:
                continue
            for m in entry.models:
                out.append({
                    **m,
                    "backend": entry.name,
                    "backend_type": entry.type,
                })
        return out

    def _capabilities_for_type(self, backend_type: str) -> set[str]:
        return set(BACKEND_CAPABILITIES.get(backend_type, set()))

    async def _poll_loop(self) -> None:
        try:
            while True:
                try:
                    async with self._lock:
                        await self._probe_all()
                        await self._notify_if_changed()
                except Exception:
                    logger.exception("backend catalog probe failed")
                if not self._initial_probe_done.is_set():
                    self._initial_probe_done.set()
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("backend catalog poll loop crashed")
            raise

    async def _notify_if_changed(self) -> None:
        """Fire subscriber callbacks if the catalog signature changed
        since the previous probe pass."""
        new_sig = self._catalog_signature()
        if new_sig == self._last_signature:
            return
        self._last_signature = new_sig
        for callback in self._subscribers:
            try:
                await callback()
            except Exception:
                logger.exception("backend catalog subscriber failed")

    async def _probe_all(self) -> None:
        now = time.time()
        active_backends = [b for b in self._backends_config if b.get("enabled", True)]
        results = await asyncio.gather(
            *[self._probe_one(b) for b in active_backends],
            return_exceptions=True,
        )
        for backend, result in zip(active_backends, results):
            name = backend["name"]
            auto_manage = backend.get("auto_manage", False)
            keep_alive_minutes = backend.get("keep_alive_minutes", 10)
            lifecycle_state = self._lifecycle_states.get(name, "running")
            if isinstance(result, Exception):
                self._mark_error(name, backend, str(result), now)
                continue
            if result.get("status") == "ok":
                self._entries[name] = BackendEntry(
                    name=name,
                    type=backend["type"],
                    url=backend["url"],
                    status="ok",
                    capabilities=self._capabilities_for_type(backend["type"]),
                    models=result.get("models", []),
                    priority=backend.get("priority", 99),
                    last_healthy=now,
                    last_probed=now,
                    error=None,
                    lifecycle_state=lifecycle_state,
                    auto_manage=auto_manage,
                    keep_alive_minutes=keep_alive_minutes,
                    enabled=backend.get("enabled", True),
                )
            else:
                self._mark_error(name, backend, result.get("error"), now)

    def _mark_error(self, name: str, backend: dict, err: Optional[str], now: float) -> None:
        existing = self._entries.get(name)
        last_healthy = existing.last_healthy if existing else None
        # stale grace period, we keep the last-known models around so the UI
        # can say "reconnecting" instead of silently clearing the dropdown
        status = "error"
        if last_healthy and (now - last_healthy) < self._stale_after:
            status = "stale"
        self._entries[name] = BackendEntry(
            name=name,
            type=backend["type"],
            url=backend["url"],
            status=status,
            capabilities=self._capabilities_for_type(backend["type"]),
            models=existing.models if existing else [],
            priority=backend.get("priority", 99),
            last_healthy=last_healthy,
            last_probed=now,
            error=err,
            lifecycle_state=self._lifecycle_states.get(name, "running"),
            auto_manage=backend.get("auto_manage", False),
            keep_alive_minutes=backend.get("keep_alive_minutes", 10),
            enabled=backend.get("enabled", True),
        )

    async def _probe_one(self, backend: dict) -> dict:
        return await self._probe_fn(backend)
