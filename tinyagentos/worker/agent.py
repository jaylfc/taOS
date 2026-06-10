from __future__ import annotations
import asyncio
import json
import logging
import os
import platform
import socket
import time
from pathlib import Path
from urllib.parse import urlparse
import httpx
import psutil

logger = logging.getLogger(__name__)


# Marker dropped by install-worker.sh when it backs up an existing
# taos-worker-pool. The worker forwards it to the controller once on
# registration; controller materialises a notification + a workspace
# text file. Worker deletes the marker after a successful POST so it
# doesn't repeat the alert on every reconnect.
_STORAGE_BACKUP_MARKER = Path("/var/lib/tinyagentos-worker/storage-backup.json")


def _read_storage_backup_marker() -> dict | None:
    """Return the parsed storage-backup marker if present, else None.
    Errors swallowed — the marker is best-effort plumbing and must not
    break worker registration."""
    try:
        if not _STORAGE_BACKUP_MARKER.exists():
            return None
        return json.loads(_STORAGE_BACKUP_MARKER.read_text())
    except Exception:  # noqa: BLE001
        return None


def _delete_storage_backup_marker() -> None:
    try:
        _STORAGE_BACKUP_MARKER.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


def _detect_lan_ip(controller_url: str) -> str | None:
    """Return the local IPv4 address the worker would use to reach the
    controller — same address the controller sees the registration POST
    coming from. Used to populate `host_lan_ip` on registration so the
    install-targets matcher can link an incus remote to its worker even
    when the worker's `url` field points at an unrelated backend (e.g.
    the local Ollama on 127.0.0.1).

    Connectionless UDP: opening a socket and calling ``connect`` makes
    the kernel pick the outbound interface, but no packet is sent — we
    just read ``getsockname()`` and close. Falls back to ``None`` if
    the controller URL can't be parsed.
    """
    try:
        host = urlparse(controller_url).hostname
        if not host:
            return None
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((host, 9))  # UDP discard port; never delivered
            return s.getsockname()[0]
    except Exception:  # noqa: BLE001
        return None


class WorkerAgent:
    def __init__(
        self,
        controller_url: str,
        name: str | None = None,
        worker_port: int = 0,
        extra_capabilities: list[str] | None = None,
        advertise_url: str | None = None,
    ):
        self.controller_url = controller_url.rstrip("/")
        self.name = name or socket.gethostname()
        self.worker_port = worker_port
        self.extra_capabilities = list(extra_capabilities or [])
        self.advertise_url = advertise_url
        self._running = False
        self._registered = False

    async def detect_backends(self) -> list[dict]:
        """Discover locally running inference backends via live probing.

        Backend-driven: each candidate gets a live health check and, on
        success, a live model list so the controller sees what's actually
        loaded right now. Filename conventions and static capability
        declarations are not the source of truth anywhere.
        """
        from tinyagentos.scheduler.backend_catalog import BACKEND_CAPABILITIES

        # Probe both the standard upstream ports AND the TAOS-namespaced
        # ones. install-worker.sh installs a TAOS-bundled Ollama on
        # 21434 to avoid colliding with any existing user Ollama on
        # 11434; we want to detect both so the user's pre-existing
        # backends are first-class citizens alongside the bundled one.
        candidates = [
            ("rkllama", "http://localhost:8080"),
            ("ollama", "http://localhost:11434"),         # user / system Ollama (default port)
            ("ollama", "http://localhost:21434"),         # TAOS-bundled Ollama (taos-ollama.service)
            ("llama-cpp", "http://localhost:8000"),
            ("llama-cpp", "http://localhost:18080"),      # TAOS-bundled llama.cpp (future)
            ("vllm", "http://localhost:8000"),
            ("vllm", "http://localhost:18000"),           # TAOS-bundled vLLM (future)
            ("sd-cpp", "http://localhost:7864"),
            ("exo", "http://localhost:52415"),           # exo distributed inference (default port)
        ]

        backends = []
        async with httpx.AsyncClient(timeout=3) as client:
            for backend_type, base_url in candidates:
                models = await self._probe_models(client, backend_type, base_url)
                if models is None:
                    continue  # backend not running here
                loaded_models = await self._probe_loaded_models(client, backend_type, base_url)
                kv_quant = await self._probe_kv_quant(client, backend_type, base_url)
                backends.append({
                    "name": f"{backend_type}:{urlparse(base_url).port}",
                    "type": backend_type,
                    "url": base_url,
                    "capabilities": sorted(BACKEND_CAPABILITIES.get(backend_type, set())),
                    "models": models,
                    # Subset of `models` that are actually resident in NPU/GPU/CPU
                    # memory right now, so Activity's Loaded Models widget reflects
                    # real residency rather than the full catalog of downloads.
                    "loaded_models": loaded_models,
                    "status": "ok",
                    # Per-backend KV quant support, used by the worker to build
                    # its cluster-level kv_cache_quant_support advertisement.
                    "kv_quant_support": kv_quant,
                })
        return backends

    async def _probe_kv_quant(
        self, client: httpx.AsyncClient, backend_type: str, base_url: str
    ) -> dict:
        """Return separate K and V quant type lists and boundary-layer support.

        Returns a dict with three fields:
            k: list[str] of valid -ctk flag values for this backend
            v: list[str] of valid -ctv flag values for this backend
            boundary: bool, True if the backend can keep first/last N layers at fp16
                      while the middle layers use a different type

        Probe is best-effort, any network or parse error silently returns the
        safe default {k: ["fp16"], v: ["fp16"], boundary: False}. Image-gen
        backends return empty lists because KV quant is not applicable to
        diffusion pipelines.

        When a backend (e.g. a future vLLM build with TurboQuant merged, or
        TheTom/llama-cpp-turboquant exposed as a llama.cpp-compat worker)
        starts reporting a richer surface, it appears here and flows up to
        the cluster-wide union without any other code changes.

        Per research (Ziskind empirical, NexusQuant llama.cpp#21591), the
        correct shape is asymmetric: keys need more bits than values. A
        single flat list cannot express this because the safe K quants and
        safe V quants are different sets.

        TODO: once a backend actually ships a /v1/kv-quant-options or
        equivalent endpoint, replace the static stubs below with a live
        probe. Track in #144.
        """
        try:
            if backend_type == "sd-cpp":
                # Image-gen backends, KV quant is not applicable.
                return {"k": [], "v": [], "boundary": False}
            # All current real backends return the default until one of them
            # starts exposing a capability endpoint. The worker merely
            # advertises what the backend says it can do.
            return {"k": ["fp16"], "v": ["fp16"], "boundary": False}
        except Exception:
            return {"k": ["fp16"], "v": ["fp16"], "boundary": False}

    async def _probe_loaded_models(
        self, client: httpx.AsyncClient, backend_type: str, base_url: str
    ) -> list[dict]:
        """Ask a backend which of its models are *currently in memory* (not
        merely downloaded and available). Used to populate the 'Loaded
        Models' widget in the Activity app so it reflects real NPU/GPU
        residency, not the full catalog of pulled-but-idle models.

        Returns empty list on any failure — loaded-state is a best-effort
        signal and should never break heartbeat.
        """
        try:
            if backend_type in ("rkllama", "ollama"):
                resp = await client.get(f"{base_url}/api/ps")
                if resp.status_code != 200:
                    return []
                data = resp.json()
                return [
                    {
                        "name": m.get("model") or m.get("name", ""),
                        "size_mb": (m.get("size") or 0) // 1_000_000,
                    }
                    for m in data.get("models", [])
                ]
            # Other backend types don't expose an "in memory" state yet —
            # llama-cpp serves one model per process, vLLM similar, etc.
            # For those, "available" == "loaded" so the normal /v1/models
            # list is correct. Return [] here and let the caller fall back
            # to available-models.
            return []
        except Exception:
            return []

    async def _probe_models(
        self, client: httpx.AsyncClient, backend_type: str, base_url: str
    ) -> list[dict] | None:
        """Ask a backend what models it has loaded. Returns None if the
        backend isn't reachable (not running on this host)."""
        try:
            if backend_type in ("rkllama", "ollama"):
                resp = await client.get(f"{base_url}/api/tags")
                if resp.status_code != 200:
                    return None
                data = resp.json()
                return [
                    {
                        "name": m.get("model") or m.get("name", ""),
                        "size_mb": (m.get("size") or 0) // 1_000_000,
                    }
                    for m in data.get("models", [])
                ]
            if backend_type == "sd-cpp":
                resp = await client.get(f"{base_url}/sdapi/v1/sd-models")
                if resp.status_code != 200:
                    return None
                return [
                    {"name": m.get("title") or m.get("model_name") or "", "size_mb": 0}
                    for m in (resp.json() if isinstance(resp.json(), list) else [])
                ]
            # llama-cpp / vllm, OpenAI compat /v1/models
            resp = await client.get(f"{base_url}/v1/models")
            if resp.status_code != 200:
                return None
            data = resp.json()
            return [
                {"name": m.get("id", ""), "size_mb": 0}
                for m in data.get("data", [])
            ]
        except Exception:
            return None

    def detect_kv_quant_support(self, backends: list[dict]) -> dict:
        """Aggregate KV cache K/V quant support across all detected backends.

        Returns a dict with four fields:
            k: sorted list[str] of supported -ctk values across all LLM backends
            v: sorted list[str] of supported -ctv values across all LLM backends
            boundary: bool, True if ANY backend supports boundary-layer protect
            legacy: sorted list[str] union of k and v (backwards compat)

        Image-gen backends return empty dicts from the probe and are skipped.
        All LLM-capable backends contribute at minimum {"k": ["fp16"], "v": ["fp16"]}.
        """
        k_types: set[str] = set()
        v_types: set[str] = set()
        boundary = False
        for b in backends:
            per_backend = b.get("kv_quant_support")
            if not per_backend:
                continue
            if isinstance(per_backend, dict):
                k_types.update(per_backend.get("k") or [])
                v_types.update(per_backend.get("v") or [])
                boundary = boundary or bool(per_backend.get("boundary"))
            elif isinstance(per_backend, list):
                # Legacy shape: flat list. Apply to both K and V.
                k_types.update(per_backend)
                v_types.update(per_backend)
        # Always include fp16 as the baseline so protocol stays compatible
        # with consumers that expect at least one entry.
        k_types.add("fp16")
        v_types.add("fp16")
        return {
            "k": sorted(k_types),
            "v": sorted(v_types),
            "boundary": boundary,
            "legacy": sorted(k_types | v_types),
        }

    def get_container_runtime(self) -> str | None:
        """Detect available container runtime (docker or podman). Returns None if neither found."""
        import shutil
        if shutil.which("docker"):
            return "docker"
        if shutil.which("podman"):
            return "podman"
        return None

    def supports_streaming(self) -> bool:
        """Return True if a container runtime is available for streaming apps."""
        return self.get_container_runtime() is not None

    def detect_capabilities(self, backends: list[dict]) -> list[str]:
        """Union of capabilities across all detected backends.

        Backend-driven: each backend contributes its own advertised
        capability set. Modern detect_backends() attaches the live set
        from BACKEND_CAPABILITIES on probe; a caller passing a legacy
        shape (only ``type`` on each dict) still works because we fall
        back to BACKEND_CAPABILITIES by type. Streaming is added if a
        container runtime is present.
        """
        from tinyagentos.scheduler.backend_catalog import BACKEND_CAPABILITIES

        caps: set[str] = set()
        for b in backends:
            declared = b.get("capabilities")
            if declared:
                caps.update(declared)
                continue
            btype = b.get("type")
            if btype:
                caps.update(BACKEND_CAPABILITIES.get(btype, set()))
        if self.supports_streaming():
            caps.add("app-streaming")
        return sorted(caps)

    def get_worker_url(self) -> str:
        """Get this worker's reachable URL."""
        # Try to get LAN IP
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
        except Exception:
            ip = "127.0.0.1"
        return f"http://{ip}:{self.worker_port}" if self.worker_port else f"http://{ip}"

    async def register(self) -> bool:
        """Register with the controller."""
        from tinyagentos.hardware import detect_hardware
        from dataclasses import asdict

        hw = detect_hardware()
        backends = await self.detect_backends()
        caps = sorted(set(self.detect_capabilities(backends)) | set(self.extra_capabilities))
        kv_quant = self.detect_kv_quant_support(backends)

        # Use pinned advertise_url if provided; otherwise infer from backends or LAN IP.
        worker_url = self.advertise_url or (backends[0]["url"] if backends else self.get_worker_url())

        payload = {
            "name": self.name,
            "url": worker_url,
            "host_lan_ip": _detect_lan_ip(self.controller_url),
            "hardware": asdict(hw),
            "backends": backends,
            "capabilities": caps,
            "platform": platform.system().lower(),
            "models": [],
            "kv_cache_quant_support": kv_quant.get("legacy", ["fp16"]),
            "kv_cache_quant_k_support": kv_quant.get("k", ["fp16"]),
            "kv_cache_quant_v_support": kv_quant.get("v", ["fp16"]),
            "kv_cache_quant_boundary_layer_protect": bool(kv_quant.get("boundary", False)),
        }
        # Forward the storage-backup marker once. Controller materialises
        # a workspace text file + a notification so the user sees the
        # rename next time they open taOS.
        backup = _read_storage_backup_marker()
        if backup:
            payload["pending_storage_backup"] = backup

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(f"{self.controller_url}/api/cluster/workers", json=payload)
                resp.raise_for_status()
                self._registered = True
                logger.info(f"Registered with controller as '{self.name}'")
                if backup:
                    _delete_storage_backup_marker()
                return True
        except Exception as e:
            logger.error(f"Failed to register: {e}")
            return False

    async def heartbeat(self) -> int:
        """Send heartbeat to controller with live backend catalog.

        Backend-driven: the heartbeat carries a fresh probe of every
        detected backend, not a cached snapshot. This lets the controller
        aggregate per-worker catalogs into a cluster-wide view that
        reflects what's actually loaded right now across the mesh.

        Returns the HTTP status code from the controller, or 0 on
        connection failure / timeout. The caller uses this to detect
        the 404 case (controller restarted and forgot about us) and
        trigger a re-registration.
        """
        from tinyagentos.cluster.worker_capacity import capacity_snapshot

        try:
            load = psutil.cpu_percent() / 100.0
            backends = await self.detect_backends()
            caps = sorted(set(self.detect_capabilities(backends)) | set(self.extra_capabilities))
            kv_quant = self.detect_kv_quant_support(backends)
            snap = capacity_snapshot()
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.post(
                    f"{self.controller_url}/api/cluster/heartbeat",
                    json={
                        "name": self.name,
                        "load": load,
                        "backends": backends,
                        "capabilities": caps,
                        "kv_cache_quant_support": kv_quant.get("legacy", ["fp16"]),
                        "kv_cache_quant_k_support": kv_quant.get("k", ["fp16"]),
                        "kv_cache_quant_v_support": kv_quant.get("v", ["fp16"]),
                        "kv_cache_quant_boundary_layer_protect": bool(kv_quant.get("boundary", False)),
                        "storage_cap_bytes": snap["storage_cap_bytes"],
                        "storage_used_bytes": snap["storage_used_bytes"],
                        "bytes_deduped_total": snap["bytes_deduped_total"],
                    },
                )
                return resp.status_code
        except Exception:
            return 0

    async def run(self):
        """Main worker loop, register, heartbeat, re-register on loss.

        The controller's in-memory cluster registry is wiped on every
        controller restart. When that happens our heartbeats start
        coming back as 404 'Worker not registered'. Treat that as a
        signal to re-register and resume, without it, every controller
        restart leaves the cluster view empty until the worker is
        manually restarted.
        """
        self._running = True
        while self._running:
            # Register if we aren't (yet, or any more).
            if not self._registered:
                if await self.register():
                    logger.info(f"worker '{self.name}' registered with {self.controller_url}")
                else:
                    await asyncio.sleep(5)
                    continue

            status = await self.heartbeat()
            if status == 404:
                # Controller has forgotten about us (restart, manual
                # deregister, etc). Drop our registered state and the
                # next loop iteration will re-register.
                logger.warning(
                    f"controller returned 404 on heartbeat, re-registering '{self.name}'"
                )
                self._registered = False
            elif status == 0:
                # Network / DNS / controller-down. Don't drop the
                # registered flag yet; the controller may still know
                # us when it comes back. Just retry on next tick.
                pass
            await asyncio.sleep(5)

    def stop(self):
        self._running = False
