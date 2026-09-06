"""HTTP routes for incus cross-host container migration and remote management."""
from __future__ import annotations

import logging
from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.containers import migrate_container, remote_add, remote_generate_token, remote_list, remote_remove
from tinyagentos.cluster.service_migrator import migrate_service

logger = logging.getLogger(__name__)

router = APIRouter()


def _install_dict(manifest) -> dict:
    """Return manifest.install normalised to a dict.

    AppManifest.install is typed as ``dict`` but older catalog entries, broken
    YAML, or future manifest shapes could surface it as None, a list, or an
    object with ``.get`` but no mapping semantics. Calling ``.get(...)`` on
    those blows up with AttributeError — callers should go through this helper
    instead of touching ``manifest.install`` directly.
    """
    install = getattr(manifest, "install", None)
    if isinstance(install, dict):
        return install
    return {}


class RemoteAddBody(BaseModel):
    name: str
    url: str
    token: str


class GenerateTokenBody(BaseModel):
    client_name: str
    projects: list[str] | None = None
    restricted: bool = False


class MigrateBody(BaseModel):
    container: str
    target_remote: str
    new_name: str | None = None
    keep_source: bool = False
    stateless: bool = True
    timeout: int = 600


class MigrateServiceBody(BaseModel):
    app_id: str
    target_remote: str
    keep_source: bool = False
    source_remote: str | None = None


@router.post("/api/cluster/remotes")
async def add_remote(body: RemoteAddBody):
    """Register an incus remote host using a one-time token."""
    if not body.name or not body.url:
        return JSONResponse({"error": "name and url are required"}, status_code=400)
    if not body.token:
        return JSONResponse({"error": "token is required"}, status_code=400)
    result = await remote_add(body.name, body.url, token=body.token)
    if not result["success"]:
        return JSONResponse({"error": result["output"]}, status_code=500)
    return {"status": "registered", "name": body.name}


@router.post("/api/cluster/remotes/token")
async def generate_token(body: GenerateTokenBody):
    """Generate a one-time enrollment token on this host for a remote client."""
    if not body.client_name:
        return JSONResponse({"error": "client_name is required"}, status_code=400)
    result = await remote_generate_token(
        body.client_name,
        projects=body.projects,
        restricted=body.restricted,
    )
    if not result["success"]:
        return JSONResponse({"error": result["output"]}, status_code=500)
    return {"token": result["token"]}


@router.get("/api/cluster/remotes")
async def list_remotes():
    """List registered incus remotes."""
    return await remote_list()


@router.delete("/api/cluster/remotes/{name}")
async def remove_remote(name: str):
    """Remove a registered incus remote."""
    result = await remote_remove(name)
    if not result["success"]:
        return JSONResponse({"error": result["output"]}, status_code=500)
    return {"status": "removed", "name": name}


@router.post("/api/cluster/migrate")
async def migrate(body: MigrateBody):
    """Move or copy a container to a remote incus host."""
    if not body.container or not body.target_remote:
        return JSONResponse(
            {"error": "container and target_remote are required"}, status_code=400
        )
    result = await migrate_container(
        body.container,
        body.target_remote,
        new_name=body.new_name,
        keep_source=body.keep_source,
        stateless=body.stateless,
        timeout=body.timeout,
    )
    if not result["success"]:
        return JSONResponse({"error": result["error"]}, status_code=500)
    return result


@router.post("/api/cluster/migrate-service")
async def migrate_service_route(request: Request, body: MigrateServiceBody):
    """Arch-portable service migration: deploy fresh on target, restore state paths."""
    if not body.app_id or not body.target_remote:
        return JSONResponse(
            {"error": "app_id and target_remote are required"}, status_code=400
        )

    registry = request.app.state.registry
    manifest = registry.get(body.app_id)
    if not manifest:
        return JSONResponse(
            {"error": f"App '{body.app_id}' not found in catalog"}, status_code=404
        )

    install = _install_dict(manifest)
    state_paths: list[str] = install.get("state_paths", [])
    if not state_paths:
        # Fall back to top-level manifest field for manifests that declare it there.
        import yaml as _yaml
        if manifest.manifest_dir is not None:
            lxc_manifest_path = manifest.manifest_dir / "manifest-lxc.yaml"
            if lxc_manifest_path.exists():
                _data = _yaml.safe_load(lxc_manifest_path.read_text()) or {}
                state_paths = _data.get("state_paths", [])

    if not state_paths:
        return JSONResponse(
            {"error": f"App '{body.app_id}' has no state_paths defined in its manifest"},
            status_code=400,
        )

    service_name: str = install.get("service_name", "")
    if not service_name and manifest.manifest_dir is not None:
        import yaml as _yaml
        lxc_manifest_path = manifest.manifest_dir / "manifest-lxc.yaml"
        if lxc_manifest_path.exists():
            _data = _yaml.safe_load(lxc_manifest_path.read_text()) or {}
            service_name = _data.get("service_name", body.app_id)

    if not service_name:
        service_name = body.app_id

    try:
        result = await migrate_service(
            body.app_id,
            body.target_remote,
            install_config=install,
            state_paths=state_paths,
            service_name=service_name,
            keep_source=body.keep_source,
            source_remote=body.source_remote,
        )
    except Exception:
        logger.exception(
            "migrate-service failed for app_id=%s target_remote=%s",
            body.app_id,
            body.target_remote,
        )
        return JSONResponse({"error": "service migration failed"}, status_code=500)

    if not result["success"]:
        return JSONResponse({"error": result["error"]}, status_code=500)

    # Update the installed-apps registry with the new runtime location.
    host_port = result.get("host_port")
    target_remote_norm = result.get("target_remote")
    if host_port:
        installed_apps = getattr(request.app.state, "installed_apps", None)
        if installed_apps is not None:
            runtime_host = await _resolve_host(target_remote_norm)
            if runtime_host is None:
                logger.warning(
                    "migrate-service: unresolved runtime host for remote %r; "
                    "skipping runtime_location update",
                    target_remote_norm,
                )
                return result
            ui_path = _install_dict(manifest).get("ui_path", "/")
            try:
                await installed_apps.update_runtime_location(
                    body.app_id,
                    host=runtime_host,
                    port=host_port,
                    backend="lxc",
                    ui_path=ui_path,
                )
            except Exception:
                logger.warning(
                    "migrate-service: failed to update runtime location for %s", body.app_id
                )

    return result


async def _resolve_host(target_remote: str | None) -> str | None:
    """Parse the hostname from a registered incus remote's URL.

    Falls back to '127.0.0.1' for local (no remote) targets.
    """
    if not target_remote:
        return "127.0.0.1"
    try:
        remotes = await remote_list()
        for r in remotes:
            if r.get("name") == target_remote:
                addr = r.get("addr", "")
                parsed = urlparse(addr)
                if parsed.hostname:
                    return parsed.hostname
    except Exception:
        logger.warning("_resolve_host: failed to look up remote %r", target_remote)
    return None
