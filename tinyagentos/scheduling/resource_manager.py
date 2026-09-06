"""Dynamic Resource Manager (taOSmd).

Discovers available hardware and models, tracks resource utilisation,
and dynamically adjusts the job queue's concurrency limits.

Designed for taOS's cluster architecture:
  - Controller (Pi) has NPU + CPU
  - Workers (GPU boxes, laptops, phones) join/leave dynamically
  - Models load/unload as agents start/stop
  - VRAM/RAM pressure changes throughout the day

The manager runs a periodic refresh that:
  1. Probes local hardware (CPU cores, NPU presence, GPU, RAM)
  2. Queries the taOS cluster API for worker capabilities
  3. Checks Ollama for loaded models
  4. Updates the job queue's concurrency limits accordingly

Pull-based: the job queue doesn't dispatch to workers — workers pull jobs
they can handle. The manager just ensures the limits reflect reality.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

from .worker_heartbeat import WorkerRegistry

if TYPE_CHECKING:
    from .job_queue import JobQueue

logger = logging.getLogger(__name__)


def _count_cpu_cores() -> int:
    """Detect usable CPU cores."""
    try:
        return os.cpu_count() or 2
    except Exception:
        return 2


def _detect_npu() -> int:
    """Detect RK3588 NPU cores. Returns 0 if no NPU."""
    # RK3588 has 3 NPU cores exposed via /proc/device-tree
    npu_path = Path("/sys/class/misc/mali0")  # Mali indicates RK3588
    rknn_path = Path("/proc/device-tree/npu")
    if rknn_path.exists() or npu_path.exists():
        return 3  # RK3588 always has 3 cores
    return 0


def _detect_gpu() -> dict:
    """Detect GPU via nvidia-smi. Returns {name, vram_mb, count} or empty dict."""
    try:
        import subprocess
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,count", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            line = result.stdout.strip().split("\n")[0]
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 2:
                return {
                    "name": parts[0],
                    "vram_mb": int(float(parts[1])),
                    "count": int(parts[2]) if len(parts) > 2 else 1,
                }
    except Exception:
        pass
    return {}


def _get_available_ram_mb() -> int:
    """Get available RAM in MB."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    return 0


async def _check_ollama_models(ollama_url: str = "http://localhost:11434") -> list[str]:
    """Query Ollama for available models."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{ollama_url}/api/tags")
            if resp.status_code == 200:
                return [m["name"] for m in resp.json().get("models", [])]
    except Exception:
        pass
    return []


async def _check_cluster_workers(controller_url: str = "") -> list[dict]:
    """Query taOS controller for cluster worker capabilities."""
    if not controller_url:
        return []
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{controller_url}/api/cluster/workers")
            if resp.status_code == 200:
                return resp.json().get("workers", [])
    except Exception:
        pass
    return []


class ResourceSnapshot:
    """Point-in-time snapshot of available resources."""

    def __init__(self):
        self.timestamp: float = time.time()
        self.cpu_cores: int = 0
        self.npu_cores: int = 0
        self.gpu: dict = {}
        self.ram_available_mb: int = 0
        self.ollama_models: list[str] = []
        self.cluster_workers: list[dict] = []

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "cpu_cores": self.cpu_cores,
            "npu_cores": self.npu_cores,
            "gpu": self.gpu,
            "ram_available_mb": self.ram_available_mb,
            "ollama_models": self.ollama_models,
            "cluster_workers": len(self.cluster_workers),
        }

    @property
    def has_gpu(self) -> bool:
        return bool(self.gpu)

    @property
    def has_npu(self) -> bool:
        return self.npu_cores > 0

    @property
    def has_ollama(self) -> bool:
        return len(self.ollama_models) > 0

    @property
    def total_gpu_workers(self) -> int:
        """Count cluster workers with GPU capability."""
        return sum(1 for w in self.cluster_workers if w.get("gpu"))


class ResourceManager:
    """Discovers resources and dynamically adjusts job queue limits."""

    def __init__(
        self,
        job_queue: JobQueue | None = None,
        worker_registry: WorkerRegistry | None = None,
        ollama_url: str = "http://localhost:11434",
        controller_url: str = "",
        refresh_interval: int = 60,
        contention_threshold: int = 30,
        idle_upgrade_delay: int = 600,
    ):
        self._queue = job_queue
        self._registry = worker_registry
        self._ollama_url = ollama_url
        self._controller_url = controller_url
        self._refresh_interval = refresh_interval
        self._contention_threshold = contention_threshold  # Seconds of busy GPU before downgrade
        self._idle_upgrade_delay = idle_upgrade_delay  # Seconds of idle GPU before upgrade back (10 min)
        self._last_refresh: float = 0
        self._snapshot: ResourceSnapshot | None = None
        self._prev_snapshot: ResourceSnapshot | None = None
        self._worker_busy_since: dict[str, float] = {}  # worker_name → timestamp
        self._worker_idle_since: dict[str, float] = {}  # worker_name → timestamp
        self._yielded: bool = False  # True when user has claimed the machine

    # ------------------------------------------------------------------
    # Yield mode — user wants the machine back
    # ------------------------------------------------------------------

    async def yield_resources(self) -> dict:
        """User is actively using this machine. Throttle worker to background.

        Reduces all limits to minimum: 1 CPU core, no GPU, no NPU.
        Called from system tray icon or taOS desktop toggle.
        """
        self._yielded = True
        if self._queue:
            await self._queue.set_limit("cpu", 1)
            await self._queue.set_limit("gpu", 0)
            await self._queue.set_limit("npu", 0)
            await self._queue.set_limit("embed", 1)  # Keep 1 for lightweight queries
        logger.info("Resources yielded — worker throttled to background mode")
        return {"mode": "yielded", "cpu": 1, "gpu": 0, "npu": 0}

    async def reclaim_resources(self) -> dict:
        """User is done. Worker reclaims all available resources.

        Triggers a fresh hardware probe to set limits based on actual hardware.
        """
        self._yielded = False
        snap = await self.refresh()  # Re-probe and apply full limits
        limits = await self._queue.get_limits() if self._queue else {}
        logger.info("Resources reclaimed — worker at full capacity")
        return {"mode": "full", **limits}

    @property
    def is_yielded(self) -> bool:
        return self._yielded

    async def refresh(self) -> ResourceSnapshot:
        """Probe all resources and update the snapshot."""
        snap = ResourceSnapshot()
        snap.cpu_cores = _count_cpu_cores()
        snap.npu_cores = _detect_npu()
        snap.gpu = _detect_gpu()
        snap.ram_available_mb = _get_available_ram_mb()
        snap.ollama_models = await _check_ollama_models(self._ollama_url)

        # Get cluster workers from registry (preferred) or controller API (fallback)
        if self._registry:
            snap.cluster_workers = await self._registry.for_resource_manager()
        else:
            snap.cluster_workers = await _check_cluster_workers(self._controller_url)

        self._snapshot = snap
        self._last_refresh = time.time()

        # Update job queue limits based on discovered resources
        if self._queue:
            await self._apply_limits(snap)

        logger.info(
            "Resource refresh: %d CPU, %d NPU, GPU=%s, RAM=%dMB, models=%d, workers=%d",
            snap.cpu_cores, snap.npu_cores,
            snap.gpu.get("name", "none"), snap.ram_available_mb,
            len(snap.ollama_models), len(snap.cluster_workers),
        )
        return snap

    async def get_snapshot(self, force_refresh: bool = False) -> ResourceSnapshot:
        """Get the current resource snapshot, refreshing if stale."""
        now = time.time()
        if force_refresh or not self._snapshot or (now - self._last_refresh) > self._refresh_interval:
            return await self.refresh()
        return self._snapshot

    async def _apply_limits(self, snap: ResourceSnapshot) -> None:
        """Calculate and apply concurrency limits based on discovered resources."""
        # If user has yielded, don't override throttled limits
        if self._yielded:
            return

        # Full power mode — use all available resources
        # CPU: all cores available (worker is greedy by default)
        cpu_limit = max(1, snap.cpu_cores)
        await self._queue.set_limit("cpu", cpu_limit)

        # NPU: all cores available
        npu_limit = snap.npu_cores if snap.npu_cores > 0 else 0
        await self._queue.set_limit("npu", npu_limit)

        # GPU: 1 per local GPU + 1 per cluster GPU worker
        gpu_count = snap.gpu.get("count", 0) + snap.total_gpu_workers
        await self._queue.set_limit("gpu", max(gpu_count, 0))

        # Embedding: 1 per device (ONNX session is not thread-safe)
        await self._queue.set_limit("embed", 1)

        # RAM-based throttling: if available RAM < 1GB, reduce all limits
        if snap.ram_available_mb < 1024:
            logger.warning("Low RAM (%dMB available), throttling job concurrency", snap.ram_available_mb)
            await self._queue.set_limit("cpu", 1)
            await self._queue.set_limit("npu", min(npu_limit, 1))

    # Approximate RAM requirements per model (MB) for CPU inference
    # GPU/NPU have separate VRAM so this only matters for CPU workers
    MODEL_RAM_MB = {
        "qwen3.5:0.8b": 800,
        "qwen3.5:2b": 2000,
        "qwen3:4b": 3300,
        "qwen3.5:4b": 3400,
        "qwen3.5:9b": 6600,
        "qwen3.5:27b": 17000,
    }

    def _model_fits_in_ram(self, model: str, available_mb: int) -> bool:
        """Check if a model fits in available RAM (for CPU inference)."""
        for name, required in self.MODEL_RAM_MB.items():
            if name in model.lower():
                # Need model + 1GB headroom for OS/agents
                return available_mb > (required + 1024)
        # Unknown model — assume it fits if >4GB available
        return available_mb > 4096

    async def best_model_for_task(self, task_type: str) -> dict:
        """Recommend the best available model for a task type.

        Checks RAM availability for CPU models, VRAM for GPU models.
        Returns {model, resource_type, location} or empty dict if none available.
        """
        snap = await self.get_snapshot()

        if task_type in ("extract", "enrich", "crystallize"):
            # Preference order: GPU (no RAM constraint) > NPU > CPU (RAM check)
            # Sort models by size descending — try the best first
            preferred_order = ["qwen3.5:27b", "qwen3.5:9b", "qwen3.5:4b", "qwen3:4b", "qwen3.5:2b", "qwen3.5:0.8b"]

            for preferred in preferred_order:
                for model in snap.ollama_models:
                    if preferred not in model.lower():
                        continue

                    if snap.has_gpu:
                        # GPU — VRAM handles model, RAM doesn't matter
                        return {"model": model, "resource_type": "gpu", "location": "local"}
                    elif snap.has_npu and "4b" in preferred:
                        # NPU — only supports 4B models currently
                        return {"model": model, "resource_type": "npu", "location": "local"}
                    elif self._model_fits_in_ram(model, snap.ram_available_mb):
                        # CPU — check if model fits in available RAM
                        return {"model": model, "resource_type": "cpu", "location": "local"}
                    else:
                        logger.debug("Model %s needs too much RAM (%dMB available)", model, snap.ram_available_mb)

            # Check cluster workers for LLM capability
            for worker in snap.cluster_workers:
                if worker.get("models"):
                    return {
                        "model": worker["models"][0],
                        "resource_type": "gpu",
                        "location": f"worker:{worker.get('name', 'unknown')}",
                    }

        elif task_type == "embed":
            # Embedding — always local ONNX
            resource = "npu" if snap.has_npu else "cpu"
            return {"model": "all-MiniLM-L6-v2", "resource_type": resource, "location": "local"}

        return {}

    # ------------------------------------------------------------------
    # Migration policies
    # ------------------------------------------------------------------

    async def evaluate_migration(self) -> dict | None:
        """Evaluate whether memory pipeline should migrate to a different device.

        Checks for:
          - GPU worker became available → upgrade to larger model
          - GPU worker disconnected → fallback to local NPU/CPU
          - GPU worker busy (high utilisation) → fallback to local
          - GPU worker idle after contention → upgrade back

        Returns a migration action dict or None if no migration needed:
          {action: "upgrade"|"downgrade", from_model: ..., to_model: ...,
           from_location: ..., to_location: ..., reason: str}
        """
        snap = await self.get_snapshot(force_refresh=True)
        prev = self._prev_snapshot

        if not prev:
            self._prev_snapshot = snap
            return None

        # Check for GPU worker changes
        prev_gpu_workers = {w.get("name"): w for w in prev.cluster_workers if w.get("gpu")}
        curr_gpu_workers = {w.get("name"): w for w in snap.cluster_workers if w.get("gpu")}

        # New GPU worker appeared → upgrade opportunity
        new_workers = set(curr_gpu_workers.keys()) - set(prev_gpu_workers.keys())
        if new_workers:
            worker_name = next(iter(new_workers))
            worker = curr_gpu_workers[worker_name]
            self._prev_snapshot = snap
            return {
                "action": "upgrade",
                "to_model": self._best_worker_model(worker),
                "to_location": f"worker:{worker_name}",
                "from_location": "local",
                "reason": f"GPU worker '{worker_name}' joined cluster",
            }

        # GPU worker disappeared → must downgrade
        lost_workers = set(prev_gpu_workers.keys()) - set(curr_gpu_workers.keys())
        if lost_workers:
            worker_name = next(iter(lost_workers))
            self._prev_snapshot = snap
            fallback = await self.best_model_for_task("extract")
            return {
                "action": "downgrade",
                "to_model": fallback.get("model", "qwen3:4b"),
                "to_location": fallback.get("location", "local"),
                "from_location": f"worker:{worker_name}",
                "reason": f"GPU worker '{worker_name}' disconnected",
            }

        # GPU worker busy (utilisation > 80%) → consider downgrade
        for name, worker in curr_gpu_workers.items():
            utilisation = worker.get("gpu_utilisation", 0)
            if utilisation > 80:
                # Check if it's been busy for more than the contention threshold
                busy_since = self._worker_busy_since.get(name)
                now = time.time()
                if busy_since is None:
                    self._worker_busy_since[name] = now
                elif now - busy_since > self._contention_threshold:
                    self._prev_snapshot = snap
                    fallback = await self.best_model_for_task("extract")
                    return {
                        "action": "downgrade",
                        "to_model": fallback.get("model", "qwen3:4b"),
                        "to_location": "local",
                        "from_location": f"worker:{name}",
                        "reason": f"GPU worker '{name}' busy for >{self._contention_threshold}s (utilisation {utilisation}%)",
                    }
            else:
                # Worker not busy — clear the busy timer
                self._worker_busy_since.pop(name, None)

        # GPU worker became idle after being busy → upgrade back
        for name, worker in curr_gpu_workers.items():
            utilisation = worker.get("gpu_utilisation", 0)
            idle_since = self._worker_idle_since.get(name)
            if utilisation < 20:
                now = time.time()
                if idle_since is None:
                    self._worker_idle_since[name] = now
                elif now - idle_since > self._idle_upgrade_delay:
                    self._worker_idle_since.pop(name, None)
                    self._prev_snapshot = snap
                    return {
                        "action": "upgrade",
                        "to_model": self._best_worker_model(worker),
                        "to_location": f"worker:{name}",
                        "from_location": "local",
                        "reason": f"GPU worker '{name}' idle for >{self._idle_upgrade_delay}s, upgrading back",
                    }
            else:
                self._worker_idle_since.pop(name, None)

        self._prev_snapshot = snap
        return None

    def _best_worker_model(self, worker: dict) -> str:
        """Pick the best model from a worker's available models."""
        models = worker.get("models", [])
        # Prefer larger models
        for preferred in ["qwen3.5:27b", "qwen3.5:9b", "qwen3.5:4b", "qwen3:4b"]:
            for m in models:
                if preferred in m:
                    return m
        return models[0] if models else "qwen3:4b"

    async def can_accept_job(self, resource_type: str) -> bool:
        """Quick check: is there capacity for a new job of this resource type?"""
        if not self._queue:
            return True
        snap = await self.get_snapshot()
        limits = await self._queue.get_limits()
        running = (await self._queue.stats()).get("running_by_resource", {})
        limit = limits.get(resource_type, 0)
        current = running.get(resource_type, 0)
        return current < limit
