from __future__ import annotations

import asyncio
import logging
import subprocess
from dataclasses import asdict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

from tinyagentos.cluster.capabilities import hardware_to_targets, potential_capabilities as _potential_capabilities
from tinyagentos.cluster.optimiser import ClusterOptimiser
from tinyagentos.cluster.worker_auth import _HMACError, require_worker_hmac
from tinyagentos.cluster.worker_protocol import WorkerInfo

router = APIRouter()


# ---------------------------------------------------------------------------
# Pairing models
# ---------------------------------------------------------------------------

class PairingAnnounce(BaseModel):
    name: str
    url: str
    platform: str = ""
    code_hash: str


class PairingConfirm(BaseModel):
    name: str
    code: str


class PairingClaim(BaseModel):
    name: str
    code: str


# ---------------------------------------------------------------------------
# Pairing endpoints
# ---------------------------------------------------------------------------

_CODE_HASH_RE = __import__("re").compile(r"^[0-9a-f]{64}$")


@router.post("/api/cluster/pairing/announce")
async def pairing_announce(request: Request, body: PairingAnnounce):
    """Unauthenticated — worker announces itself with a code hash."""
    if not body.name or not body.url:
        return JSONResponse({"error": "name and url are required"}, status_code=400)
    if not _CODE_HASH_RE.match(body.code_hash):
        return JSONResponse(
            {"error": "code_hash must be 64 lowercase hex characters"},
            status_code=400,
        )
    store = request.app.state.cluster_pairing
    await store.announce(body.name, body.url, body.platform, body.code_hash)
    return {"status": "pending"}


@router.get("/api/cluster/pairing/pending")
async def pairing_pending(request: Request):
    """Admin session required — list pending pairing announcements."""
    store = request.app.state.cluster_pairing
    items = await store.list_pending()
    return items


@router.post("/api/cluster/pairing/confirm")
async def pairing_confirm(request: Request, body: PairingConfirm):
    """Admin session required — confirm a pending pairing."""
    import tinyagentos.cluster.pairing_store as _ps
    store = request.app.state.cluster_pairing
    # Check if entry exists at all (including expired/maxed-out)
    row = await store._fetch_row(body.name)  # noqa: SLF001
    if row is None or row["pending_code_hash"] is None:
        return JSONResponse({"error": "No pending pairing for this worker"}, status_code=404)
    # Attempt cap check (invalidated entries are 404, not 410)
    if row["claim_attempts"] >= _ps._MAX_ATTEMPTS:  # noqa: SLF001
        return JSONResponse({"error": "No pending pairing for this worker"}, status_code=404)
    # Expiry check — use store's _now so monkeypatching in tests works correctly
    ts = row["pending_ts"]
    if ts is None or (_ps._now() - ts) > _ps._EXPIRY_SECS:  # noqa: SLF001
        return JSONResponse({"error": "Pairing request has expired"}, status_code=410)
    ok = await store.confirm(body.name, body.code)
    if not ok:
        return JSONResponse({"error": "Incorrect pairing code"}, status_code=403)
    return {"status": "confirmed"}


@router.post("/api/cluster/pairing/claim")
async def pairing_claim(request: Request, body: PairingClaim):
    """Unauthenticated — worker claims its signing key after admin confirmation."""
    store = request.app.state.cluster_pairing
    row = await store._fetch_row(body.name)  # noqa: SLF001
    if row is None:
        return JSONResponse({"error": "Unknown or invalidated worker"}, status_code=404)
    # No pending entry at all (never announced or already fully claimed)
    if row["pending_code_hash"] is None:
        return JSONResponse({"error": "Unknown or invalidated worker"}, status_code=404)
    # Not yet confirmed by admin
    if not row["confirmed"]:
        return JSONResponse({"status": "awaiting_confirm"}, status_code=202)
    key = await store.claim(body.name, body.code)
    if key is None:
        # claim() returns None on wrong code or expiry after confirm
        row2 = await store._fetch_row(body.name)  # noqa: SLF001
        if row2 is None or row2["pending_code_hash"] is None:
            return JSONResponse({"error": "Unknown or invalidated worker"}, status_code=404)
        return JSONResponse({"error": "Incorrect pairing code"}, status_code=403)
    return {"signing_key": key.hex()}


class WorkerRegister(BaseModel):
    name: str
    url: str
    hardware: dict = {}
    backends: list[dict] = []
    models: list[str] = []
    capabilities: list[str] = []
    platform: str = ""
    # KV quant support, asymmetric K/V plus boundary-layer flag. Defaults
    # to fp16-only so legacy workers that don't send these still validate.
    kv_cache_quant_support: list[str] = ["fp16"]
    kv_cache_quant_k_support: list[str] = ["fp16"]
    kv_cache_quant_v_support: list[str] = ["fp16"]
    kv_cache_quant_boundary_layer_protect: bool = False
    # LXC capacity fields — absent from legacy flat-mode workers, safe defaults.
    host_lan_ip: str | None = None
    storage_cap_bytes: int = 0
    storage_used_bytes: int = 0
    bytes_deduped_total: int = 0
    worker_lxc_image_version: str | None = None
    # One-shot marker delivered by install-worker.sh when it backed up
    # an existing taos-worker-pool. Controller materialises it as a
    # workspace text file + notification so the user knows old data was
    # preserved under the renamed pool. Worker deletes its local marker
    # after a successful registration so this never repeats.
    pending_storage_backup: dict | None = None


class HeartbeatBody(BaseModel):
    name: str
    load: float = 0.0
    models: list[str] | None = None
    # Backend-driven fields (worker agent v2+). Both optional so legacy
    # worker agents that only report load + models still validate.
    backends: list[dict] | None = None
    capabilities: list[str] | None = None
    # KV quant support. Each field is optional; None means the worker didn't
    # send it and the controller leaves the cached value unchanged rather
    # than overwriting with a default.
    kv_cache_quant_support: list[str] | None = None
    kv_cache_quant_k_support: list[str] | None = None
    kv_cache_quant_v_support: list[str] | None = None
    kv_cache_quant_boundary_layer_protect: bool | None = None
    # LXC byte counters — optional so legacy workers that don't send them
    # leave the stored values unchanged (Task 4 wires these through).
    storage_cap_bytes: int | None = None
    storage_used_bytes: int | None = None
    bytes_deduped_total: int | None = None


class RouteRequest(BaseModel):
    capability: str
    method: str = "POST"
    path: str
    body: dict | None = None
    timeout: float = 60


class MoveRequest(BaseModel):
    item: str
    from_worker: str | None = None
    to_worker: str


@router.get("/api/cluster/workers")
async def list_workers(request: Request):
    cluster = request.app.state.cluster_manager
    registry = getattr(request.app.state, "registry", None)
    workers = cluster.get_workers()
    result = []
    for w in workers:
        d = asdict(w)
        # signing_key is the worker's raw HMAC secret (bytes). Two reasons
        # to strip it from API responses: (1) FastAPI's default encoder
        # tries to utf-8 decode bytes fields, which crashes on random key
        # material — the entire workers list 500s when any worker has a
        # non-utf8 signing key, and (2) even when serialization didn't
        # crash, the secret has no business being on the wire.
        d.pop("signing_key", None)
        if registry is not None:
            tier_id, pot_caps = _potential_capabilities(w.hardware, registry)
            d["tier_id"] = tier_id
            d["potential_capabilities"] = pot_caps
            # Keep WorkerInfo fields in sync too so in-memory state is consistent
            w.tier_id = tier_id
            w.potential_capabilities = pot_caps
        result.append(d)
    return result


@router.post("/api/cluster/workers")
async def register_worker(request: Request, body: WorkerRegister):
    # HMAC gate — workers must be paired before registering.
    # The 'local' worker registers in-process (manager.register_worker),
    # never over HTTP, so it is unaffected by this check.
    try:
        await require_worker_hmac(request)
    except _HMACError as exc:
        return exc.response
    # The authenticated worker name must match the body name.
    if getattr(request.state, "hmac_worker_name", None) != body.name:
        return JSONResponse(
            {"error": "Worker name in header does not match body"},
            status_code=403,
        )
    cluster = request.app.state.cluster_manager
    if not body.name or not body.url:
        return JSONResponse({"error": "name and url are required"}, status_code=400)
    if body.host_lan_ip:
        existing = cluster.find_worker_by_host_lan_ip(body.host_lan_ip)
        if existing is not None and existing.name != body.name:
            return JSONResponse(
                {"error": f"Worker '{existing.name}' already registered for host {body.host_lan_ip}; only one worker LXC per host is supported"},
                status_code=409,
            )
    # Populate signing_key from pairing store so ticket-signing consumers work.
    pairing_store = getattr(request.app.state, "cluster_pairing", None)
    signing_key = b""
    if pairing_store is not None:
        key = await pairing_store.get_signing_key(body.name)
        if key is not None:
            signing_key = key
    info = WorkerInfo(
        name=body.name,
        url=body.url,
        hardware=body.hardware,
        backends=body.backends,
        models=body.models,
        capabilities=body.capabilities,
        platform=body.platform,
        kv_cache_quant_support=body.kv_cache_quant_support,
        kv_cache_quant_k_support=body.kv_cache_quant_k_support,
        kv_cache_quant_v_support=body.kv_cache_quant_v_support,
        kv_cache_quant_boundary_layer_protect=body.kv_cache_quant_boundary_layer_protect,
        host_lan_ip=body.host_lan_ip,
        storage_cap_bytes=body.storage_cap_bytes,
        storage_used_bytes=body.storage_used_bytes,
        bytes_deduped_total=body.bytes_deduped_total,
        worker_lxc_image_version=body.worker_lxc_image_version,
        signing_key=signing_key,
    )
    await cluster.register_worker(info)
    if body.pending_storage_backup:
        await _surface_storage_backup(request.app, body.name, body.pending_storage_backup)
    return {"status": "registered", "name": body.name}


async def _surface_storage_backup(app, worker_name: str, marker: dict) -> None:
    """Materialise an install-worker storage-backup marker as both a
    notification and a workspace text file so the user finds out about
    the rename without needing to inspect the worker box.

    Failures are swallowed — registration must succeed even if the
    surfacing path is broken (e.g. workspace dir missing in tests).
    """
    backed_up = marker.get("backed_up_pool", "?")
    original = marker.get("original_name", "taos-worker-pool")
    timestamp = marker.get("timestamp_utc", "")
    reason = marker.get("reason", "")

    title = f"Worker '{worker_name}': storage pool backed up"
    short_msg = (
        f"The installer found an existing '{original}' on this worker and "
        f"renamed it to '{backed_up}' before creating a fresh pool. "
        f"No data was deleted — see your workspace inbox for the full note."
    )
    notif = getattr(app.state, "notif_store", None)
    if notif is not None:
        try:
            await notif.add(title, short_msg, level="warning", source=f"worker:{worker_name}")
        except Exception:  # noqa: BLE001
            logger.warning("storage-backup notify: failed to add notification")

    data_dir = getattr(app.state, "data_dir", None)
    if data_dir is None:
        return
    try:
        import re as _re
        from pathlib import Path as _Path
        inbox = (_Path(data_dir) / "workspace" / "inbox").resolve()
        inbox.mkdir(parents=True, exist_ok=True)
        # Sanitise both interpolated components — worker_name and the
        # marker timestamp arrive over the wire and can carry slashes,
        # NULs, or other path-traversal characters. Strip everything
        # outside [A-Za-z0-9._-] and cap length so a hostile or weird
        # worker can't escape the inbox via crafted filename pieces.
        safe_re = _re.compile(r"[^A-Za-z0-9._-]+")
        safe_worker = safe_re.sub("-", worker_name)[:64] or "worker"
        safe_ts = safe_re.sub("-", timestamp)[:32] if timestamp else "unknown"
        fname = f"worker-storage-backup-{safe_worker}-{safe_ts}.txt"
        target = (inbox / fname).resolve()
        # Belt-and-braces — even after sanitisation, refuse to write
        # outside the inbox directory.
        if not str(target).startswith(str(inbox) + "/") and target != inbox:
            logger.warning("storage-backup notify: refusing write outside inbox (%s)", target)
            return
        body_lines = [
            f"Worker storage pool backed up — {worker_name}",
            "",
            f"When: {timestamp or 'unknown'} (UTC)",
            f"Reason: {reason or 'unspecified'}",
            f"Original pool: {original}",
            f"Renamed to:   {backed_up}",
            "",
            "What this means:",
            f"  The installer detected an existing '{original}' storage pool",
            "  on this worker and renamed it for safety before creating a",
            "  fresh pool. Nothing was deleted. Any LXCs from the previous",
            f"  install are still attached to '{backed_up}'.",
            "",
            "Recovery on the worker box:",
            f"  incus storage list                # confirm '{backed_up}' is present",
            f"  incus storage info {backed_up}    # check what's stored there",
            "",
            "Discard the backup once you're sure you don't need it:",
            f"  incus storage delete {backed_up}",
        ]
        target.write_text("\n".join(body_lines) + "\n")
    except Exception:  # noqa: BLE001
        logger.warning("storage-backup notify: failed to write workspace inbox file")


@router.post("/api/cluster/heartbeat")
async def worker_heartbeat(request: Request, body: HeartbeatBody):
    # HMAC gate — only paired, registered workers may heartbeat.
    try:
        await require_worker_hmac(request)
    except _HMACError as exc:
        return exc.response
    # The authenticated worker name must match the body name.
    if getattr(request.state, "hmac_worker_name", None) != body.name:
        return JSONResponse(
            {"error": "Worker name in header does not match body"},
            status_code=403,
        )
    cluster = request.app.state.cluster_manager
    ok = cluster.heartbeat(
        body.name,
        load=body.load,
        models=body.models,
        backends=body.backends,
        capabilities=body.capabilities,
        kv_cache_quant_support=body.kv_cache_quant_support,
        kv_cache_quant_k_support=body.kv_cache_quant_k_support,
        kv_cache_quant_v_support=body.kv_cache_quant_v_support,
        kv_cache_quant_boundary_layer_protect=body.kv_cache_quant_boundary_layer_protect,
    )
    if not ok:
        return JSONResponse({"error": "Worker not registered"}, status_code=404)
    return {"status": "ok"}


@router.delete("/api/cluster/workers/{name}")
async def unregister_worker(request: Request, name: str):
    cluster = request.app.state.cluster_manager
    removed = cluster.unregister_worker(name)
    if not removed:
        return JSONResponse({"error": "Worker not found"}, status_code=404)
    return {"status": "removed", "name": name}


@router.get("/api/cluster/capabilities")
async def list_capabilities(request: Request):
    cluster = request.app.state.cluster_manager
    workers = cluster.get_workers()
    caps: dict[str, list[str]] = {}
    for w in workers:
        if w.status != "online":
            continue
        for cap in w.capabilities:
            caps.setdefault(cap, []).append(w.name)
    return caps


@router.get("/api/cluster/kv-quant-options")
async def kv_quant_options(request: Request):
    """Return supported KV cache quant options as separate K and V lists.

    The deploy wizard fetches this to decide whether to render the K / V
    dropdowns and the boundary-layer toggle. When both K and V contain only
    "fp16", the wizard shows nothing (no dead control). As soon as any
    online worker advertises a second type the relevant dropdown
    materialises automatically.

    Response shape:
        {
            "options": ["fp16", ...],          # legacy flat union for old clients
            "k": ["fp16", "q8_0", ...],        # valid -ctk values
            "v": ["fp16", "turbo3", ...],      # valid -ctv values
            "boundary_layer_protect": bool     # true if any worker supports it
        }

    Keeping the legacy "options" field for one release while any older
    desktop builds in the field upgrade.
    """
    cluster = request.app.state.cluster_manager
    legacy = cluster.kv_quant_union()
    detailed = cluster.kv_quant_union_detailed()
    return {
        "options": legacy,
        "k": detailed["k"],
        "v": detailed["v"],
        "boundary_layer_protect": detailed["boundary"],
    }


@router.get("/api/cluster/backends")
async def cluster_backends(request: Request):
    """Aggregate backend catalog across every online worker in the mesh.

    Unions each worker's latest-heartbeat BackendCatalog into a single
    cluster-wide view. This is the cluster sibling of /api/scheduler/backends
    (which shows only the local controller's backends). Used by:

    - Cluster page UI to show 'what the whole mesh can do right now'
    - Scheduler Phase 2 cluster-aware dispatch to pick remote resources
    - Model Browser's 'available on cluster' filter
    """
    cluster = request.app.state.cluster_manager
    return cluster.aggregate_catalog()


@router.post("/api/cluster/route")
async def route_task(request: Request, body: RouteRequest):
    task_router = request.app.state.task_router
    data, worker_name = await task_router.route_request(
        capability=body.capability,
        method=body.method,
        path=body.path,
        body=body.body,
        timeout=body.timeout,
    )
    if data is None:
        return JSONResponse(
            {"error": f"No available worker for capability '{body.capability}'"},
            status_code=503,
        )
    return {"data": data, "worker": worker_name}


@router.get("/api/cluster/optimise")
async def optimise_cluster(request: Request):
    cluster = request.app.state.cluster_manager
    optimiser = ClusterOptimiser(cluster)
    return optimiser.analyse()


_BUILTIN_REMOTES = {"images", "ubuntu", "ubuntu-daily", "local"}


@router.get("/api/cluster/install-targets")
async def list_install_targets(request: Request):
    """Return the ordered list of hosts available for LXC service installs.

    Always includes the controller first ("local"), then any registered incus
    remotes whose protocol is "incus" (filters out the read-only image servers).
    Each entry carries a `tier_id` (hardware profile id, used by the Store
    filter to match against catalog `hardware_tiers`) and a `friendly_name`
    for display.
    """
    hp = getattr(request.app.state, "hardware_profile", None)
    # HardwareProfile is a flat dataclass (ram_mb / cpu / gpu / npu / disk
    # / os) — no .hardware attribute. asdict() produces the same nested
    # shape that hardware_to_targets and the Store filter expect.
    local_hw = asdict(hp) if hp is not None else {}
    targets: list[dict] = [
        {
            "name": "local",
            "label": "This controller",
            "type": "local",
            "tier_id": getattr(hp, "profile_id", "") if hp else "",
            "targets": hardware_to_targets(local_hw),
            "friendly_name": "Controller",
        }
    ]
    # Map worker name → tier_id by reusing the existing capability resolver.
    # Also map URL-hostname → tier_id so an incus remote whose name doesn't
    # match the worker registration name (e.g. remote "fedora-worker" vs
    # worker "fedora-host") still resolves the same physical hardware.
    from urllib.parse import urlparse as _urlparse
    cluster = getattr(request.app.state, "cluster_manager", None)
    registry = getattr(request.app.state, "registry", None)
    worker_tiers: dict[str, str] = {}
    worker_tiers_by_host: dict[str, str] = {}
    workers_by_host: dict[str, "WorkerInfo"] = {}
    if cluster is not None and registry is not None:
        for w in cluster.get_workers():
            try:
                tier_id, _caps = _potential_capabilities(w.hardware, registry)
            except Exception:  # noqa: BLE001
                tier_id = ""
            worker_tiers[w.name] = tier_id
            # Index by every plausible host signal — URL hostname AND
            # host_lan_ip — so an incus remote at https://192.168.x.y:8443
            # can match a worker whose `url` field points at its local
            # Ollama (e.g. http://localhost:11434) but who registered with
            # the LAN IP it would use to reach the controller.
            host_keys: set[str] = set()
            try:
                host = _urlparse(getattr(w, "url", "") or "").hostname or ""
            except Exception:  # noqa: BLE001
                host = ""
            if host and host not in ("localhost", "127.0.0.1", "::1"):
                host_keys.add(host)
            lan_ip = getattr(w, "host_lan_ip", None) or ""
            if lan_ip:
                host_keys.add(lan_ip)
            for k in host_keys:
                worker_tiers_by_host[k] = tier_id
                workers_by_host[k] = w

    try:
        import tinyagentos.containers as containers
        remotes = await containers.remote_list()
        for r in remotes:
            name = r.get("name", "")
            proto = r.get("protocol", "")
            if not name or name in _BUILTIN_REMOTES or proto != "incus":
                continue
            addr = r.get("addr", "")
            if addr.startswith("unix://"):
                # Local incus daemon — already added as the "local" controller
                # entry above. Incus exposes this as "local (current)" when it
                # is the active context, which isn't in _BUILTIN_REMOTES.
                continue
            # Look up the matching worker: by name first, then by URL host.
            worker_hw: dict = {}
            tier_id = ""
            if cluster is not None:
                worker_hw = next(
                    (w.hardware for w in cluster.get_workers() if w.name == name),
                    {},
                )
                tier_id = worker_tiers.get(name, "")
                if not worker_hw:
                    try:
                        remote_host = _urlparse(addr).hostname or ""
                    except Exception:  # noqa: BLE001
                        remote_host = ""
                    if remote_host and remote_host in workers_by_host:
                        worker_hw = workers_by_host[remote_host].hardware
                        tier_id = worker_tiers_by_host.get(remote_host, tier_id)
            hardware_known = bool(worker_hw) or bool(tier_id)
            targets.append({
                "name": name,
                "label": name,
                "type": "remote",
                "addr": addr,
                "tier_id": tier_id or ("unknown" if not hardware_known else ""),
                "targets": hardware_to_targets(worker_hw),
                "hardware_known": hardware_known,
                "friendly_name": name,
            })
    except Exception as exc:  # noqa: BLE001
        logger.warning("list_install_targets: remote_list failed: %s", exc)
    return targets


@router.post("/api/cluster/move")
async def move_model(request: Request, body: MoveRequest):
    cluster = request.app.state.cluster_manager
    to_worker = cluster.get_worker(body.to_worker)
    if not to_worker:
        return JSONResponse({"error": f"Worker '{body.to_worker}' not found"}, status_code=404)
    if to_worker.status != "online":
        return JSONResponse({"error": f"Worker '{body.to_worker}' is not online"}, status_code=400)

    # If from_worker specified, remove the item from it
    if body.from_worker:
        from_w = cluster.get_worker(body.from_worker)
        if from_w and body.item in from_w.models:
            from_w.models.remove(body.item)
        if from_w and body.item in from_w.capabilities:
            from_w.capabilities.remove(body.item)

    # Add to target worker's models if not already there
    if body.item not in to_worker.models:
        to_worker.models.append(body.item)

    return {"status": "moved", "item": body.item, "to": body.to_worker}


class IncusEnrollRequest(BaseModel):
    incus_url: str
    token: str


@router.post("/api/cluster/workers/{name}/incus-enroll")
async def incus_enroll(request: Request, name: str, body: IncusEnrollRequest):
    """Wire a registered worker's incus daemon into the controller's remote list.

    The worker installer calls this after completing ``POST /api/cluster/workers``.
    It adds the worker's incus HTTPS endpoint as a named remote on the controller
    so LXC services can be deployed to it without any manual incus configuration.

    Returns ``{"ok": true}`` on success or ``{"ok": false, "error": "..."}`` on
    failure. 404 when the worker is not yet registered.
    """
    from urllib.parse import urlparse
    import tinyagentos.containers as containers

    cluster = request.app.state.cluster_manager
    worker = cluster.get_worker(name)
    if not worker:
        return JSONResponse({"error": f"Worker '{name}' not registered"}, status_code=404)

    # Validate that incus_url's hostname matches the worker's registered address
    # to prevent a confused/malicious worker from enrolling an arbitrary remote.
    try:
        incus_host = urlparse(body.incus_url).hostname or ""
        worker_host = urlparse(worker.url).hostname or ""
        if incus_host != worker_host:
            return JSONResponse(
                {
                    "error": (
                        f"incus_url host {incus_host!r} does not match "
                        f"registered worker address {worker_host!r}"
                    )
                },
                status_code=400,
            )
    except Exception as exc:
        return JSONResponse({"error": f"invalid incus_url: {exc}"}, status_code=400)

    try:
        result = await containers.remote_add(name, body.incus_url, body.token)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    if result["success"]:
        # Verify the worker LXC was launched with security.privileged=true and
        # security.nesting=true. If either is missing, mark the worker as
        # degraded so the cluster UI surfaces the warning. Don't fail the enroll
        # itself — registration is already complete.
        try:
            info_proc = await asyncio.to_thread(
                subprocess.run,
                ["incus", "info", "taos-worker", "--remote", name],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if info_proc.returncode == 0:
                privileged = "security.privileged: \"true\"" in info_proc.stdout
                nesting = "security.nesting: \"true\"" in info_proc.stdout
                if not (privileged and nesting):
                    worker.degraded = True
                    missing = []
                    if not privileged:
                        missing.append("security.privileged=true")
                    if not nesting:
                        missing.append("security.nesting=true")
                    worker.degraded_reason = (
                        f"worker LXC missing {', '.join(missing)}; "
                        "nested incus operations may fail"
                    )
                    logger.warning("worker %s degraded: %s", name, worker.degraded_reason)
            # If incus info fails (returncode != 0), don't flag — could just be that
            # the worker is at incus-only state without the LXC yet (during initial
            # enrollment). Worth a debug log only.
        except Exception as exc:
            logger.warning("could not verify worker LXC privilege for %s: %s", name, exc)
        return {"ok": True}
    return JSONResponse({"ok": False, "error": result["output"]}, status_code=500)


class DeployRequest(BaseModel):
    command: str


class WorkerRemoteRequest(BaseModel):
    command: str
    timeout: int = 30


DEPLOY_COMMANDS = {
    "install-ollama",
    "install-exo",
    "install-llama-cpp",
    "install-llama-cpp --cuda",
    "install-vllm",
    "install-rknpu",
    "update-worker",
    "status",
}

REMOTE_EXEC_ALLOWLIST = [
    "systemctl status",
    "systemctl restart",
    "journalctl -u",
    "df -h",
    "free -h",
    "nvidia-smi",
    "cat /proc/meminfo",
    "uname -a",
    "uptime",
    "ip addr",
    "pip list",
    "pip install",
    "apt-get update",
    "apt-get install",
    "dnf install",
]


@router.post("/api/cluster/workers/{name}/deploy")
async def deploy_backend(request: Request, name: str, body: DeployRequest):
    """Trigger a backend install on a remote worker.

    The controller proxies this to the worker's deploy endpoint. The
    worker runs taos-deploy-helper.sh via passwordless sudo. Only
    commands in the fixed allowlist are accepted.
    """
    cluster = request.app.state.cluster_manager
    worker = cluster.get_worker(name)
    if not worker:
        return JSONResponse({"error": f"Worker '{name}' not found"}, status_code=404)
    if worker.status != "online":
        return JSONResponse({"error": f"Worker '{name}' is not online"}, status_code=400)
    if body.command not in DEPLOY_COMMANDS:
        return JSONResponse(
            {"error": f"Unknown command: {body.command}", "allowed": sorted(DEPLOY_COMMANDS)},
            status_code=400,
        )

    import httpx
    try:
        async with httpx.AsyncClient(timeout=620) as client:
            resp = await client.post(
                f"{worker.url}/api/worker/deploy",
                json={"command": body.command},
            )
            return resp.json()
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


@router.post("/api/cluster/workers/{name}/remote")
async def worker_remote_command(request: Request, name: str, body: WorkerRemoteRequest):
    """Run an allowlisted command on a remote worker for debugging.

    Used by the taOS agent/expert agent and the admin UI to
    diagnose worker issues without SSH access. Commands must match
    a prefix in the allowlist. The worker-side endpoint uses
    create_subprocess_exec (no shell) with the command split into
    argv to prevent injection.
    """
    cluster = request.app.state.cluster_manager
    worker = cluster.get_worker(name)
    if not worker:
        return JSONResponse({"error": f"Worker '{name}' not found"}, status_code=404)
    if worker.status != "online":
        return JSONResponse({"error": f"Worker '{name}' is not online"}, status_code=400)

    if not any(body.command.startswith(prefix) for prefix in REMOTE_EXEC_ALLOWLIST):
        return JSONResponse(
            {"error": "Command not in allowlist", "allowed_prefixes": REMOTE_EXEC_ALLOWLIST},
            status_code=403,
        )

    import httpx
    try:
        async with httpx.AsyncClient(timeout=body.timeout + 5) as client:
            resp = await client.post(
                f"{worker.url}/api/worker/remote",
                json={"command": body.command, "timeout": body.timeout},
            )
            return resp.json()
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


@router.post("/api/cluster/promote-archived")
async def promote_archived_models(request: Request):
    """Manual trigger: scan all online workers and promote any archived
    models that are now compatible with cluster hardware.

    Called by the user from the Cluster page or admin CLI. Safe to call
    repeatedly — already-promoted models are skipped.
    """
    cluster = request.app.state.cluster_manager
    notifications = getattr(request.app.state, "notifications", None)

    workers = cluster.get_workers()
    online = [w for w in workers if w.status == "online"]

    from tinyagentos.cluster.model_archive import (
        find_promotable,
        promote_model,
    )

    promoted_by_worker: dict[str, list[str]] = {}
    total = 0

    for w in online:
        promotable = find_promotable(
            worker_hardware=w.hardware,
            worker_name=w.name,
        )
        for model in promotable:
            model_id = model.get("model_id", "?")
            if promote_model(model):
                promoted_by_worker.setdefault(w.name, []).append(model_id)
                total += 1
                if notifications:
                    try:
                        await notifications.emit_event(
                            "model.promoted",
                            f"Archived model '{model_id}' promoted",
                            f"Worker '{w.name}' can now run '{model_id}'. "
                            f"Moved from archive to active models.",
                            level="info",
                        )
                    except Exception:
                        logger.exception(
                            "notification emit failed for model promotion %s",
                            model_id,
                        )

    return {
        "promoted": total,
        "by_worker": promoted_by_worker,
        "workers_scanned": len(online),
    }
