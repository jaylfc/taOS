"""Cluster resource inventory (read-only) --- one list of everything taOS runs.

taOS #897, slice S9. The Cluster app needs ONE flat list of every resource
across all workers so a later Resources tab (S10) and a later Move action
(S11) share a single source. This card is the read-only backend aggregate
only: no UI, no actions.

Mirrors ``routes/cluster_map.py`` closely: same admin auth gate, holds no
state and adds no methods to ``ClusterManager``, aggregates per request from
existing getters, and every source is wrapped so one failing getter (e.g.
incus down) records its name in ``errors`` and keeps the rest of the list
alive rather than 500-ing the whole response.

Response shape::

    {"resources": [...], "errors": [...], "generated_at": <unix float>}

Each resource row has exactly these keys: ``id`` (``<type>:<name>`` except
backend ids are ``backend:<worker>:<name>``), ``type``
(``agent`` / ``backend`` / ``app`` / ``controller``), ``name``,
``worker``, ``project`` (``str`` or ``null``), ``state``
(``running`` / ``stopped`` / ``frozen`` / ``unknown``), ``cpu_cores``
(``int`` or ``null``), ``memory_mb`` (``int`` or ``null``) and ``warnings``
(list of short strings, may be empty).

Auth mirrors the rest of the Cluster admin surface. The pairing and BLE
routes and the capability-map reads in ``cluster_capability.py`` all require
an admin session. Two layers apply: the auth middleware answers 401 to a
cookie-less caller (this path is not in the exempt list), and
``_require_admin`` answers 403 to a signed-in non-admin.
"""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Request

from tinyagentos.containers import list_containers
from tinyagentos.routes.auth import _require_admin
from tinyagentos.routes.cluster_map import RUNNING_BACKEND_STATUS, _health

router = APIRouter()

logger = logging.getLogger(__name__)


def _container_state(status: str) -> str:
    """Map an incus container status word to the resource row vocabulary."""
    s = (status or "").strip().lower()
    if s in ("running", "stopped", "frozen"):
        return s
    return "unknown"


@router.get("/api/cluster/resources")
async def cluster_resources(request: Request):
    """Aggregated resource inventory across the whole cluster.

    Returns one flat list of every resource taOS runs: agent containers on
    the local host, backends on each worker, installed apps, and the
    controller itself. Read-only by construction --- no state, no mutations,
    aggregated per request from existing getters so it cannot drift from the
    routing/scheduling source of truth.
    """
    ok, err = _require_admin(request)
    if not ok:
        return err

    cluster = request.app.state.cluster_manager
    config = request.app.state.config
    now = time.time()

    resources: list[dict] = []
    errors: list[str] = []

    # ------------------------------------------------------------------ #
    # Controller -- exactly one row, always running on the local host.
    # ------------------------------------------------------------------ #
    resources.append({
        "id": "controller:local",
        "type": "controller",
        "name": "local",
        "worker": "local",
        "project": None,
        "state": "running",
        "cpu_cores": None,
        "memory_mb": None,
        "warnings": [],
    })

    # ------------------------------------------------------------------ #
    # Agents -- one row per agent record, joined to its incus container
    # via list_containers. Worker is always "local"; the agent record's
    # `host` field is a container IP, not a worker name, so it is not used.
    # ------------------------------------------------------------------ #
    # list_containers raises RuntimeError when incus fails. Catch it so the
    # rest of the inventory (backends, apps, controller) still ships;
    # agent rows are still emitted but with state "unknown".
    container_map: dict[str, object] = {}
    try:
        for c in await list_containers(prefix="taos-agent-"):
            container_map[c.name] = c
    except Exception:
        logger.warning("cluster resources: list_containers failed", exc_info=True)
        errors.append("containers")

    for agent in config.agents:
        if not isinstance(agent, dict):
            continue
        name = agent.get("name", "")
        if not name:
            continue
        container_name = agent.get("container_name") or f"taos-agent-{name}"
        info = container_map.get(container_name)
        if info is None:
            state = "unknown"
            cpu_cores = None
            memory_mb = None
            warnings: list[str] = ["container not found"]
        else:
            state = _container_state(info.status)
            cpu_cores = info.cpu_cores
            memory_mb = info.memory_mb
            warnings = []
        resources.append({
            "id": f"agent:{name}",
            "type": "agent",
            "name": name,
            "worker": "local",
            "project": agent.get("project"),
            "state": state,
            "cpu_cores": cpu_cores,
            "memory_mb": memory_mb,
            "warnings": warnings,
        })

    # ------------------------------------------------------------------ #
    # Backends -- one row per backend across every registered worker.
    # Mirrors cluster_map's walk of worker.backends. state is "running"
    # only when the backend status is in RUNNING_BACKEND_STATUS AND the
    # worker's heartbeat health is online or stale; otherwise "stopped".
    # ------------------------------------------------------------------ #
    for worker in cluster.get_workers():
        health = _health(worker.last_heartbeat or 0, now)
        serving = health in ("online", "stale")
        worker_warnings: list[str] = []
        if health == "offline":
            worker_warnings.append("worker offline")
        for b in worker.backends or []:
            if not isinstance(b, dict):
                continue
            bname = str(b.get("name") or b.get("type") or "")
            bstatus = str(b.get("status") or "")
            state = "running" if (bstatus in RUNNING_BACKEND_STATUS and serving) else "stopped"
            resources.append({
                "id": f"backend:{worker.name}:{bname}",
                "type": "backend",
                "name": bname,
                "worker": worker.name,
                "project": None,
                "state": state,
                "cpu_cores": None,
                "memory_mb": None,
                "warnings": list(worker_warnings),
            })

    # ------------------------------------------------------------------ #
    # Apps -- one row per installed app, mirroring GET /api/apps/installed.
    # state is "running" when the app has a runtime host + port, else
    # "unknown". If installed_apps is None, no app rows and no error.
    # ------------------------------------------------------------------ #
    installed_apps = getattr(request.app.state, "installed_apps", None)
    if installed_apps is not None:
        try:
            rows = await installed_apps.list_installed()
            for row in rows:
                app_id: str = row["app_id"]
                try:
                    loc = await installed_apps.get_runtime_location(app_id)
                except Exception:
                    logger.warning("cluster resources: get_runtime_location failed for app %s", app_id, exc_info=True)
                    resources.append({
                        "id": f"app:{app_id}",
                        "type": "app",
                        "name": app_id,
                        "worker": "local",
                        "project": None,
                        "state": "unknown",
                        "cpu_cores": None,
                        "memory_mb": None,
                        "warnings": ["runtime lookup failed"],
                    })
                    continue
                if loc is not None and loc.get("runtime_host") and loc.get("runtime_port"):
                    state = "running"
                    app_warnings: list[str] = []
                else:
                    state = "unknown"
                    app_warnings = ["no runtime location"]
                resources.append({
                    "id": f"app:{app_id}",
                    "type": "app",
                    "name": app_id,
                    "worker": "local",
                    "project": None,
                    "state": state,
                    "cpu_cores": None,
                    "memory_mb": None,
                    "warnings": app_warnings,
                })
        except Exception:
            logger.warning("cluster resources: installed_apps failed", exc_info=True)
            errors.append("installed_apps")

    resources.sort(key=lambda r: (r["type"], r["worker"], r["name"]))

    return {
        "resources": resources,
        "errors": errors,
        "generated_at": now,
    }
