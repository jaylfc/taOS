from __future__ import annotations

import logging
from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from tinyagentos.installers.lxc_installer import LXCInstaller

logger = logging.getLogger(__name__)

router = APIRouter()


async def _resolve_host(target_remote: str | None) -> str:
    """Return the host the controller uses to reach a service's proxy port.

    - Local (no target_remote): the proxy device binds to 0.0.0.0 on this
      host, so 127.0.0.1 is always reachable by the controller.
    - Remote: look up the registered remote's URL and parse the hostname.
      incus remotes are registered as https://<host>:8443; we extract <host>.
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
    # Fall back to using the remote name itself (useful in DNS-based setups).
    return target_remote


def _get_current_user(request: Request) -> dict | None:
    """Return the currently authenticated user or None."""
    auth = getattr(request.app.state, "auth", None)
    if auth is None:
        return None
    token = request.cookies.get("taos_session", "")
    return auth.session_user(token)


@router.post("/api/store/install-v2")
async def install_app(request: Request):
    body = await request.json()
    app_id = body.get("app_id", "")
    if not app_id:
        return JSONResponse({"error": "app_id required"}, status_code=400)

    # Resolve manifest to determine backend.
    registry = getattr(request.app.state, "registry", None)
    manifest = None
    install_config = {}
    backend = "docker"  # default

    if registry is not None:
        manifest = registry.get(app_id)

    if manifest is not None:
        install_block = getattr(manifest, "install", None) or {}
        if isinstance(install_block, dict):
            install_config = install_block
            backend = install_config.get("backend", install_config.get("method", "docker"))
        # manifest.install might be an object with a .get method or attributes
        elif hasattr(install_block, "get"):
            backend = install_block.get("method", "docker")

    # Allow metadata to override backend only when manifest did not declare one.
    meta = body.get("metadata") or {}
    manifest_declared_backend = manifest is not None
    if not manifest_declared_backend:
        if isinstance(meta, dict) and meta.get("backend"):
            backend = meta["backend"]
        if isinstance(meta, dict) and meta.get("method"):
            backend = meta["method"]
    elif isinstance(meta, dict) and (meta.get("backend") or meta.get("method")):
        meta_backend = meta.get("backend") or meta.get("method")
        if meta_backend != backend:
            logger.warning(
                "install_app: ignoring metadata backend %r — manifest declares %r for %s",
                meta_backend, backend, app_id,
            )
            return JSONResponse(
                {"error": f"backend override {meta_backend!r} contradicts manifest backend {backend!r}"},
                status_code=400,
            )

    if backend == "rkllama":
        # Models served by rkllama are pulled by rkllama itself via its
        # /api/pull endpoint — no file transfer from the controller. We
        # need to know which device is hosting rkllama; default is the
        # controller, override via target_remote.
        from tinyagentos.installers.rkllama_installer import (
            RkllamaInstaller,
            resolve_rkllama_url,
        )

        raw_remote = body.get("target_remote") or install_config.get("target_remote") or ""
        target_remote: str | None = raw_remote if raw_remote and raw_remote != "local" else None
        rkllama_url = resolve_rkllama_url(target_remote)

        # Pick the right variant: explicit body override → manifest's
        # recommended for this hardware tier → first variant.
        variant_id = body.get("variant_id")
        variants = list(getattr(manifest, "variants", []) or [])
        chosen_variant: dict | None = None
        if variant_id:
            chosen_variant = next(
                (v for v in variants if v.get("id") == variant_id), None
            )
        if chosen_variant is None and variants:
            chosen_variant = variants[0]
        if chosen_variant is None:
            return JSONResponse(
                {"error": f"manifest for {app_id!r} declares method=rkllama but has no variants"},
                status_code=400,
            )

        installer = RkllamaInstaller(rkllama_url=rkllama_url)
        try:
            result = await installer.install(
                app_id, install_config, variant=chosen_variant
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("rkllama install failed for %s", app_id)
            return JSONResponse({"error": str(exc)}, status_code=500)

        if not result.get("success"):
            return JSONResponse(
                {"error": result.get("error", "rkllama install failed")},
                status_code=500,
            )

        # Persist install state.
        store = getattr(request.app.state, "installed_apps", None)
        if store is not None:
            await store.install(app_id, body.get("version", ""), meta)
            # rkllama always serves on 8080; the runtime location is the
            # rkllama host itself — that's where the model lives.
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

        return JSONResponse({
            "ok": True,
            "app_id": app_id,
            "status": "installed",
            "rkllama_url": rkllama_url,
            **result,
        })

    if backend == "lxc":
        # LXC installs require admin_password.
        admin_password = body.get("admin_password", "")
        if not admin_password:
            return JSONResponse(
                {"error": "admin_password is required for LXC installs"},
                status_code=400,
            )

        # Resolve target_remote: body field takes precedence over manifest.
        # Normalise empty string / "local" to None (= install on controller).
        raw_remote = body.get("target_remote") or install_config.get("target_remote") or ""
        target_remote: str | None = raw_remote if raw_remote and raw_remote != "local" else None

        # Validate that the requested remote is registered.
        if target_remote:
            try:
                import tinyagentos.containers as containers
                registered = await containers.remote_list()
                known = {r.get("name") for r in registered}
                if target_remote not in known:
                    return JSONResponse(
                        {"error": f"incus remote '{target_remote}' is not registered. "
                         f"Register it first via POST /api/cluster/remotes."},
                        status_code=400,
                    )
            except Exception as exc:
                logger.warning("install-v2: could not verify remote %r: %s", target_remote, exc)

        user = _get_current_user(request)
        # Body overrides win so local-token / non-session callers can seed the
        # admin user explicitly. Gitea rejects "admin" as a reserved name, so
        # the fallback is "owner" when no session user is available.
        taos_username = body.get("taos_username") or (user or {}).get("username") or "owner"
        taos_email = body.get("taos_email") or (user or {}).get("email") or ""

        installer = LXCInstaller()
        try:
            result = await installer.install(
                app_id,
                install_config,
                admin_password=admin_password,
                taos_username=taos_username,
                taos_email=taos_email,
                target_remote=target_remote,
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

        if not result.get("success"):
            return JSONResponse({"error": result.get("error", "install failed")}, status_code=500)

        # Persist in installed-apps store if available.
        store = getattr(request.app.state, "installed_apps", None)
        if store is not None:
            await store.install(app_id, body.get("version", ""), meta)

            # Record runtime location so the proxy can reach the service.
            host_port = result.get("host_port")
            if host_port:
                runtime_host = await _resolve_host(target_remote)
                ui_path = install_config.get("ui_path", "/")
                await store.update_runtime_location(
                    app_id,
                    host=runtime_host,
                    port=host_port,
                    backend="lxc",
                    ui_path=ui_path,
                )

        # Also mark as installed in the registry — /api/store/catalog reads
        # `installed` from there, so without this the Store UI reverts to
        # "Install" after a page reload (issue #317).
        if registry is not None:
            version = body.get("version") or (getattr(manifest, "version", "") if manifest else "")
            registry.mark_installed(app_id, version)

        resp_target = target_remote or "local"
        return JSONResponse({"ok": True, "app_id": app_id, "status": "installed",
                             "target_remote": resp_target, **result})

    # Default: delegate to InstalledAppsStore (docker/pip/download).
    store = request.app.state.installed_apps
    await store.install(app_id, body.get("version", ""), meta)

    # If the caller specified a target_remote (e.g. from the Store
    # filter when exactly one device is selected), record the runtime
    # location against that remote. Models live on the device that runs
    # them — without this the controller is implied.
    raw_remote = body.get("target_remote") or ""
    target_remote: str | None = (
        raw_remote if raw_remote and raw_remote != "local" else None
    )
    if target_remote is not None:
        # Validate the remote is registered, same check as the LXC branch.
        try:
            import tinyagentos.containers as containers
            registered = await containers.remote_list()
            known = {r.get("name") for r in registered}
            if target_remote not in known:
                return JSONResponse(
                    {
                        "error": (
                            f"incus remote '{target_remote}' is not registered."
                            f" Register it first via POST /api/cluster/remotes."
                        )
                    },
                    status_code=400,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "install-v2 default: could not verify remote %r: %s",
                target_remote, exc,
            )
        runtime_host = await _resolve_host(target_remote)
        # Default-branch installs don't expose a stable proxy port (they
        # run in-process on the controller or via per-backend daemons).
        # Use 0 as a "no proxy port" sentinel; the runtime_host is still
        # recorded so /installed-v2 reports the correct device. Note: the
        # /apps/<id>/ proxy in service_proxy.py does NOT yet guard on
        # port=0 — accessing the proxy URL for a port=0 entry will 502.
        # Non-LXC apps are accessed via their native URL, not the /apps/
        # proxy, so this is acceptable today; tracked as a follow-up.
        await store.update_runtime_location(
            app_id,
            host=runtime_host,
            port=0,
            backend=meta.get("backend", "") if isinstance(meta, dict) else "",
            ui_path=(install_config.get("ui_path", "/")
                     if isinstance(install_config, dict) else "/"),
        )

    if registry is not None:
        version = body.get("version") or (getattr(manifest, "version", "") if manifest else "")
        registry.mark_installed(app_id, version)
    return JSONResponse({"ok": True, "app_id": app_id, "status": "installed"})


@router.post("/api/store/uninstall-v2")
async def uninstall_app(request: Request):
    body = await request.json()
    app_id = body.get("app_id", "")
    if not app_id:
        return JSONResponse({"error": "app_id required"}, status_code=400)

    # Determine backend from manifest or body metadata.
    registry = getattr(request.app.state, "registry", None)
    backend = "docker"
    if registry is not None:
        manifest = registry.get(app_id)
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
