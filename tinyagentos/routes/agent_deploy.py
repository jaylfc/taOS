from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


def validate_framework_and_ram(request: Request, body) -> "JSONResponse | None":
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
            # Controller needs ~500 MB + Debian base ~256 MB +
            # framework dependencies + a small model (~2 GB headroom).
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


def resolve_deploy_routing(request: Request, body) -> "JSONResponse | None":
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
            # now we return a 202 naming the destination so the caller
            # knows where the agent needs to land. Deliberately do NOT
            # add a local agent entry — a ghost entry on the controller
            # for an agent that lives on Fedora would confuse both the
            # UI and the LXC bulk-ops endpoints.
            chosen = body.target_worker or location.canonical_host
            return JSONResponse(
                {
                    "status": "routed",
                    "name": body.name,
                    "model": body.model,
                    "worker": chosen,
                    "available_on": location.hosts,
                    "message": (
                        f"model '{body.model}' lives on worker '{chosen}'. "
                        f"Routed deploy target only — remote launch lands "
                        f"with Phase 1.5 network model placement."
                    ),
                },
                status_code=202,
            )
        # kind == "controller" or "cloud": fall through to the unchanged
        # controller-local deploy path below.
    return None


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
