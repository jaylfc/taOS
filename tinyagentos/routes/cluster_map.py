"""Cluster capability + placement map (read-only) — taOS #897, first slice.

One aggregate view over the cluster manager's own state, answering the two
questions the Cluster app has to answer before anything can be moved or
auto-organised:

  * what can the *whole cluster* do right now (capability map), and
  * what is installed or running on which node, on what hardware, and is it
    healthy (live placement).

This module deliberately holds no state and adds no methods to
``ClusterManager``. It reads the existing getters — ``get_workers()`` for
heartbeat/reported state and ``get_leases()`` for the GPU arbiter's active
reservations — and aggregates them per request, so it cannot drift from the
routing/scheduling source of truth. Placement is derived from what workers
already report on heartbeat (``backends[].available_models`` carries loaded
vs installed-but-stopped), and the catalog tier/potential capabilities are
derived with the existing ``cluster.capabilities`` helpers.

Auth mirrors the rest of the Cluster app's admin surface — the pairing and BLE
routes and the capability-map reads in ``routes/cluster_capability.py`` all
require an admin session. Two layers apply: the auth middleware answers 401 to
a cookie-less caller (this path is not in the exempt list), and
``_require_admin`` answers 403 to a signed-in non-admin. This endpoint is a
whole-mesh inventory (hostnames, hardware, model placement), so it is not left
public like the legacy ``/api/cluster/workers`` list.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Request

from tinyagentos.cluster.capabilities import potential_capabilities, worker_tier_id
from tinyagentos.routes.auth import _require_admin

router = APIRouter()

# Freshness thresholds, deliberately identical to the desktop's
# `workerStatus()` helper so the API and the UI never disagree on health.
ONLINE_AGE_S = 60
STALE_AGE_S = 300

# Backend statuses that mean "serving right now". Anything else
# ("stopped", "error", "stale") is installed capacity, not live capacity.
RUNNING_BACKEND_STATUS = {"ok", "running", "healthy"}


def _health(last_heartbeat: float, now: float) -> str:
    if not last_heartbeat:
        return "unknown"
    age = now - last_heartbeat
    if age < ONLINE_AGE_S:
        return "online"
    if age < STALE_AGE_S:
        return "stale"
    return "offline"


def _model_name(entry: dict) -> str:
    return str(entry.get("name") or entry.get("id") or "")


def _placement_for(worker) -> list[dict]:
    """Flatten a worker's backends into per-model placement rows.

    ``available_models`` (worker manifest enrichment) is the authoritative
    source when present: it survives a stopped backend, so an installed model
    on a backend that is not currently running is still visible. Models the
    backend reports as resident but the manifest does not list are added
    afterwards so a loaded model is never hidden. A backend that declares no
    ``available_models`` (no manifest, or a manifest that does not cover its
    software) falls back to its ``models`` catalog, placed against the
    worker's ``loaded_models`` residency: a catalog model the worker reports
    resident is ``loaded``, the rest ``installed``. With no residency signal
    at all (an absent ``loaded_models`` key) nothing may be claimed loaded.
    """
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def _add(model_id: str, backend_name: str, backend_type: str, backend_status: str,
             state: str, capability: str = "", vram_required_gb: float = 0.0,
             health_url: str = "") -> None:
        key = (model_id, backend_name)
        if not model_id or key in seen:
            return
        seen.add(key)
        rows.append({
            "model_id": model_id,
            "capability": capability or "",
            "backend": backend_name,
            "backend_type": backend_type,
            "backend_status": backend_status,
            "state": state,
            "vram_required_gb": vram_required_gb,
            "health_url": health_url,
        })

    for b in worker.backends or []:
        if not isinstance(b, dict):
            continue
        btype = str(b.get("type") or "")
        bname = str(b.get("name") or btype)
        bstatus = str(b.get("status") or "")
        # `loaded_models` is the resident (actually-loaded) subset when the
        # worker reports it. An absent key means the worker published NO
        # residency signal — never fall back to `models` (that is the whole
        # catalog = pulled-but-idle), or we would claim every downloaded
        # model is loaded.
        resident = b.get("loaded_models")
        resident_names = (
            {_model_name(m) for m in resident if isinstance(m, dict)}
            if resident is not None
            else set()
        )

        available = [m for m in (b.get("available_models") or []) if isinstance(m, dict)]
        for m in available:
            model_id = str(m.get("model_id") or "")
            mstatus = str(m.get("status") or "").strip().lower()
            if mstatus == "loaded":
                state = "loaded"
            elif mstatus:
                # An explicit non-"loaded" status ("available", "installed",
                # "downloaded", ...) means present-but-not-resident.
                state = "installed"
            elif model_id in resident_names:
                state = "loaded"
            else:
                state = "installed"
            _add(
                model_id, bname, btype, bstatus, state,
                capability=str(m.get("capability") or ""),
                vram_required_gb=float(m.get("vram_required_gb") or 0.0),
                health_url=str(m.get("health_url") or ""),
            )

        if not available:
            # No per-model availability declared: the backend's `models`
            # catalog is all we have. Residency still comes from
            # `loaded_models` — a catalog model the worker reports resident is
            # loaded, every other one is merely installed. Marking this from
            # resident_names (not a blanket "installed") matters because `_add`
            # dedupes on (model_id, backend), so a later resident pass cannot
            # upgrade a row that was already emitted as installed. With no
            # residency signal at all, resident_names is empty and nothing is
            # claimed loaded.
            for m in (b.get("models") or []):
                if isinstance(m, dict):
                    name = _model_name(m)
                    _add(
                        name, bname, btype, bstatus,
                        "loaded" if name in resident_names else "installed",
                    )

        # A resident model the manifest did not declare must still be placed.
        for name in sorted(resident_names):
            _add(name, bname, btype, bstatus, "loaded")

    if not worker.backends:
        # No backend view at all (legacy/CPU worker): the flat `models` list
        # is the worker's only statement of what is loaded.
        for name in worker.models or []:
            _add(str(name), "", "", "", "loaded")

    return rows


def _leases_for(worker_name: str, leases: list) -> list[dict]:
    """Active GPU leases whose resource_id targets this node."""
    out = []
    for lease in leases:
        resource_id = getattr(lease, "resource_id", "") or ""
        owner = resource_id.split(":", 1)[0] if ":" in resource_id else resource_id
        if owner != worker_name:
            continue
        out.append({
            "lease_id": lease.lease_id,
            "resource_id": resource_id,
            "caller": lease.caller,
            "expires_at": lease.expires_at,
            "required_vram_mb": lease.required_vram_mb,
        })
    return out


def _potential(worker, registry) -> list[str]:
    """Catalog capabilities the hardware could support, plus what the worker
    already reported.

    Derived fresh rather than written back onto the worker: this endpoint is
    read-only, and mutating shared worker state from a GET would race the
    routes that own it.
    """
    caps = set(worker.potential_capabilities or [])
    hardware = worker.hardware if isinstance(worker.hardware, dict) else {}
    if registry is not None and hardware:
        try:
            _, derived = potential_capabilities(hardware, registry)
            caps.update(derived)
        except Exception:  # noqa: BLE001 - catalog errors must not 500 the map
            pass
    return sorted(caps)


@router.get("/api/cluster/map")
async def cluster_map(request: Request):
    """Aggregated capability map + live placement across every cluster node.

    Returns:
        ``nodes``: one entry per registered node (offline rows are kept, not
        filtered — a node that stopped heartbeating is exactly what the view
        is for), with its health, hardware/VRAM, backends, per-model placement
        rows (``state`` is ``loaded`` or ``installed``), catalog tier,
        capabilities and active GPU leases.
        ``capabilities``: the union of capabilities seen anywhere in the mesh,
        each split into mutually exclusive buckets — ``active_nodes``
        (reported/serving), ``installed_nodes`` (a model or backend for it is
        present but not serving) and ``potential_nodes`` (hardware could run
        it, nothing installed yet). A node serving a capability is never also
        listed as merely installed or capable for it. Only nodes that are
        still heartbeating (health ``online`` or ``stale``) count as active:
        the manager keeps an offline worker with its last-reported
        capabilities and loaded models, and those are not reachable capacity.
    """
    ok, err = _require_admin(request)
    if not ok:
        return err
    cluster = request.app.state.cluster_manager
    registry = getattr(request.app.state, "registry", None)
    now = time.time()
    leases = cluster.get_leases()

    nodes: list[dict] = []
    active: dict[str, list[str]] = {}
    installed: dict[str, list[str]] = {}
    potential: dict[str, list[str]] = {}

    def _mark(bucket: dict[str, list[str]], capability: str, node_name: str) -> None:
        if not capability:
            return
        names = bucket.setdefault(capability, [])
        if node_name not in names:
            names.append(node_name)

    for worker in cluster.get_workers():
        hardware = worker.hardware if isinstance(worker.hardware, dict) else {}
        # Health decides whether anything this node reports counts as serving.
        # A worker whose heartbeat stopped is retained by the manager with its
        # last-reported capabilities/backends/models, so without this check an
        # offline node would be advertised as serving capacity the mesh cannot
        # actually reach.
        health = _health(worker.last_heartbeat or 0, now)
        serving = health in ("online", "stale")
        placement = _placement_for(worker)
        backends = []
        reported_caps = list(worker.capabilities or [])

        for cap in reported_caps:
            _mark(active if serving else installed, str(cap), worker.name)

        for b in worker.backends or []:
            if not isinstance(b, dict):
                continue
            bstatus = str(b.get("status") or "")
            bcaps = [str(c) for c in (b.get("capabilities") or [])]
            backends.append({
                "name": str(b.get("name") or b.get("type") or ""),
                "type": str(b.get("type") or ""),
                "status": bstatus,
                "capabilities": bcaps,
                "model_count": len(b.get("models") or []),
                "available_model_count": len(b.get("available_models") or []),
            })
            bucket = active if serving and bstatus in RUNNING_BACKEND_STATUS else installed
            for cap in bcaps:
                _mark(bucket, cap, worker.name)

        for row in placement:
            # A resident model means its capability is being served, so a
            # `loaded` row counts as active — provided the node is still
            # heartbeating. Only installed-but-not-running capacity lands in
            # `installed`. The three buckets are made mutually exclusive when
            # the response is assembled, so a node never appears as both
            # serving and merely installed.
            bucket = active if serving and row["state"] == "loaded" else installed
            _mark(bucket, row["capability"], worker.name)

        pot = _potential(worker, registry)
        for cap in pot:
            _mark(potential, cap, worker.name)

        free_mb = worker.free_vram_mb
        used_mb = worker.used_vram_mb
        total_mb = (
            free_mb + used_mb
            if free_mb is not None and used_mb is not None
            else None
        )

        nodes.append({
            "name": worker.name,
            "url": worker.url,
            "platform": worker.platform,
            "status": worker.status,
            "health": _health(worker.last_heartbeat or 0, now),
            "last_heartbeat": worker.last_heartbeat,
            "heartbeat_age_s": (
                round(now - worker.last_heartbeat, 1) if worker.last_heartbeat else None
            ),
            "load": worker.load,
            "tier_id": worker.tier_id or worker_tier_id(hardware),
            "hardware": {
                "ram_mb": hardware.get("ram_mb", 0),
                "cpu": hardware.get("cpu") or {},
                "gpu": hardware.get("gpu") or {},
                "npu": hardware.get("npu") or {},
                "disk": hardware.get("disk") or {},
            },
            "vram": {"free_mb": free_mb, "used_mb": used_mb, "total_mb": total_mb},
            "capabilities": sorted(str(c) for c in reported_caps),
            "potential_capabilities": pot,
            "backends": backends,
            "placement": placement,
            "leases": _leases_for(worker.name, leases),
        })

    capability_names = sorted(set(active) | set(installed) | set(potential))
    capabilities = []
    for name in capability_names:
        # The buckets are mutually exclusive and ordered by strength: a node
        # that serves a capability is reported as active, not also as
        # installed or merely capable. Each node's own `placement` rows keep
        # the finer-grained detail (a node can serve one model for a
        # capability and have another merely installed).
        active_nodes = set(active.get(name, []))
        installed_nodes = set(installed.get(name, [])) - active_nodes
        potential_nodes = set(potential.get(name, [])) - active_nodes - installed_nodes
        capabilities.append({
            "capability": name,
            "active_nodes": sorted(active_nodes),
            "installed_nodes": sorted(installed_nodes),
            "potential_nodes": sorted(potential_nodes),
        })

    return {
        "generated_at": now,
        "nodes": nodes,
        "capabilities": capabilities,
    }
