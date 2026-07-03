"""Setup-status API — drives the frontend onboarding checklist.

GET  /api/setup/status  → current completion state across all checklist items
POST /api/setup/dismiss → persist user's dismissal of the checklist
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()

_PREF_NAMESPACE = "setup"

# The rkllama probe holds a worker thread while its socket connects, and
# /api/setup/status is polled by the frontend; without a cache and a hard
# timeout, concurrent polls could pin the default-thread executor.
_NPU_PROBE_TTL_S = 5.0
_NPU_PROBE_TIMEOUT_S = 5.0
_npu_probe_cache: tuple[float, bool] = (0.0, False)


async def _npu_backend_running() -> bool:
    """True if either NPU LLM backend (rkllama or rk-llama.cpp) is live.

    Installing either one from the Store satisfies the checklist step —
    they're alternative backends for the same NPU, not a matched pair.
    """
    global _npu_probe_cache
    now = time.monotonic()
    cached_at, cached = _npu_probe_cache
    if cached_at and now - cached_at < _NPU_PROBE_TTL_S:
        return cached

    from tinyagentos.installers.rkllama_installer import rkllama_is_running
    from tinyagentos.installers.rkllamacpp_installer import rkllamacpp_is_running

    async def _probe(fn) -> bool:
        try:
            # Neither probe raises, but both block on socket I/O; keep them
            # off the event loop and cap how long one can hang.
            return await asyncio.wait_for(asyncio.to_thread(fn), _NPU_PROBE_TIMEOUT_S)
        except asyncio.TimeoutError:
            return False

    rkllama_up, rkllamacpp_up = await asyncio.gather(
        _probe(rkllama_is_running), _probe(rkllamacpp_is_running),
    )
    running = rkllama_up or rkllamacpp_up
    _npu_probe_cache = (now, running)
    return running


@router.get("/api/setup/status")
async def setup_status(request: Request):
    """Return onboarding checklist completion state.

    Fields:
      account         — always True for an authenticated request
      has_provider    — any backend configured in config.backends
      taos_model_set  — the taOS agent has a model configured
      has_agent       — at least one deployed agent exists
      memory_enabled  — user completed the taOSmd memory setup wizard
      npu_present     — a Rockchip NPU was detected on this board (#1535)
      npu_backend_running — a live rkllama OR rk-llama.cpp answers locally
      dismissed       — user dismissed the checklist
      complete        — has_provider AND taos_model_set (the core two steps)
    """
    config = request.app.state.config
    store = request.app.state.desktop_settings
    data_dir: Path = getattr(request.app.state, "data_dir", Path("data"))

    # has_provider: any configured backend (cloud or local)
    has_provider = bool(config.backends)

    # taos_model_set: taOS agent's model pref is non-empty
    taos_agent_prefs = await store.get_preference("user", "taos_agent")
    taos_model_set = bool(taos_agent_prefs.get("model"))

    # has_agent: at least one agent in config
    has_agent = bool(config.agents)

    # memory_enabled: taosmd setup wizard completed (taosmd_default.json written)
    memory_enabled = (data_dir / "taosmd_default.json").exists()

    # dismissed: user explicitly dismissed the setup checklist
    setup_prefs = await store.get_preference("user", _PREF_NAMESPACE)
    dismissed = bool(setup_prefs.get("dismissed", False))

    # NPU-conditional step (#1535): on a Rockchip board the checklist offers
    # the rkllama backend install (via the Store), complete once a live
    # rkllama answers. Boards without an NPU never see the step.
    profile = getattr(request.app.state, "hardware_profile", None)
    npu = getattr(profile, "npu", None)
    npu_present = bool(npu is not None and getattr(npu, "type", "none") == "rknpu")
    npu_backend_running = False
    if npu_present:
        npu_backend_running = await _npu_backend_running()

    # complete: the two core steps done
    complete = has_provider and taos_model_set

    return JSONResponse({
        "account": True,
        "has_provider": has_provider,
        "taos_model_set": taos_model_set,
        "has_agent": has_agent,
        "memory_enabled": memory_enabled,
        "npu_present": npu_present,
        "npu_backend_running": npu_backend_running,
        "dismissed": dismissed,
        "complete": complete,
    })


@router.post("/api/setup/dismiss")
async def setup_dismiss(request: Request):
    """Persist the user's dismissal of the setup checklist."""
    store = request.app.state.desktop_settings
    prefs = await store.get_preference("user", _PREF_NAMESPACE)
    prefs["dismissed"] = True
    await store.save_preference("user", _PREF_NAMESPACE, prefs)
    return JSONResponse({"ok": True, "dismissed": True})


@router.post("/api/setup/install-npu-backend")
async def install_npu_backend(request: Request):
    """One-tap install of the NPU LLM backend SERVERS (rkllama + rk-llama.cpp).

    Runs each backend's Store install script via the same privileged
    ScriptInstaller path the Store itself uses (creates/enables/starts the
    systemd unit and health-gates on the service actually answering before
    the script exits 0 — see scripts/install-rknpu.sh and
    scripts/install-rk-llama-cpp.sh). Idempotent: re-running is safe and
    self-heals a half-installed box, exactly like re-clicking Install in
    the Store.

    Deliberately scoped to just these two permissively-licensed
    (Apache-2.0 / MIT) LLM-runtime servers — this is the user's one
    explicit tap of consent for exactly those two installs, nothing more.
    No models are pulled here (those go through the existing per-model
    Store flow with their own license acceptance) and the AGPL RK NPU
    image-gen stack is never touched by this endpoint. Only meaningful on
    a Rockchip NPU board — 400 otherwise.
    """
    profile = getattr(request.app.state, "hardware_profile", None)
    npu = getattr(profile, "npu", None)
    npu_present = bool(npu is not None and getattr(npu, "type", "none") == "rknpu")
    if not npu_present:
        return JSONResponse(
            {"error": "no Rockchip NPU detected on this device"}, status_code=400,
        )

    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        return JSONResponse({"error": "app registry unavailable"}, status_code=500)

    from tinyagentos.install_progress import get_global_store
    from tinyagentos.routes.store_install import _legacy_install
    from tinyagentos.task_utils import _create_supervised_task

    progress = getattr(request.app.state, "install_progress_store", None) or get_global_store()
    bg_tasks = getattr(request.app.state, "_background_tasks", None)
    if bg_tasks is None:
        bg_tasks = set()
        request.app.state._background_tasks = bg_tasks

    backends: dict[str, dict] = {}
    for backend_app_id in ("rkllama", "rk-llama-cpp"):
        manifest = registry.get(backend_app_id) if hasattr(registry, "get") else None
        if manifest is None:
            backends[backend_app_id] = {
                "started": False,
                "error": f"{backend_app_id!r} not in catalog",
            }
            continue

        entry = progress.start(app_id=backend_app_id, target_remote=None)
        install_id = entry.install_id

        async def _run(app_id: str = backend_app_id, install_id: str = install_id) -> None:
            progress.update(install_id, state="unpacking", detail=f"installing {app_id}")
            try:
                # Same dispatcher the Store's install-v2 endpoint uses for
                # type=service manifests — running the real script, gated
                # on it exiting 0 (verified-running), not just "ran".
                resp = await _legacy_install(request, {}, app_id, None)
            except Exception as exc:  # noqa: BLE001
                progress.finish(install_id, success=False, error=str(exc))
                return
            success = getattr(resp, "status_code", 200) < 400
            error = None
            if not success:
                try:
                    error = json.loads(resp.body).get("error")
                except Exception:  # noqa: BLE001
                    error = "install failed"
            progress.finish(install_id, success=success, error=error)

        _create_supervised_task(_run(), bg_tasks)
        backends[backend_app_id] = {"started": True, "install_id": install_id}

    return JSONResponse({"ok": True, "npu_present": True, "backends": backends})
