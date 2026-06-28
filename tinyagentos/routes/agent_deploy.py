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
    - not_found: 404
    - worker + pin conflict: 409
    - worker (no conflict or no pin): 202 routed
    - controller/cloud: returns None to fall through to local deploy

    Returns a JSONResponse to short-circuit, or None to continue the
    controller-local deploy path.
    """
    if body.model:
        from tinyagentos.cluster.model_resolver import resolve_model_location

        location = resolve_model_location(request, body.model)

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

            # Cases 3 + 4: route to the worker that holds the model.
            # Phase 1.5 will actually instruct the worker to launch; for
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


def controller_callback_host(request: Request) -> str:
    """Return the address a remote worker should use to reach this controller.

    Per the cluster networking model (manual Tailscale, headscale not yet
    configured for taOSgo), the agent container on a worker reaches the
    controller over the tailnet, so prefer the controller's Tailscale IP.
    Falls back to the host LAN IP, then 127.0.0.1 (which only works for a
    local deploy).
    """
    import subprocess

    try:
        out = subprocess.run(
            ["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5
        )
        ip = (out.stdout or "").strip().splitlines()
        if out.returncode == 0 and ip and ip[0].strip():
            return ip[0].strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):  # noqa: BLE001
        pass
    # Fall back to the LAN address the controller advertises, if known.
    lan = getattr(request.app.state, "controller_lan_ip", None)
    return lan or "127.0.0.1"


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
      - error_response: a JSONResponse to short-circuit on (e.g. the pinned
        worker is unknown or offline), or None.

    When a valid online worker is pinned, this also best-effort prefetches the
    correct-arch base image onto that worker's nested incus so the deploy can
    clone instead of cold-installing.
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

    taos_host = controller_callback_host(request)

    # Best-effort: prefetch the framework's base image onto the worker so the
    # deploy clones it instead of running the slow apt path. A failure here is
    # non-fatal -- the deployer falls back to the cold install.
    if body.framework and body.framework != "none":
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

    return body.target_worker, taos_host, None


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
