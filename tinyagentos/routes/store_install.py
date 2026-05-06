"""Store install dispatcher driven by the manifest dependency resolver.

Reads variant.requires.backends, asks the resolver which backend should
serve the model on the target device, and (when the backend is missing)
recursively installs that backend's service manifest first via this same
dispatcher. Recursion is bounded at one level — backend service manifests
must not declare requires.backends themselves (enforced by the audit
script).
"""
from __future__ import annotations

import logging
from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from tinyagentos.catalog.resolver import (
    DeviceCapability,
    ResolveErr,
    ResolveOk,
    classify,
    resolve,
)
from tinyagentos.cluster.capabilities import hardware_to_targets
from tinyagentos.installers.base import get_installer
from tinyagentos.installers.lxc_installer import LXCInstaller

logger = logging.getLogger(__name__)
router = APIRouter()


_KNOWN_BACKENDS = {
    "rkllama", "rk-llama-cpp", "ollama", "llama-cpp",
    "mlx", "vllm", "comfyui", "transformers",
}

# Backend ID → install method known to get_installer().
# rkllama has a purpose-built installer (calls /api/pull, manages symlinks,
# restarts systemd units). rk-llama-cpp models are downloaded to disk and
# loaded by the rk-llama-cpp runtime on demand, same as other backends.
# Future per-backend installers (OllamaInstaller using `ollama pull`, etc.)
# can land as follow-ups; download is the safest default in the meantime.
_BACKEND_TO_METHOD: dict[str, str] = {
    "rkllama": "rkllama",
    "rk-llama-cpp": "download",
    "ollama": "download",
    "llama-cpp": "download",
    "mlx": "download",
    "vllm": "download",
    "comfyui": "download",
    "transformers": "download",
    "diffusers": "download",
    "sentence-transformers": "download",
    "whisper-cpp": "download",
    "piper": "download",
    "onnxruntime": "download",
    "nemo": "download",
    "ezrknpu": "download",
    "sd-webui": "download",
}


async def get_device_capability(request: Request, target_remote: str | None) -> DeviceCapability:
    """Build a DeviceCapability snapshot for the (local | remote) target."""
    if not target_remote or target_remote == "local":
        hp = getattr(request.app.state, "hardware_profile", None)
        hw = getattr(hp, "hardware", {}) if hp else {}
        targets = tuple(hardware_to_targets(hw))
        ram_mb = int(hw.get("ram_mb", 0) or 0)
        vram_mb = int((hw.get("gpu") or {}).get("vram_mb", 0) or 0)
        # Free disk lives on the hardware profile's disk dict (best-effort).
        disk_total = int((hw.get("disk") or {}).get("total_gb", 0) or 0) * 1024
        free_disk_mb = max(0, disk_total)  # we don't track used precisely; assume room.
        registry = getattr(request.app.state, "registry", None)
        installed_backends: tuple[str, ...] = ()
        if registry is not None:
            try:
                installed = registry.list_installed()
                ids = {entry["id"] for entry in installed if isinstance(entry, dict)}
                installed_backends = tuple(b for b in _KNOWN_BACKENDS if b in ids)
            except Exception:  # noqa: BLE001
                installed_backends = ()
        return DeviceCapability(
            device_id="local",
            targets=targets,
            total_ram_mb=ram_mb,
            total_vram_mb=vram_mb,
            free_disk_mb=free_disk_mb,
            installed_backends=installed_backends,
        )

    # Remote: query the worker registry's last-known capacity.
    cluster = getattr(request.app.state, "cluster_manager", None)
    if cluster is None:
        return DeviceCapability(
            device_id=target_remote,
            targets=("cpu",), total_ram_mb=0, total_vram_mb=0,
            free_disk_mb=0, installed_backends=(),
        )
    worker = cluster.get_worker(target_remote) if hasattr(cluster, "get_worker") else None
    if worker is None:
        return DeviceCapability(
            device_id=target_remote,
            targets=("cpu",), total_ram_mb=0, total_vram_mb=0,
            free_disk_mb=0, installed_backends=(),
        )
    targets = tuple(hardware_to_targets(getattr(worker, "hardware", {}) or {}))
    ram_mb = int((getattr(worker, "hardware", {}) or {}).get("ram_mb", 0) or 0)
    vram_mb = int(((getattr(worker, "hardware", {}) or {}).get("gpu") or {}).get("vram_mb", 0) or 0)
    disk_cap = max(
        0,
        int(getattr(worker, "storage_cap_bytes", 0)) - int(getattr(worker, "storage_used_bytes", 0)),
    ) // (1024 * 1024)
    installed_backends = tuple(
        b.get("name", "") for b in (getattr(worker, "backends", None) or []) if b.get("name")
    )
    return DeviceCapability(
        device_id=target_remote,
        targets=targets,
        total_ram_mb=ram_mb,
        total_vram_mb=vram_mb,
        free_disk_mb=int(disk_cap),
        installed_backends=installed_backends,
    )


async def _resolve_host(target_remote: str | None) -> str:
    """Return the host the controller uses to reach a service's proxy port.

    Preserved from the previous dispatcher — used by other endpoints in this file.
    """
    if not target_remote:
        return "127.0.0.1"
    try:
        import tinyagentos.containers as containers
        remotes = await containers.remote_list()
        for r in remotes:
            if r.get("name") == target_remote:
                addr = r.get("addr", "")
                parsed = urlparse(addr)
                if parsed.hostname:
                    return parsed.hostname
    except Exception:
        logger.warning("_resolve_host: failed to look up remote %r", target_remote)
    return target_remote


def _get_current_user(request: Request) -> dict | None:
    """Return the currently authenticated user or None."""
    auth = getattr(request.app.state, "auth", None)
    if auth is None:
        return None
    token = request.cookies.get("taos_session", "")
    return auth.session_user(token)


def _registry_get(registry, app_id: str):
    """Look up a manifest by ID using whichever method the registry exposes."""
    if hasattr(registry, "get_app"):
        return registry.get_app(app_id)
    if hasattr(registry, "get"):
        return registry.get(app_id)
    return None


async def _legacy_install(request: Request, body: dict, app_id: str | None, target_remote: str | None) -> JSONResponse:
    """Legacy method-driven install path for non-model manifests.

    Handles LXC services, pip/docker agent frameworks, and any manifest type
    that doesn't go through the resolver. Preserved from the pre-resolver
    dispatcher so existing tests and callers continue to work.
    """
    if not app_id:
        return JSONResponse({"error": "app_id required"}, status_code=400)

    registry = getattr(request.app.state, "registry", None)
    manifest = _registry_get(registry, app_id) if registry else None
    install_config: dict = {}
    backend = "docker"

    if manifest is not None:
        install_block = getattr(manifest, "install", None) or {}
        if isinstance(install_block, dict):
            install_config = install_block
            backend = install_config.get("backend", install_config.get("method", "docker"))
        elif hasattr(install_block, "get"):
            backend = install_block.get("method", "docker")

    meta = body.get("metadata") or {}
    manifest_declared = manifest is not None
    if not manifest_declared:
        if isinstance(meta, dict) and meta.get("backend"):
            backend = meta["backend"]
        if isinstance(meta, dict) and meta.get("method"):
            backend = meta["method"]
    elif isinstance(meta, dict) and (meta.get("backend") or meta.get("method")):
        meta_backend = meta.get("backend") or meta.get("method")
        if meta_backend != backend:
            logger.warning(
                "_legacy_install: ignoring metadata backend %r — manifest declares %r for %s",
                meta_backend, backend, app_id,
            )
            return JSONResponse(
                {"error": f"backend override {meta_backend!r} contradicts manifest backend {backend!r}"},
                status_code=400,
            )

    if backend == "rkllama":
        from tinyagentos.installers.rkllama_installer import (
            RkllamaInstaller,
            resolve_rkllama_url,
        )
        raw_remote = body.get("target_remote") or install_config.get("target_remote") or ""
        _target_remote: str | None = raw_remote if raw_remote and raw_remote != "local" else None
        rkllama_url = resolve_rkllama_url(_target_remote)
        variant_id = body.get("variant_id")
        variants = list(getattr(manifest, "variants", []) or [])
        chosen_variant: dict | None = None
        if variant_id:
            chosen_variant = next((v for v in variants if v.get("id") == variant_id), None)
        if chosen_variant is None and variants:
            chosen_variant = variants[0]
        if chosen_variant is None:
            return JSONResponse(
                {"error": f"manifest for {app_id!r} declares method=rkllama but has no variants"},
                status_code=400,
            )
        installer = RkllamaInstaller(rkllama_url=rkllama_url)
        try:
            result = await installer.install(app_id, install_config, variant=chosen_variant)
        except Exception as exc:  # noqa: BLE001
            logger.exception("rkllama install failed for %s", app_id)
            return JSONResponse({"error": str(exc)}, status_code=500)
        if not result.get("success"):
            return JSONResponse(
                {"error": result.get("error", "rkllama install failed")}, status_code=500
            )
        store = getattr(request.app.state, "installed_apps", None)
        if store is not None:
            await store.install(app_id, body.get("version", ""), meta)
            await store.update_runtime_location(
                app_id,
                host=urlparse(rkllama_url).hostname or "localhost",
                port=urlparse(rkllama_url).port or 8080,
                backend="rkllama",
                ui_path="/",
            )
        if registry is not None:
            version = body.get("version") or (getattr(manifest, "version", "") if manifest else "")
            registry.mark_installed(app_id, version)
        return JSONResponse({"ok": True, "app_id": app_id, "status": "installed", "rkllama_url": rkllama_url, **result})

    if backend == "lxc":
        admin_password = body.get("admin_password", "")
        if not admin_password:
            return JSONResponse(
                {"error": "admin_password is required for LXC installs"}, status_code=400
            )
        raw_remote = body.get("target_remote") or install_config.get("target_remote") or ""
        _target_remote = raw_remote if raw_remote and raw_remote != "local" else None
        if _target_remote:
            try:
                import tinyagentos.containers as containers
                registered = await containers.remote_list()
                known = {r.get("name") for r in registered}
                if _target_remote not in known:
                    return JSONResponse(
                        {"error": f"incus remote '{_target_remote}' is not registered. "
                         f"Register it first via POST /api/cluster/remotes."},
                        status_code=400,
                    )
            except Exception as exc:
                logger.warning("_legacy_install: could not verify remote %r: %s", _target_remote, exc)
        user = _get_current_user(request)
        taos_username = body.get("taos_username") or (user or {}).get("username") or "owner"
        taos_email = body.get("taos_email") or (user or {}).get("email") or ""
        installer = LXCInstaller()
        try:
            result = await installer.install(
                app_id, install_config,
                admin_password=admin_password,
                taos_username=taos_username,
                taos_email=taos_email,
                target_remote=_target_remote,
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)
        if not result.get("success"):
            return JSONResponse({"error": result.get("error", "install failed")}, status_code=500)
        store = getattr(request.app.state, "installed_apps", None)
        if store is not None:
            await store.install(app_id, body.get("version", ""), meta)
            host_port = result.get("host_port")
            if host_port:
                runtime_host = await _resolve_host(_target_remote)
                ui_path = install_config.get("ui_path", "/")
                await store.update_runtime_location(
                    app_id, host=runtime_host, port=host_port, backend="lxc", ui_path=ui_path,
                )
        if registry is not None:
            version = body.get("version") or (getattr(manifest, "version", "") if manifest else "")
            registry.mark_installed(app_id, version)
        resp_target = _target_remote or "local"
        return JSONResponse({"ok": True, "app_id": app_id, "status": "installed", "target_remote": resp_target, **result})

    # Default: delegate to InstalledAppsStore (docker/pip/download).
    store = request.app.state.installed_apps
    await store.install(app_id, body.get("version", ""), meta)
    raw_remote = body.get("target_remote") or ""
    _target_remote = raw_remote if raw_remote and raw_remote != "local" else None
    if _target_remote is not None:
        try:
            import tinyagentos.containers as containers
            registered = await containers.remote_list()
            known = {r.get("name") for r in registered}
            if _target_remote not in known:
                return JSONResponse(
                    {"error": (
                        f"incus remote '{_target_remote}' is not registered."
                        f" Register it first via POST /api/cluster/remotes."
                    )},
                    status_code=400,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("_legacy_install default: could not verify remote %r: %s", _target_remote, exc)
        runtime_host = await _resolve_host(_target_remote)
        await store.update_runtime_location(
            app_id, host=runtime_host, port=0,
            backend=meta.get("backend", "") if isinstance(meta, dict) else "",
            ui_path=(install_config.get("ui_path", "/") if isinstance(install_config, dict) else "/"),
        )
    if registry is not None:
        version = body.get("version") or (getattr(manifest, "version", "") if manifest else "")
        registry.mark_installed(app_id, version)
    return JSONResponse({"ok": True, "app_id": app_id, "status": "installed"})


@router.post("/api/store/install-v2")
async def install_app(request: Request):
    """Resolver-driven install. Chains backend → model in one user click.

    For type=model manifests the resolver picks the best backend and
    optionally chains a backend install first. Non-model manifests (type=service,
    type=agent-framework, etc.) use the legacy method-driven path so that LXC,
    pip, and docker installs continue to work unchanged.
    """
    body = await request.json()
    manifest_id = body.get("manifest_id") or body.get("app_id")
    variant_id = body.get("variant_id", "auto")
    target_remote = body.get("target_remote") or None
    force = bool(body.get("force", False))

    registry = getattr(request.app.state, "registry", None)
    manifest = _registry_get(registry, manifest_id) if registry and manifest_id else None
    if manifest is None:
        # Fall through to legacy path if the registry doesn't know this ID —
        # allows callers that don't have a manifest to install via metadata.
        return await _legacy_install(request, body, manifest_id, target_remote)

    # Non-model manifests use the legacy method-driven path.
    if getattr(manifest, "type", "model") != "model":
        return await _legacy_install(request, body, manifest_id, target_remote)

    device = await get_device_capability(request, target_remote)
    manifest_dict = {
        "id": manifest.id,
        "type": manifest.type,
        "variants": manifest.variants,
        "context_window": getattr(manifest, "context_window", 0),
    }
    result = resolve(manifest_dict, variant_id, device, force=force)
    if isinstance(result, ResolveErr):
        return JSONResponse(
            {
                "error": result.reason,
                "near_miss": result.near_miss,
                "suggestions": result.suggestions,
            },
            status_code=422,
        )

    chain: list[dict] = []

    # Install the backend if missing.
    if result.action == "install_chain":
        backend_manifest = _registry_get(registry, result.backend_id)
        if backend_manifest is None:
            return JSONResponse(
                {"error": f"backend service manifest {result.backend_id!r} not in catalog"},
                status_code=500,
            )
        install_block = getattr(backend_manifest, "install", None) or {}
        backend_method = (
            install_block.get("method") if isinstance(install_block, dict) else None
        )
        if not backend_method:
            return JSONResponse(
                {"error": f"backend {result.backend_id!r} has no install.method"},
                status_code=500,
            )
        backend_installer = get_installer(backend_method)
        be_result = await backend_installer.install(
            result.backend_id,
            install_config=install_block if isinstance(install_block, dict) else {},
        )
        if not be_result.get("success"):
            return JSONResponse(
                {
                    "error": (
                        f"backend install failed for {result.backend_id!r}: "
                        f"{be_result.get('error', 'unknown')}"
                    ),
                    "chain": chain + [{"step": "backend", "id": result.backend_id, "status": "failed"}],
                },
                status_code=500,
            )
        if hasattr(registry, "mark_installed"):
            try:
                registry.mark_installed(result.backend_id, getattr(backend_manifest, "version", ""))
            except Exception:  # noqa: BLE001
                pass
        chain.append({"step": "backend", "id": result.backend_id, "status": "installed"})

    # Install the model via the chosen backend's installer.
    chosen_variant = next(
        (v for v in manifest.variants if isinstance(v, dict) and v.get("id") == result.variant_id),
        None,
    )
    if chosen_variant is None:
        return JSONResponse(
            {"error": f"variant {result.variant_id!r} not found in manifest"},
            status_code=500,
        )
    install_method = _BACKEND_TO_METHOD.get(result.backend_id)
    if install_method is None:
        return JSONResponse(
            {
                "error": (
                    f"backend {result.backend_id!r} has no installer mapping. "
                    "Add an entry to _BACKEND_TO_METHOD in store_install.py."
                ),
            },
            status_code=500,
        )
    model_installer = get_installer(install_method)
    install_config = dict(getattr(manifest, "install", None) or {})
    install_config["backend"] = result.backend_id
    model_result = await model_installer.install(
        manifest.id,
        install_config=install_config,
        variant=chosen_variant,
        target_remote=target_remote,
    )
    if not model_result.get("success"):
        return JSONResponse(
            {
                "error": f"model install failed: {model_result.get('error', 'unknown')}",
                "chain": chain + [{"step": "model", "id": manifest.id, "status": "failed"}],
            },
            status_code=500,
        )
    if hasattr(registry, "mark_installed"):
        try:
            registry.mark_installed(manifest.id, getattr(manifest, "version", ""))
        except Exception:  # noqa: BLE001
            pass
    chain.append({"step": "model", "id": manifest.id, "status": "installed"})

    return JSONResponse({"chain": chain, "compat": classify(manifest_dict, device)})


@router.post("/api/store/uninstall-v2")
async def uninstall_app(request: Request):
    body = await request.json()
    app_id = body.get("app_id", "")
    if not app_id:
        return JSONResponse({"error": "app_id required"}, status_code=400)

    # Determine backend from manifest or body metadata.
    registry = getattr(request.app.state, "registry", None)
    backend = "docker"
    manifest = None
    if registry is not None:
        manifest = _registry_get(registry, app_id)
        if manifest is not None:
            install_block = getattr(manifest, "install", None) or {}
            if isinstance(install_block, dict):
                backend = install_block.get("backend", install_block.get("method", "docker"))
    meta = body.get("metadata") or {}
    if manifest is None:
        if isinstance(meta, dict) and meta.get("backend"):
            backend = meta["backend"]
        if isinstance(meta, dict) and meta.get("method"):
            backend = meta["method"]

    # Retrieve the runtime location before touching the store so we know
    # whether the container lives on a remote host.
    _store_for_loc = getattr(request.app.state, "installed_apps", None)
    _runtime_loc = None
    if _store_for_loc is not None:
        _runtime_loc = await _store_for_loc.get_runtime_location(app_id)

    container_error: str | None = None
    if backend == "lxc":
        target_remote: str | None = None
        if _runtime_loc is not None:
            _runtime_host = _runtime_loc.get("runtime_host", "")
            # 127.0.0.1 means local; anything else is a remote host name.
            if _runtime_host and _runtime_host != "127.0.0.1":
                target_remote = _runtime_host
        try:
            installer = LXCInstaller()
            uninstall_result = await installer.uninstall(app_id, target_remote=target_remote)
            if not uninstall_result.get("success", False):
                container_error = (
                    uninstall_result.get("error")
                    or uninstall_result.get("output")
                    or "LXC uninstall failed"
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("LXC container destroy failed for %s: %s", app_id, exc)
            container_error = str(exc)

    if container_error is not None:
        # Container removal failed; do not clear the store record so the
        # orphaned container can be retried or manually cleaned up.
        return JSONResponse(
            {"ok": False, "app_id": app_id, "container_error": container_error},
            status_code=500,
        )

    store = request.app.state.installed_apps
    removed = await store.uninstall(app_id)
    await store.remove_runtime_location(app_id)
    if registry is not None:
        registry.mark_uninstalled(app_id)
    resp: dict = {"ok": removed, "app_id": app_id, "status": "uninstalled" if removed else "not_installed"}
    return JSONResponse(resp)


@router.get("/api/store/installed-v2")
async def list_installed(request: Request):
    store = request.app.state.installed_apps
    items = await store.list_installed()
    # Annotate each item with its runtime location so the UI can show which
    # host each app currently lives on.
    for item in items:
        loc = await store.get_runtime_location(item["app_id"])
        if loc:
            item["runtime_host"] = loc["runtime_host"]
            item["runtime_port"] = loc["runtime_port"]
            item["runtime_backend"] = loc["backend"]
        else:
            item["runtime_host"] = None
            item["runtime_port"] = None
            item["runtime_backend"] = None
    return JSONResponse({"installed": items})
