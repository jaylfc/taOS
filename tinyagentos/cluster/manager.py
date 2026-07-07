from __future__ import annotations
import asyncio
import logging
import secrets
import time
from tinyagentos.cluster.worker_protocol import GpuLease, WorkerInfo

logger = logging.getLogger(__name__)

HEARTBEAT_TIMEOUT = 30  # seconds before marking worker offline


def _format_hw(hw) -> str:
    """Format hardware info for notification messages."""
    if not isinstance(hw, dict):
        return "Unknown hardware"
    parts = []
    ram = hw.get("ram_mb", 0)
    if ram:
        parts.append(f"{ram // 1024}GB RAM")
    gpu = hw.get("gpu", {})
    if gpu.get("type") not in (None, "none", ""):
        vram = gpu.get("vram_mb", 0)
        parts.append(f"{gpu.get('model', gpu['type'])}" + (f" {vram // 1024}GB" if vram else ""))
    npu = hw.get("npu", {})
    if npu.get("type", "none") != "none":
        parts.append(f"{npu['type']} {npu.get('tops', 0)} TOPS")
    return ", ".join(parts) if parts else "CPU only"


class ClusterManager:
    def __init__(self, notifications=None, capabilities=None):
        self._workers: dict[str, WorkerInfo] = {}
        self._leases: dict[str, GpuLease] = {}
        # Serializes all lease-table mutations (claim/release/renew/expiry
        # sweep) so a claim's find-existing -> check-VRAM -> store sequence
        # is atomic even when multiple requests are in flight at once.
        self._lease_lock = asyncio.Lock()
        self._monitor_task: asyncio.Task | None = None
        self._notifications = notifications  # NotificationStore, optional
        self._capabilities = capabilities    # CapabilityChecker, optional
        # Track worker names seen at least once so we only fire worker.join
        # on the very first appearance within this process lifetime.
        self._ever_seen: set[str] = set()
        # Strong references to background tasks to prevent GC before completion.
        self._background_tasks: set[asyncio.Task] = set()

    async def start(self):
        self._monitor_task = asyncio.create_task(self._monitor_loop())

    async def stop(self):
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

    async def register_worker(self, info: WorkerInfo) -> None:
        # Snapshot capabilities before adding worker
        caps_before = set()
        if self._capabilities:
            caps_before = {k for k, v in self._capabilities.get_all_capabilities().items() if v["available"]}

        is_first_time = info.name not in self._ever_seen
        self._ever_seen.add(info.name)

        prev_status = self._workers[info.name].status if info.name in self._workers else None

        info.registered_at = time.time()
        info.last_heartbeat = time.time()
        info.status = "online"
        self._workers[info.name] = info
        logger.info(f"Worker registered: {info.name} ({info.platform}, {len(info.capabilities)} capabilities)")

        # The "local" worker is the controller registering itself on every boot;
        # that is not a noteworthy cluster event, so do not notify for it. Only
        # real remote workers joining or coming back online should notify.
        if self._notifications and info.name != "local":
            newly_unlocked = []
            if self._capabilities:
                caps_after = {k for k, v in self._capabilities.get_all_capabilities().items() if v["available"]}
                newly_unlocked = sorted(caps_after - caps_before)

            hw_summary = _format_hw(info.hardware)

            if is_first_time:
                # Brand-new worker — never seen before this process started
                msg = f"Platform: {info.platform}, {hw_summary}"
                if newly_unlocked:
                    msg += f"\n\nNewly unlocked: {', '.join(newly_unlocked)}"
                    msg += "\n\nConsider running cluster optimisation to redistribute workloads."
                await self._notifications.emit_event(
                    "worker.join",
                    f"Worker '{info.name}' joined the cluster",
                    msg,
                    level="info",
                )
            elif prev_status in ("offline", "stale"):
                # Known worker re-registering after being offline
                msg = f"Platform: {info.platform}, {hw_summary}"
                if newly_unlocked:
                    msg += f"\n\nNewly unlocked: {', '.join(newly_unlocked)}"
                await self._notifications.emit_event(
                    "worker.online",
                    f"Worker '{info.name}' came back online",
                    msg,
                    level="info",
                )

        # Promote any archived models this worker can now run.
        # Scheduled as a background task so worker registration returns
        # immediately — promotion may involve large cross-volume copies.
        async def _promote_bg() -> None:
            try:
                from tinyagentos.cluster.model_archive import (
                    promote_compatible_models,
                )

                await promote_compatible_models(
                    worker_hardware=info.hardware,
                    worker_name=info.name,
                    notifications=self._notifications,
                )
            except Exception:
                logger.exception(
                    "model_archive: promotion scan failed for worker '%s'",
                    info.name,
                )

        task = asyncio.create_task(_promote_bg())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def kv_quant_union(self) -> list[str]:
        """Return the set-union of KV cache quant types across all online workers.

        Legacy flat union, kept for consumers that have not learned the
        split K/V shape yet. New callers should use kv_quant_union_detailed().
        """
        types: set[str] = {"fp16"}
        for w in self._workers.values():
            if w.status != "online":
                continue
            types.update(w.kv_cache_quant_support or ["fp16"])
        return sorted(types)

    def kv_quant_union_detailed(self) -> dict:
        """Return separate K and V quant type unions plus boundary support.

        Shape:
            {
                "k": sorted list[str] of -ctk values any online worker supports
                "v": sorted list[str] of -ctv values any online worker supports
                "boundary": bool, True if ANY online worker supports boundary-layer protection
            }

        Always includes "fp16" in both K and V as the baseline, so a cluster
        with no TurboQuant-capable workers still returns a valid shape. The
        deploy wizard uses this to decide whether to render the K/V
        dropdowns: if both lists have only "fp16" the control is not shown.
        """
        k_types: set[str] = {"fp16"}
        v_types: set[str] = {"fp16"}
        boundary = False
        for w in self._workers.values():
            if w.status != "online":
                continue
            k_types.update(getattr(w, "kv_cache_quant_k_support", None) or ["fp16"])
            v_types.update(getattr(w, "kv_cache_quant_v_support", None) or ["fp16"])
            if getattr(w, "kv_cache_quant_boundary_layer_protect", False):
                boundary = True
        return {
            "k": sorted(k_types),
            "v": sorted(v_types),
            "boundary": boundary,
        }

    def heartbeat(
        self,
        name: str,
        load: float = 0.0,
        models: list[str] | None = None,
        backends: list[dict] | None = None,
        capabilities: list[str] | None = None,
        kv_cache_quant_support: list[str] | None = None,
        kv_cache_quant_k_support: list[str] | None = None,
        kv_cache_quant_v_support: list[str] | None = None,
        kv_cache_quant_boundary_layer_protect: bool | None = None,
        free_vram_mb: int | None = None,
        used_vram_mb: int | None = None,
    ) -> bool:
        """Accept a worker heartbeat.

        Backend-driven: when ``backends`` or ``capabilities`` are supplied
        (worker agent v2+), overwrite the worker's cached view so the
        cluster-wide catalog stays fresh. Old-style heartbeats that only
        carry load/models still work.
        """
        worker = self._workers.get(name)
        if not worker:
            return False
        prev_status = worker.status
        worker.last_heartbeat = time.time()
        worker.load = load
        worker.status = "online"
        if models is not None:
            worker.models = models
        if backends is not None:
            worker.backends = backends
            # Derive a flat model list from the live backend catalog for
            # compatibility with the existing worker.models field
            flat_models: list[str] = []
            for b in backends:
                for m in b.get("models") or []:
                    name_m = m.get("name") or m.get("id") or ""
                    if name_m and name_m not in flat_models:
                        flat_models.append(name_m)
            worker.models = flat_models
            # Derive available_models from the live backend catalog.
            # Each backend may carry a manifest-enriched
            # available_models list; flatten across all backends.
            flat_available: list[dict] = []
            seen_ids: set[str] = set()
            for b in backends:
                for m in b.get("available_models") or []:
                    model_id = m.get("model_id") or ""
                    if model_id and model_id not in seen_ids:
                        seen_ids.add(model_id)
                        flat_available.append(m)
            worker.available_models = flat_available
        if capabilities is not None:
            worker.capabilities = list(capabilities)
        if kv_cache_quant_support is not None:
            worker.kv_cache_quant_support = list(kv_cache_quant_support)
        if kv_cache_quant_k_support is not None:
            worker.kv_cache_quant_k_support = list(kv_cache_quant_k_support)
        if kv_cache_quant_v_support is not None:
            worker.kv_cache_quant_v_support = list(kv_cache_quant_v_support)
        if kv_cache_quant_boundary_layer_protect is not None:
            worker.kv_cache_quant_boundary_layer_protect = bool(kv_cache_quant_boundary_layer_protect)
        if free_vram_mb is not None:
            worker.free_vram_mb = int(free_vram_mb)
        if used_vram_mb is not None:
            worker.used_vram_mb = int(used_vram_mb)
        # Fire worker.online notification when a previously-offline worker recovers.
        # heartbeat() is sync, so schedule the async emit as a background task.
        if self._notifications and prev_status in ("offline", "stale"):
            try:
                asyncio.get_running_loop().create_task(
                    self._notifications.emit_event(
                        "worker.online",
                        f"Worker '{worker.name}' came back online",
                        f"Resumed after being {prev_status}.",
                        level="info",
                    )
                )
            except RuntimeError:
                pass  # No running loop (e.g. in sync tests) — skip gracefully
        return True

    async def unregister_worker(self, name: str) -> bool:
        """Remove a worker and all of its active GPU leases.

        When a worker is explicitly unregistered (admin action), every
        active lease tied to the worker's resources is released so that
        those resource slots become available for new claims immediately.
        This mirrors the lease-release logic in ``_monitor_loop`` for the
        heartbeat-timeout path (taOS #1705).
        """
        worker = self._workers.get(name)
        if worker is None:
            return False

        # Release all active leases for this worker's resources.
        async with self._lease_lock:
            lids: list[str] = [
                lid for lid, lease in self._leases.items()
                if (parsed := self._parse_resource_id(lease.resource_id))
                and parsed[0] == name
            ]
            for lid in lids:
                self._leases.pop(lid, None)
                logger.debug(
                    "Lease %s released — worker '%s' unregistered",
                    lid, name,
                )

            self._workers.pop(name, None)
        logger.info("Worker '%s' unregistered — %d leases released", name, len(lids))
        return True

    def get_workers(self) -> list[WorkerInfo]:
        return list(self._workers.values())

    def get_worker(self, name: str) -> WorkerInfo | None:
        return self._workers.get(name)

    def find_worker_by_host_lan_ip(self, host_lan_ip: str) -> WorkerInfo | None:
        """Return the worker registered for this bare host's LAN IP, or None.

        Only worker-LXC mode workers send host_lan_ip; legacy flat-mode workers
        have host_lan_ip=None and don't collide via this check.
        """
        for w in self._workers.values():
            if w.host_lan_ip == host_lan_ip:
                return w
        return None

    # ── Lease management ───────────────────────────────────────────────

    def _parse_resource_id(self, resource_id: str) -> tuple[str, str] | None:
        """Split ``"worker-name:gpu-cuda-0"`` into (worker_name, resource_name)."""
        if ":" not in resource_id:
            return None
        worker, rest = resource_id.split(":", 1)
        return worker, rest

    def _worker_for_resource(self, resource_id: str) -> WorkerInfo | None:
        """Return the WorkerInfo for a resource_id, or None."""
        parsed = self._parse_resource_id(resource_id)
        if parsed is None:
            return None
        worker_name, _ = parsed
        worker = self._workers.get(worker_name)
        if worker is None or worker.status != "online":
            return None
        return worker

    def find_existing_lease(self, resource_id: str) -> GpuLease | None:
        """Return the active (non-expired) lease for a resource, if any."""
        now = time.time()
        for lease in self._leases.values():
            if lease.resource_id == resource_id and lease.expires_at > now:
                return lease
        return None

    async def claim_lease(
        self,
        resource_id: str,
        caller: str = "",
        ttl_seconds: float = 30,
        required_vram_mb: int = 0,
    ) -> GpuLease | None:
        """Attempt to claim a GPU lease on ``resource_id``.

        Fails if:
        - The resource_id is malformed.
        - The target worker is not online.
        - Another active lease already exists for this resource.
        - ``required_vram_mb > worker.free_vram_mb`` (when
          ``required_vram_mb > 0`` and the worker's free VRAM is known).
          A worker that has never reported VRAM (``free_vram_mb is None``,
          e.g. non-NVIDIA hardware with no probe) is never refused on VRAM
          grounds since we cannot prove insufficient capacity.

        The find-existing / check-VRAM / store sequence runs under
        ``_lease_lock`` so two concurrent claims for the same resource
        cannot both succeed.
        """
        async with self._lease_lock:
            # Resolve the worker and re-check its status inside the lock: the
            # monitor loop marks workers offline and sweeps their leases under
            # this same lock, so a lookup before the lock could admit a lease
            # against a worker that went offline while we waited (TOCTOU).
            worker = self._worker_for_resource(resource_id)
            if worker is None:
                logger.debug("claim_lease: worker not found or offline for %s", resource_id)
                return None

            existing = self.find_existing_lease(resource_id)
            if existing is not None:
                logger.debug(
                    "claim_lease: resource %s already leased by %r (expires in %.0fs)",
                    resource_id, existing.caller, existing.expires_at - time.time(),
                )
                return None

            if (
                required_vram_mb > 0
                and worker.free_vram_mb is not None
                and required_vram_mb > worker.free_vram_mb
            ):
                logger.debug(
                    "claim_lease: %s needs %d MiB VRAM but %s has %d MiB free",
                    caller, required_vram_mb, worker.name, worker.free_vram_mb,
                )
                return None

            lease_id = f"l_{secrets.token_hex(16)}"
            lease = GpuLease(
                lease_id=lease_id,
                resource_id=resource_id,
                caller=caller,
                expires_at=time.time() + ttl_seconds,
                required_vram_mb=required_vram_mb,
            )
            self._leases[lease_id] = lease
            logger.info(
                "Lease claimed: %s on %s by %r (ttl=%.0fs, vram=%d MiB)",
                lease_id, resource_id, caller, ttl_seconds, required_vram_mb,
            )
            return lease

    async def release_lease(self, lease_id: str) -> bool:
        """Release a lease by id.  Idempotent — returns True even if the
        lease was already expired or never existed."""
        async with self._lease_lock:
            lease = self._leases.pop(lease_id, None)
        if lease is not None:
            logger.info("Lease released: %s on %s", lease_id, lease.resource_id)
        return True  # idempotent

    async def renew_lease(self, lease_id: str, ttl_seconds: float = 30) -> GpuLease | None:
        """Extend a lease's TTL.  Returns the lease, or None if expired/unknown."""
        async with self._lease_lock:
            lease = self._leases.get(lease_id)
            if lease is None:
                return None
            now = time.time()
            if lease.expires_at <= now:
                self._leases.pop(lease_id, None)
                return None
            lease.expires_at = now + ttl_seconds
            return lease

    def get_leases(self) -> list[GpuLease]:
        """Return a snapshot of active (non-expired) leases."""
        now = time.time()
        return [lease for lease in self._leases.values() if lease.expires_at > now]

    def _sweep_expired_leases(self):
        """Remove leases whose TTL has elapsed.  Called from _monitor_loop
        while holding ``_lease_lock``."""
        now = time.time()
        expired = [
            lid for lid, lease in self._leases.items()
            if lease.expires_at <= now
        ]
        for lid in expired:
            lease = self._leases.pop(lid)
            logger.debug("Lease expired: %s on %s", lid, lease.resource_id)

    # ── Capability routing ─────────────────────────────────────────────

    def get_workers_for_capability(self, capability: str) -> list[WorkerInfo]:
        """Get online workers that support a capability, sorted by priority (lowest load first)."""
        eligible = [
            w for w in self._workers.values()
            if w.status == "online" and capability in w.capabilities
        ]
        return sorted(eligible, key=lambda w: w.load)

    def get_best_worker(self, capability: str) -> WorkerInfo | None:
        """Get the best available worker for a capability."""
        workers = self.get_workers_for_capability(capability)
        return workers[0] if workers else None

    def aggregate_catalog(self) -> dict:
        """Cluster-wide union of every online worker's live BackendCatalog.

        Each online worker reports its own backends + models on every
        heartbeat. This method joins them into a single view keyed on
        ``f"{worker_name}:{backend_name}"`` so the Cluster page and the
        cluster-aware scheduler dispatch (Phase 2) can see 'what the
        entire mesh can do right now' without polling every worker
        individually.

        Offline workers are skipped entirely, their stale data is not
        useful and could mislead routing. The in-process BackendCatalog
        on the controller handles the local-host view; this method
        handles the remote-worker view.

        Returns:
            A dict with:
            - ``workers``: per-worker summary (name, status, capabilities,
              backend count, model count)
            - ``backends``: flat list of every remote backend entry with
              its owning worker tagged. Note: each entry's ``url`` is the
              worker-local probe address -- cross-host calls must go via
              ``worker_url`` (the worker agent), never the backend ``url``.
            - ``capabilities``: set of capabilities present somewhere in
              the mesh (union across workers)
            - ``models``: flat list of every model loaded on any online
              worker, tagged with its owning worker and backend
        """
        workers_summary = []
        flat_backends: list[dict] = []
        flat_models: list[dict] = []
        all_capabilities: set[str] = set()

        for worker in self._workers.values():
            if worker.status != "online":
                continue

            worker_caps = set(worker.capabilities or [])
            all_capabilities |= worker_caps

            wbackends = worker.backends or []
            for b in wbackends:
                entry = {
                    **b,
                    "worker": worker.name,
                    "worker_url": worker.url,
                    "worker_platform": getattr(worker, "platform", ""),
                }
                flat_backends.append(entry)
                for m in b.get("models") or []:
                    flat_models.append({
                        **m,
                        "worker": worker.name,
                        "worker_url": worker.url,
                        "backend_name": b.get("name", ""),
                        "backend_type": b.get("type", ""),
                    })

            workers_summary.append({
                "name": worker.name,
                "url": worker.url,
                "platform": getattr(worker, "platform", ""),
                "status": worker.status,
                "load": worker.load,
                "capabilities": sorted(worker_caps),
                "backend_count": len(wbackends),
                "model_count": sum(len(b.get("models") or []) for b in wbackends),
            })

        return {
            "workers": workers_summary,
            "backends": flat_backends,
            "capabilities": sorted(all_capabilities),
            "models": flat_models,
        }

    async def _monitor_loop(self):
        """Monitor worker heartbeats, mark stale workers as offline, and
        sweep expired GPU leases."""
        while True:
            now = time.time()
            for worker in self._workers.values():
                # The 'local' worker is the controller itself — it never sends
                # heartbeats (it IS the server), so never mark it offline.
                if worker.name == "local":
                    continue
                if worker.status == "online" and (now - worker.last_heartbeat) > HEARTBEAT_TIMEOUT:
                    worker.status = "offline"
                    logger.warning(f"Worker '{worker.name}' marked offline (no heartbeat for {HEARTBEAT_TIMEOUT}s)")
                    if self._notifications:
                        await self._notifications.emit_event(
                            "worker.leave",
                            f"Worker '{worker.name}' went offline",
                            f"No heartbeat for {HEARTBEAT_TIMEOUT}s. Capabilities may be reduced.",
                            level="warning",
                        )
                    # Release any active leases for this worker's resources.
                    # Match on the exact worker name (not a resource_id
                    # prefix) so "gpu-node" and "gpu-node-2" don't collide.
                    async with self._lease_lock:
                        offline_lids = [
                            lid for lid, lease in self._leases.items()
                            if (parsed := self._parse_resource_id(lease.resource_id))
                            and parsed[0] == worker.name
                        ]
                        for lid in offline_lids:
                            self._leases.pop(lid, None)
                            logger.debug("Lease %s released — worker %s went offline", lid, worker.name)
            async with self._lease_lock:
                self._sweep_expired_leases()
            await asyncio.sleep(5)
