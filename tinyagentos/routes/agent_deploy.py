from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import Request
from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from tinyagentos.routes.agents import DeployAgentRequest

logger = logging.getLogger(__name__)


def validate_framework_and_ram(request: Request, body: "DeployAgentRequest") -> "JSONResponse | None":
    """Validate the requested framework against the catalog and check available RAM.

    Returns a JSONResponse with an error payload if validation fails, or None
    if the request is acceptable and deploy should continue.
    """
    if body.framework != "none":
        registry = request.app.state.registry
        known = {a.id for a in registry.list_available(type_filter="agent-framework")}
        if body.framework not in known:
            return JSONResponse({"error": f"Unknown framework '{body.framework}'. Available: {sorted(known)}"}, status_code=400)

        # Pre-flight RAM check — low-RAM hosts (≤4GB) silently fail at
        # container launch because incus accepts the request but the
        # container never reaches RUNNING.  Check before we mutate any
        # state so the user gets an actionable message, not a generic
        # "failed" with no diagnostic.  (#384)
        hw = getattr(request.app.state, "hardware_profile", None)
        if hw is not None and hw.ram_mb > 0:
            framework = registry.get(body.framework)
            framework_ram = framework.requires.get("ram_mb", 0) if framework else 0
            # Controller overhead ~500 MB + framework dependencies +
            # model headroom ~2 GB (the headroom absorbs the Debian base).
            _CONTROLLER_OVERHEAD_MB = 500
            _MODEL_DEPS_OVERHEAD_MB = 2048
            min_ram = _CONTROLLER_OVERHEAD_MB + framework_ram + _MODEL_DEPS_OVERHEAD_MB
            if hw.ram_mb < min_ram:
                return JSONResponse(
                    {
                        "error": (
                            f"Your device has {hw.ram_mb / 1024:.1f} GB RAM. "
                            f"{body.framework} needs at least "
                            f"{min_ram / 1024:.1f} GB to run with a model. "
                            f"Deploy this agent on a worker with more RAM, or "
                            f"pick a smaller framework."
                        ),
                        "ram_mb": hw.ram_mb,
                        "min_ram_mb": min_ram,
                        "framework": body.framework,
                    },
                    status_code=400,
                )
    return None


def resolve_deploy_routing(request: Request, body: "DeployAgentRequest") -> "JSONResponse | None":
    """Resolve cross-worker deploy routing for the requested model.

    Resolution order (task #176 stub):
    - downloaded_backend_down: 404 (actionable — start the backend)
    - not_found: 404
    - worker + pin conflict: 409
    - worker (no conflict or no pin): 202 routed
    - controller/cloud: returns None to fall through to local deploy

    Returns a JSONResponse to short-circuit, or None to continue the
    controller-local deploy path.
    """
    if body.model:
        from tinyagentos.cluster.model_resolver import (
            describe_downloaded_backend_down,
            resolve_model_location,
        )

        location = resolve_model_location(request, body.model)

        if location.kind == "downloaded_backend_down":
            return JSONResponse(
                {
                    "error": describe_downloaded_backend_down(location, body.model),
                    "model": body.model,
                    "backend": location.backend_id,
                },
                status_code=404,
            )

        if location.kind == "not_found":
            return JSONResponse(
                {
                    "error": (
                        f"model '{body.model}' was not found on the controller, "
                        f"on any online worker, or among configured cloud providers. "
                        f"Download it first or pick a model that is already in the cluster."
                    ),
                },
                status_code=404,
            )

        if location.kind == "worker":
            # Case 5: pin conflict — user asked for a specific worker
            # that does not hold the model.
            if body.target_worker and body.target_worker not in location.hosts:
                return JSONResponse(
                    {
                        "error": (
                            f"model '{body.model}' is not on worker "
                            f"'{body.target_worker}'. It is available on: "
                            f"{location.hosts}. Deploy there, or wait for "
                            f"Phase 1.5 network model placement."
                        ),
                        "model": body.model,
                        "pinned_worker": body.target_worker,
                        "available_on": location.hosts,
                    },
                    status_code=409,
                )

            # Explicit, non-conflicting worker pin: fall through to the deploy
            # path, which now actually creates the agent container ON that
            # worker's nested incus (see configure_remote_deploy + the deployer
            # remote= path). Returning None lets deploy_agent_endpoint handle it.
            if body.target_worker:
                return None

            # No pin, worker-hosted model: auto-placement onto the worker that
            # holds the model is not implemented yet, so return a 202 naming the
            # destination. Deliberately do NOT add a local agent entry -- a ghost
            # entry on the controller for an agent that lives on a worker would
            # confuse both the UI and the LXC bulk-ops endpoints.
            chosen = location.canonical_host
            return JSONResponse(
                {
                    "status": "routed",
                    "name": body.name,
                    "model": body.model,
                    "worker": chosen,
                    "available_on": location.hosts,
                    "message": (
                        f"model '{body.model}' lives on worker '{chosen}'. "
                        f"Pin a target_worker to deploy there now, or wait for "
                        f"auto-placement."
                    ),
                },
                status_code=202,
            )
        # kind == "controller" or "cloud": fall through to the unchanged
        # controller-local deploy path below.
    return None


async def controller_callback_host(request: Request) -> str | None:
    """Return the address a remote worker should use to reach this controller.

    Resolution order:
      1. TAOS_CONTROLLER_CALLBACK_HOST -- explicit operator override. Set this
         when the worker reaches the controller on a specific address, e.g. the
         controller's LAN IP for a bridged worker on the same LAN.
      2. The controller's Tailscale IP (the manual-Tailscale worker path).
      3. The configured host LAN IP (request.app.state.controller_lan_ip).
    Returns None when none is available -- a remote deploy must NOT silently
    fall back to 127.0.0.1 (the worker's own loopback, where the controller is
    not listening).
    """
    import asyncio
    import os
    import subprocess

    override = os.environ.get("TAOS_CONTROLLER_CALLBACK_HOST", "").strip()
    if override:
        return override

    try:
        # Shell out off the event loop so a cold/hanging tailscale never stalls
        # the FastAPI worker for other requests.
        out = await asyncio.to_thread(
            subprocess.run,
            ["tailscale", "ip", "-4"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        ip = (out.stdout or "").strip().splitlines()
        if out.returncode == 0 and ip and ip[0].strip():
            return ip[0].strip()
    except Exception:  # noqa: BLE001
        logger.debug("tailscale ip lookup failed; falling back to controller_lan_ip", exc_info=True)
    # Fall back to the LAN address the controller advertises, if known.
    return getattr(request.app.state, "controller_lan_ip", None)


def _worker_arch_suffix(worker) -> str:
    """Map a worker's reported hardware arch to the base-image arch suffix."""
    hw = getattr(worker, "hardware", None) or {}
    arch = str(hw.get("arch") or hw.get("machine") or "").lower()
    if arch in ("x86_64", "amd64", "x64"):
        return "x64"
    if arch in ("aarch64", "arm64"):
        return "arm64"
    # Unknown: default to x64 (the common worker case); a wrong guess just
    # means the prefetch 404s and the deploy falls back to the apt path.
    return "x64"


async def configure_remote_deploy(request: Request, body: "DeployAgentRequest"):
    """Resolve an explicit target_worker pin into a remote-deploy config.

    Returns ``(remote, taos_host, error_response)``:
      - remote: the worker name to create the container on, or None for a
        normal controller-local deploy.
      - taos_host: the address the agent uses to call back to the controller.
      - error_response: a JSONResponse to short-circuit on (the pinned worker
        is unknown/offline, or the controller has no address the worker could
        reach it on), or None.

    The base-image prefetch is NOT done here -- it would block the deploy
    request on a ~300-500MB download. Call prefetch_base_onto_worker from the
    background deploy task instead.
    """
    if not body.target_worker:
        return None, "127.0.0.1", None

    cm = getattr(request.app.state, "cluster_manager", None)
    worker = cm.get_worker(body.target_worker) if cm is not None else None
    if worker is None:
        return None, "127.0.0.1", JSONResponse(
            {"error": f"worker '{body.target_worker}' is not registered"},
            status_code=409,
        )
    if getattr(worker, "status", "offline") != "online":
        return None, "127.0.0.1", JSONResponse(
            {"error": f"worker '{body.target_worker}' is not online"},
            status_code=409,
        )

    taos_host = await controller_callback_host(request)
    if not taos_host:
        # A remote agent that can't reach the controller would install and then
        # silently fail to phone home (bridge, skills, traces, memory). Refuse
        # rather than start an unreachable agent.
        return None, "127.0.0.1", JSONResponse(
            {
                "error": (
                    "cannot derive a controller address the worker can reach "
                    "(no Tailscale IP and no controller_lan_ip configured); "
                    "remote deploy aborted"
                )
            },
            status_code=500,
        )

    return body.target_worker, taos_host, None


async def prefetch_base_onto_worker(request: Request, body: "DeployAgentRequest") -> None:
    """Best-effort: import the framework's correct-arch base image onto the
    pinned worker's nested incus so the deploy clones it instead of cold-installing.

    Call this from the BACKGROUND deploy task -- it can download ~300-500MB and
    must never block the deploy HTTP request. Any failure is non-fatal: the
    deployer falls back to the cold apt path when the alias is absent.
    """
    if not body.target_worker or not body.framework or body.framework == "none":
        return
    cm = getattr(request.app.state, "cluster_manager", None)
    worker = cm.get_worker(body.target_worker) if cm is not None else None
    if worker is None:
        return
    try:
        from tinyagentos.agent_image import (
            base_image_alias,
            base_image_url_for_alias,
            ensure_image_present,
        )

        alias = base_image_alias(body.framework)
        url = base_image_url_for_alias(alias, _worker_arch_suffix(worker))
        await ensure_image_present(alias, url=url, remote=body.target_worker)
    except Exception:  # noqa: BLE001
        logger.exception(
            "remote prefetch of base image for %s onto %s failed (non-fatal)",
            body.framework, body.target_worker,
        )


async def archive_smoke_check(request: Request, unique_slug: str, framework: str) -> bool:
    """Verify the archive trace path end-to-end after provisioning.

    A failure here does NOT abort the deploy — it surfaces a warning flag.
    Returns True if the smoke-check record was written and read back
    successfully, False otherwise.
    """
    archive = getattr(request.app.state, "archive", None)
    smoke_ok = False
    if archive is not None:
        try:
            await archive.record(
                event_type="agent_deployed",
                data={"slug": unique_slug, "framework": framework},
                agent_name=unique_slug,
                summary=f"deployed {unique_slug}",
            )
            rows = await archive.query(agent_name=unique_slug, limit=1)
            smoke_ok = bool(rows)
        except Exception:
            logger.exception("archive smoke-check failed for %s", unique_slug)
            smoke_ok = False
    return smoke_ok
