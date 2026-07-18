"""Store install dispatcher driven by the manifest dependency resolver.

Reads variant.requires.backends, asks the resolver which backend should
serve the model on the target device, and (when the backend is missing)
recursively installs that backend's service manifest first via this same
dispatcher. Recursion is bounded at one level — backend service manifests
must not declare requires.backends themselves (enforced by the audit
script).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from tinyagentos.agent_image import (
    FRAMEWORKS_WITH_DEDICATED_BASE,
    base_image_alias,
    base_image_url_for_alias,
    ensure_image_present,
)
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
from tinyagentos.task_utils import _create_supervised_task

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
    "rk-llama-cpp": "rkllamacpp",
    "ollama": "ollama",
    "llama-cpp": "download",
    "mlx": "download",
    "vllm": "download",
    "comfyui": "download",
    # Multi-file backends — model weights ship as an HF directory (config,
    # tokenizer, sharded safetensors / .bin chunks). The huggingface
    # installer downloads the whole repo into the shared layout. Single-
    # file variants on these backends still work because the installer
    # delegates back to DownloadInstaller when a variant has no hf_repo.
    "transformers": "huggingface",
    "diffusers": "huggingface",
    "sentence-transformers": "huggingface",
    "mlc-llm": "huggingface",
    "mlc": "huggingface",
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
        # HardwareProfile is a flat dataclass — there is no .hardware
        # attribute on it. asdict() yields {ram_mb, cpu, gpu, npu, disk,
        # os}, which is the shape hardware_to_targets and the resolver
        # both expect.
        hw = asdict(hp) if hp is not None else {}
        targets = tuple(hardware_to_targets(hw))
        ram_mb = int(hw.get("ram_mb", 0) or 0)
        vram_mb = int((hw.get("gpu") or {}).get("vram_mb", 0) or 0)
        # Free disk lives on the hardware profile's disk dict (best-effort).
        free_gb = int((hw.get("disk") or {}).get("free_gb", 0) or 0)
        free_disk_mb = max(0, free_gb * 1024)
        if free_disk_mb == 0:
            # Hardware probe didn't report disk OR detection failed entirely.
            # Fall back to OS-level free space against the install dir.
            try:
                import shutil as _shutil
                from pathlib import Path as _Path
                install_dir = getattr(request.app.state, "data_dir", _Path("/"))
                usage = _shutil.disk_usage(str(install_dir))
                free_disk_mb = int(usage.free // (1024 * 1024))
            except Exception:
                free_disk_mb = 0  # truly unknowable; original behaviour
        registry = getattr(request.app.state, "registry", None)
        installed_backends: tuple[str, ...] = ()
        if registry is not None:
            try:
                installed = registry.list_installed()
                ids = {entry["id"] for entry in installed if isinstance(entry, dict)}
                installed_backends = tuple(b for b in _KNOWN_BACKENDS if b in ids)
            except Exception:  # noqa: BLE001
                installed_backends = ()
        # A backend that is actually running but missing from the registry must
        # still count as installed, so the resolver uses it (action="use")
        # instead of trying to (re)install it. rkllama runs as a bare process on
        # the Pi and is often not registered, which made every model install
        # take a broken install_chain path (issue #783 follow-up).
        if "rkllama" not in installed_backends:
            try:
                from tinyagentos.installers.rkllama_installer import rkllama_is_running
                # Offload the blocking socket probe so it never stalls the loop.
                if await asyncio.to_thread(rkllama_is_running):
                    installed_backends = installed_backends + ("rkllama",)
            except Exception as exc:  # noqa: BLE001
                logger.debug("rkllama runtime detection skipped: %s", exc)
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
    """Look up a manifest by ID."""
    return registry.get(app_id)


def _verify_manifest_for_install(
    manifest_id: str,
    registry,
    store_signing_pubkey: bytes | None,
) -> tuple[bool, str | None]:
    """Verify a manifest's Ed25519 signature before installing.

    Returns ``(allowed, error_detail)``:
    - ``(True, None)`` — proceed with install.
    - ``(False, reason)`` — block install with the given error string.

    When the signing pubkey is not configured, the gate is skipped (allowed).
    When a manifest has no stored signature (e.g. it was catalog-loaded
    before signing was enabled), the gate is also skipped (allowed) rather
    than returning a hard 403 — the absence of a signature is not evidence
    of tampering.
    """
    if store_signing_pubkey is None or registry is None:
        return True, None
    if not hasattr(registry, "verify_manifest_signature"):
        return True, None

    stored_sig = registry.get_signature(manifest_id)
    if stored_sig is None:
        # No stored signature.  If signing is configured and this manifest
        # failed to sign during catalog load, block it — treating a signing
        # failure as "merely unsigned" would bypass the install gate.
        if (
            hasattr(registry, "is_signing_failure")
            and registry.is_signing_failure(manifest_id)
        ):
            return False, (
                f"manifest {manifest_id} failed to sign during catalog load — "
                "the manifest may be malformed or the signing key may be invalid"
            )
        # Otherwise: never signed — manifest was loaded before signing was
        # enabled.  Skip the gate; absence of a signature is not evidence of
        # tampering.
        return True, None

    if not registry.verify_manifest_signature(manifest_id, store_signing_pubkey):
        return False, "manifest signature verification failed"

    return True, None


async def _install_agent_framework(
    request: Request, manifest, app_id: str, meta: dict, body: dict,
) -> JSONResponse:
    """Install an agent-framework manifest declaring ``install.method: script``.

    That script (e.g. hermes/openclaw's ``scripts/install.sh``) is written to
    run once *inside a fresh per-agent LXC container* at deploy time — it
    installs Node/npm or pip packages, writes container-local config, and
    enables systemd units for that container (see ``deployer.py``'s
    method=="script" branch). Running it here, on the taOS controller host,
    at Store-install time would be wrong: there is no target container yet,
    and the script would pollute the host instead.

    So "installing" a framework from the Store means: enable it for deploy
    (mark it installed so the Store/Agents app reflect real state — the
    adapter registry already knows how to deploy every listed framework),
    kick off a best-effort background prefetch of its dedicated base image
    (if it has one) so the first real deploy is fast, and notify the user it
    is ready to deploy. Prefetch failure is non-fatal: the framework stays
    enabled and a first deploy falls back to a cold image build.
    """
    registry = getattr(request.app.state, "registry", None)
    store = getattr(request.app.state, "installed_apps", None)
    version = body.get("version") or getattr(manifest, "version", "") or ""

    if store is not None:
        await store.install(app_id, version, meta)
    if registry is not None and hasattr(registry, "mark_installed"):
        registry.mark_installed(app_id, version)

    prefetch_started = False
    if app_id in FRAMEWORKS_WITH_DEDICATED_BASE:
        alias = base_image_alias(app_id)
        url = base_image_url_for_alias(alias)
        bg_tasks = getattr(request.app.state, "_background_tasks", None)
        if bg_tasks is None:
            bg_tasks = set()
            request.app.state._background_tasks = bg_tasks
        _create_supervised_task(ensure_image_present(alias, url), bg_tasks)
        prefetch_started = True

    notifs = getattr(request.app.state, "notifications", None)
    if notifs is not None:
        try:
            await notifs.add(
                title=f"{getattr(manifest, 'name', app_id)} installed",
                message="You can now deploy it from the Agents app",
                level="success",
                source="agent_framework",
                data={"framework": app_id},
            )
        except Exception:  # noqa: BLE001
            logger.warning("_install_agent_framework: notification failed for %s", app_id, exc_info=True)

    return JSONResponse({
        "ok": True,
        "app_id": app_id,
        "status": "installed",
        "prefetch": "started" if prefetch_started else "skipped",
    })


def _docker_published_port(install_config: dict) -> int:
    """Return the first host port a docker service publishes, or 0.

    DockerInstaller maps each declared port as ``{p}:{p}`` so the host port
    equals the container port. Ports may be declared either at the top level
    (``install.ports``) or nested under ``install.requires.ports`` — mirror
    the precedence DockerInstaller._generate_compose uses (requires first).
    """
    if not isinstance(install_config, dict):
        return 0
    ports = (install_config.get("requires") or {}).get("ports") or install_config.get("ports") or []
    for p in ports:
        try:
            return int(p)
        except (TypeError, ValueError):
            continue
    return 0


def _manifest_first_port(manifest) -> int:
    """Return the first port declared in a manifest's top-level ``requires.ports``.

    Script-backed service manifests (rkllama, rk-llama-cpp, ollama, ...)
    declare their port under the manifest's top-level ``requires`` block,
    not under ``install`` (that's a docker-only convention — see
    ``_docker_published_port``).
    """
    ports = (getattr(manifest, "requires", None) or {}).get("ports") or []
    for p in ports:
        try:
            return int(p)
        except (TypeError, ValueError):
            continue
    return 0


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

    # Agent frameworks whose install.sh runs inside a per-agent LXC container
    # at deploy time (method=="script") don't get a generic host-side
    # install — see _install_agent_framework for why and what "install"
    # means for them instead. Pip/docker-based frameworks (smolagents,
    # langroid, ...) are unaffected and fall through to the normal dispatch
    # below, unchanged.
    if manifest is not None and getattr(manifest, "type", "") == "agent-framework" and backend == "script":
        return await _install_agent_framework(request, manifest, app_id, meta, body)

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
                port=urlparse(rkllama_url).port or 7833,
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

    # script — host-side backend/plugin manifests (e.g. ollama, rkllama,
    # tailscale) that declare install.method: script. Previously this fell
    # straight through to the default branch below and silently marked the
    # app installed without ever running the script (same class of bug as
    # #410 below for docker/pip, filed as #1582). Actually run it via
    # ScriptInstaller and only mark installed / record the runtime location
    # when the script EXITS 0. These scripts are written to be their own
    # health gate (e.g. install-rkllama.sh delegates to install-rknpu.sh,
    # which polls the service's HTTP port before returning) so rc==0 here
    # means the service is actually up, not just that files landed on disk.
    # On failure, surface the real script error and do not mark installed.
    if backend == "script":
        installer = get_installer("script")
        try:
            inst_result = await installer.install(app_id, install_config)
        except Exception as exc:  # noqa: BLE001
            logger.exception("_legacy_install: script installer raised for %s", app_id)
            return JSONResponse({"error": f"script install failed: {exc}"}, status_code=500)
        if not inst_result.get("success"):
            return JSONResponse(
                {"error": inst_result.get("error", "script install failed")},
                status_code=500,
            )
        store = getattr(request.app.state, "installed_apps", None)
        if store is not None:
            await store.install(app_id, body.get("version", ""), meta)
            # Record where the now-verified-running service actually
            # listens, same as the docker branch below, so it gets a
            # Launchpad shortcut / proxy target. Script-backed services are
            # host-level systemd units (installed via sudo on this
            # controller), not remote-incus deploys, so always 127.0.0.1.
            script_port = _manifest_first_port(manifest) if manifest is not None else 0
            if script_port:
                await store.update_runtime_location(
                    app_id, host="127.0.0.1", port=script_port, backend=app_id,
                    ui_path=(install_config.get("ui_path", "/") if isinstance(install_config, dict) else "/"),
                )
        if registry is not None:
            version = body.get("version") or (getattr(manifest, "version", "") if manifest else "")
            registry.mark_installed(app_id, version)
        return JSONResponse({"ok": True, "app_id": app_id, "status": "installed", **inst_result})

    # docker / pip — actually run the installer.
    # Previously this branch fell straight through to the installed-apps
    # store, which only marks the app installed in the registry without
    # running anything. That left services like open-webui / perplexica /
    # anything-llm in a "stale" state forever (filed as #410). Now we
    # invoke DockerInstaller / PipInstaller and only mark installed when
    # the underlying tool succeeds.
    if backend in ("docker", "pip"):
        # Apps_dir defaults to data_dir/apps so an unprivileged install
        # works without /opt write access. Tests can override via
        # app.state.apps_dir on a tmp path.
        apps_dir = getattr(request.app.state, "apps_dir", None)
        if apps_dir is None:
            data_dir = getattr(request.app.state, "data_dir", None)
            if data_dir is not None:
                from pathlib import Path as _Path
                apps_dir = _Path(data_dir) / "apps"
        try:
            from tinyagentos.installers.docker_installer import DockerInstaller
            from tinyagentos.installers.pip_installer import PipInstaller
            installer = (
                DockerInstaller(apps_dir=apps_dir) if backend == "docker"
                else PipInstaller(apps_dir=apps_dir)
            )
            inst_result = await installer.install(app_id, install_config)
        except (FileNotFoundError, ImportError) as exc:
            # Binary or installer module missing on this controller.
            return JSONResponse(
                {"error": f"{backend} not available on this controller: {exc}"},
                status_code=500,
            )
        except (OSError, RuntimeError) as exc:
            # Filesystem / process-launch / runtime errors. logger.exception
            # captures the traceback for debugging; the response message is
            # intentionally narrower to avoid leaking internal paths.
            logger.exception("_legacy_install: %s installer raised", backend)
            return JSONResponse({"error": f"{backend} install failed: {exc}"}, status_code=500)
        if not inst_result.get("success"):
            return JSONResponse(
                {"error": inst_result.get("error", f"{backend} install failed")},
                status_code=500,
            )
        # Auto-start docker compose so the service is actually serving
        # on its declared ports. Pip installs don't have a generic start
        # command — those are libraries the user invokes from code.
        if backend == "docker":
            try:
                start_result = await installer.start(app_id)
                if not start_result.get("success"):
                    detail = start_result.get("output", "") or start_result.get("error", "")
                    logger.error(
                        "_legacy_install: docker compose up failed for %s: %s",
                        app_id, detail[:500],
                    )
                    return JSONResponse(
                        {
                            "ok": False,
                            "error": (
                                f"App container failed to start: {detail[:200] or 'unknown error'}. "
                                "Port conflict or image error — check logs."
                            ),
                        },
                        status_code=500,
                    )
            except (FileNotFoundError, OSError, RuntimeError) as exc:
                logger.error(
                    "_legacy_install: docker compose up raised for %s: %s", app_id, exc,
                )
                return JSONResponse(
                    {
                        "ok": False,
                        "error": (
                            f"App container failed to start: {exc}. "
                            "Port conflict or image error — check logs."
                        ),
                    },
                    status_code=500,
                )

    # Default: delegate to InstalledAppsStore (records the install in db / store).
    store = request.app.state.installed_apps
    await store.install(app_id, body.get("version", ""), meta)
    raw_remote = body.get("target_remote") or ""
    _target_remote = raw_remote if raw_remote and raw_remote != "local" else None

    # Docker services publish on an ALLOCATED host port (the DockerInstaller
    # maps {allocated_pool_port}:{container_port} so apps never bind a core port
    # like 8080). Record that allocated host port as the runtime location so the
    # app appears in /api/apps/installed and gets a Launchpad shortcut pointing
    # at the right place. The installer returns it as inst_result["host_port"];
    # use that, never the container port. (Falling back to the manifest's
    # container port only for the no-ports / legacy case.) Without a recorded
    # location a local docker install (e.g. SearxNG) succeeds but never surfaces
    # a shortcut. Remote docker installs resolve the host from the registered
    # incus remote; local installs use 127.0.0.1.
    if backend == "docker":
        docker_port = inst_result.get("host_port") or _docker_published_port(install_config)
        if docker_port:
            runtime_host = (
                await _resolve_host(_target_remote) if _target_remote else "127.0.0.1"
            )
            await store.update_runtime_location(
                app_id, host=runtime_host, port=docker_port, backend="docker",
                ui_path=(install_config.get("ui_path", "/") if isinstance(install_config, dict) else "/"),
            )
        else:
            logger.warning(
                "_legacy_install: docker service %s declares no port; "
                "no runtime location recorded (won't appear in Launchpad).",
                app_id,
            )

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
        # Docker installs already recorded a full host:port location above —
        # don't clobber it with the port=0 placeholder this branch records for
        # backends (e.g. pip) that have no proxy-routable port.
        if backend != "docker":
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


@router.get("/api/store/install-progress/by-app/{app_id}")
async def install_progress_by_app(request: Request, app_id: str):
    """Return the most-recent install progress entry for ``app_id``.

    The Store frontend polls this every 1.5 s after a user clicks
    Install so it can render a download bar / status label without
    needing to track the install_id explicitly.
    """
    from tinyagentos.install_progress import get_global_store
    store = getattr(request.app.state, "install_progress_store", None) or get_global_store()
    entries = store.list_by_app(app_id)
    if not entries:
        return JSONResponse({"app_id": app_id, "active": None})
    return JSONResponse({"app_id": app_id, "active": entries[0].to_dict()})


@router.get("/api/store/install-progress/{install_id}")
async def install_progress_by_id(request: Request, install_id: str):
    """Look up a specific install attempt. Returned by install-v2 in
    the response body so the caller can pin a progress stream to the
    exact attempt rather than 'most recent for app_id'."""
    from tinyagentos.install_progress import get_global_store
    store = getattr(request.app.state, "install_progress_store", None) or get_global_store()
    entry = store.get(install_id)
    if entry is None:
        return JSONResponse({"error": "install_id not found"}, status_code=404)
    return JSONResponse(entry.to_dict())


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

    # Progress store — opened up here so both legacy and v2 paths can
    # tag work-in-flight that the frontend polls for. Importing here
    # avoids a circular import at module load.
    from tinyagentos.install_progress import get_global_store
    progress = getattr(request.app.state, "install_progress_store", None) or get_global_store()
    progress_entry = progress.start(app_id=manifest_id or "(unknown)", target_remote=target_remote)
    install_id = progress_entry.install_id

    registry = getattr(request.app.state, "registry", None)
    manifest = _registry_get(registry, manifest_id) if registry and manifest_id else None
    if manifest is None:
        # Fall through to legacy path if the registry doesn't know this ID —
        # allows callers that don't have a manifest to install via metadata.
        progress.finish(install_id, success=False, error="manifest not found in registry")
        return await _legacy_install(request, body, manifest_id, target_remote)

    # Code-signing gate (#647): verify the catalog manifest's Ed25519
    # signature against the store signing public key.  Re-reads the
    # manifest from disk at install time so post-boot tampering is
    # detected.  Manifests that were catalog-loaded without a signing
    # key configured are allowed through — no stored signature means
    # no tampering evidence.
    #
    # Threat model: this protects against post-boot *catalog* tampering
    # (an attacker modifying a manifest on disk while the server is
    # running).  It does NOT protect against an attacker who can also
    # replace the signing keyfile and then restart the server — that is
    # the OS-level trust boundary.  For defence-in-depth, deploy the
    # keyfile on a read-only filesystem or use a hardware-backed key
    # store.
    _store_pub = getattr(request.app.state, "store_signing_pubkey", None)
    verified, verify_err = _verify_manifest_for_install(
        manifest_id, registry, _store_pub,
    )
    if not verified:
        progress.finish(
            install_id, success=False,
            error=verify_err or "manifest signature verification failed — the catalog may have been tampered with",
        )
        return JSONResponse(
            {
                "error": verify_err or "manifest signature verification failed",
                "detail": "The catalog manifest for this app failed signature verification. "
                          "The app may have been tampered with. Rebuild the catalog or "
                          "reinstall the app from a trusted source.",
                "install_id": install_id,
            },
            status_code=403,
        )

    # TOCTOU guard: the signing gate above verified the signature against
    # the on-disk manifest, but the install below uses the in-memory
    # manifest object loaded at boot.  An attacker who swaps the on-disk
    # file between verification and execution could bypass the gate.
    # Re-read from disk and re-verify the signature — if it no longer
    # verifies, the manifest was modified after the gate check and the
    # install is blocked.
    _manifest_dir = getattr(manifest, "manifest_dir", None)
    if (
        _manifest_dir is not None
        and isinstance(_manifest_dir, Path)
        and _store_pub is not None
        and registry is not None
        and hasattr(registry, "verify_manifest_signature")
    ):
        disk_path = _manifest_dir / "manifest.yaml"
        try:
            import yaml as _yaml
            on_disk = _yaml.safe_load(disk_path.read_text()) if disk_path.exists() else None
        except Exception:
            on_disk = None
        if on_disk is not None:
            # Re-verify the signature against the just-read bytes.
            # This catches any change — not just the narrow set of fields
            # the old comparison whitelisted — and does not false-positive
            # on legitimate catalog reloads (which update signatures too).
            stored_sig = registry.get_signature(manifest_id)
            if stored_sig is not None:
                from tinyagentos.store_signing import verify_manifest_signature as _verify_sig
                if not _verify_sig(on_disk, stored_sig, _store_pub):
                    progress.finish(
                        install_id, success=False,
                        error="manifest modified between signature verification and install",
                    )
                    return JSONResponse(
                        {
                            "error": "manifest modified between signature verification and install",
                            "detail": (
                                "The manifest on disk was modified after the initial "
                                "signature verification. This may indicate post-verification "
                                "tampering. Rebuild the catalog or reinstall the app from "
                                "a trusted source."
                            ),
                            "install_id": install_id,
                        },
                        status_code=403,
                    )

    # Non-commercial weights gate (#169): a manifest's code license (MIT etc.)
    # can be permissive while the model weights it downloads are not (e.g.
    # musicgen pulls Meta's CC-BY-NC 4.0 weights). Block the install until the
    # user has explicitly accepted the weights license -- either previously
    # (recorded in license_acceptances) or on this very request (accepted:
    # true, sent by the frontend after the user clicks Agree in the license
    # dialog). Applies uniformly, including to admins: license acceptance is
    # a legal fact about the user, not a permission an admin role bypasses.
    if getattr(manifest, "license_class", "") == "non-commercial":
        license_store = getattr(request.app.state, "license_acceptances", None)
        license_id = getattr(manifest, "weights_license", "") or manifest.id
        user = _get_current_user(request)
        user_id = (user or {}).get("id") or "anonymous"
        already_accepted = (
            await license_store.has_accepted(user_id, manifest.id, license_id)
            if license_store is not None else False
        )
        accepted_now = bool(body.get("accepted", False))
        if not already_accepted and not accepted_now:
            progress.finish(install_id, success=False, error="needs_license_acceptance")
            return JSONResponse(
                {
                    "needs_license_acceptance": True,
                    "license_id": license_id,
                    "weights_license": manifest.weights_license,
                    "name": manifest.name,
                    "text": (
                        f"{manifest.name} downloads model weights licensed under "
                        f"{manifest.weights_license}, for non-commercial use only. "
                        "Continuing means you agree to that license for the "
                        "weights this service uses."
                    ),
                },
                status_code=412,
            )
        if accepted_now and not already_accepted and license_store is not None:
            await license_store.record_acceptance(user_id, manifest.id, license_id)

    # Non-model manifests use the legacy method-driven path.
    if getattr(manifest, "type", "model") != "model":
        progress.update(install_id, state="unpacking", detail=f"installing {manifest.type}")
        legacy_resp = await _legacy_install(request, body, manifest_id, target_remote)
        # Best-effort: read status code on the response to mark final
        # state. JSONResponse defaults to 200 on success.
        success = getattr(legacy_resp, "status_code", 200) < 400
        progress.finish(install_id, success=success)
        return legacy_resp

    device = await get_device_capability(request, target_remote)
    manifest_dict = {
        "id": manifest.id,
        "type": manifest.type,
        "variants": manifest.variants,
        "context_window": getattr(manifest, "context_window", 0),
    }
    result = resolve(manifest_dict, variant_id, device, force=force)
    if isinstance(result, ResolveErr):
        progress.finish(install_id, success=False, error=result.reason)
        return JSONResponse(
            {
                "error": result.reason,
                "near_miss": result.near_miss,
                "suggestions": result.suggestions,
                "install_id": install_id,
            },
            status_code=422,
        )

    chain: list[dict] = []

    # Install the backend if missing.
    if result.action == "install_chain":
        progress.update(install_id, state="unpacking", detail=f"installing backend {result.backend_id}")
        backend_manifest = _registry_get(registry, result.backend_id)
        if backend_manifest is None:
            progress.finish(install_id, success=False, error=f"backend manifest {result.backend_id!r} not in catalog")
            return JSONResponse(
                {"error": f"backend service manifest {result.backend_id!r} not in catalog", "install_id": install_id},
                status_code=500,
            )
        install_block = getattr(backend_manifest, "install", None) or {}
        backend_method = (
            install_block.get("method") if isinstance(install_block, dict) else None
        )
        if not backend_method:
            progress.finish(install_id, success=False, error=f"backend {result.backend_id!r} has no install.method")
            return JSONResponse(
                {"error": f"backend {result.backend_id!r} has no install.method", "install_id": install_id},
                status_code=500,
            )
        # Verify the backend manifest's signature before running its installer.
        # A backend manifest that was tampered with after catalog load would
        # run an untrusted script/image — reject it here.
        be_verified, be_verify_err = _verify_manifest_for_install(
            result.backend_id, registry, _store_pub,
        )
        if not be_verified:
            progress.finish(
                install_id, success=False,
                error=be_verify_err or "backend manifest signature verification failed",
            )
            return JSONResponse(
                {
                    "error": be_verify_err or "backend manifest signature verification failed",
                    "detail": f"The backend manifest for {result.backend_id!r} failed signature verification.",
                    "install_id": install_id,
                },
                status_code=403,
            )
        backend_installer = get_installer(backend_method)
        be_result = await backend_installer.install(
            result.backend_id,
            install_config=install_block if isinstance(install_block, dict) else {},
        )
        if not be_result.get("success"):
            err = be_result.get("error", "unknown")
            progress.finish(install_id, success=False, error=f"backend install failed: {err}")
            return JSONResponse(
                {
                    "error": (
                        f"backend install failed for {result.backend_id!r}: {err}"
                    ),
                    "chain": chain + [{"step": "backend", "id": result.backend_id, "status": "failed"}],
                    "install_id": install_id,
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
        progress.finish(install_id, success=False, error=f"variant {result.variant_id!r} not in manifest")
        return JSONResponse(
            {"error": f"variant {result.variant_id!r} not found in manifest", "install_id": install_id},
            status_code=500,
        )
    install_method = _BACKEND_TO_METHOD.get(result.backend_id)
    if install_method is None:
        progress.finish(install_id, success=False, error=f"backend {result.backend_id!r} has no installer mapping")
        return JSONResponse(
            {
                "error": (
                    f"backend {result.backend_id!r} has no installer mapping. "
                    "Add an entry to _BACKEND_TO_METHOD in store_install.py."
                ),
                "install_id": install_id,
            },
            status_code=422,
        )
    model_installer = get_installer(install_method)
    install_config = dict(getattr(manifest, "install", None) or {})
    install_config["backend"] = result.backend_id

    # Wire a download-progress callback so the in-flight bytes show
    # up in the install-progress store. Throttled to ~1 update per
    # second by download_file itself.
    def _on_progress(downloaded: int, total: int) -> None:
        progress.update(
            install_id,
            state="downloading",
            bytes_downloaded=downloaded,
            bytes_total=total,
            detail=(f"downloading {chosen_variant.get('id','')} from {chosen_variant.get('download_url','')[:60]}…"
                    if downloaded == 0 else None),
        )

    progress.update(install_id, state="downloading", detail=f"downloading {chosen_variant.get('id','')}")
    model_result = await model_installer.install(
        manifest.id,
        install_config=install_config,
        variant=chosen_variant,
        target_remote=target_remote,
        on_progress=_on_progress,
    )
    if not model_result.get("success"):
        err = model_result.get("error", "unknown")
        progress.finish(install_id, success=False, error=f"model install failed: {err}")
        return JSONResponse(
            {
                "error": f"model install failed: {err}",
                "chain": chain + [{"step": "model", "id": manifest.id, "status": "failed"}],
                "install_id": install_id,
            },
            status_code=500,
        )
    if hasattr(registry, "mark_installed"):
        try:
            registry.mark_installed(manifest.id, getattr(manifest, "version", ""))
        except Exception:  # noqa: BLE001
            pass
    chain.append({"step": "model", "id": manifest.id, "status": "installed"})
    progress.finish(install_id, success=True, detail="install complete")

    return JSONResponse({"chain": chain, "compat": classify(manifest_dict, device), "install_id": install_id})


@router.get("/api/store/signing-pubkey")
async def store_signing_pubkey(request: Request):
    """Return the store signing public key (Ed25519, PEM).

    Clients and auditing tools can use this to verify catalog manifest
    signatures independently.  The corresponding private key never leaves
    the node.
    """
    pub_pem = getattr(request.app.state, "store_signing_pubkey", None)
    if pub_pem is None:
        return JSONResponse({"error": "store signing key not configured"}, status_code=404)
    return JSONResponse({"public_key_pem": pub_pem.decode()})


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
