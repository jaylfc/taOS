from __future__ import annotations
import datetime
import io
import tarfile
import time
from pathlib import Path

import yaml
from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from tinyagentos.config import AppConfig, save_config_locked, validate_config

router = APIRouter()


class ConfigUpdate(BaseModel):
    yaml: str


class PlatformUpdate(BaseModel):
    poll_interval: int
    retention_days: int
    catalog_repo: str = ""


def _dir_size(path: Path) -> int:
    """Return total size of a directory in bytes."""
    if not path.exists():
        return 0
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file():
                try:
                    total += entry.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _format_size(size_bytes: int) -> str:
    """Format bytes into a human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 ** 3:
        return f"{size_bytes / (1024 ** 2):.1f} MB"
    else:
        return f"{size_bytes / (1024 ** 3):.2f} GB"


def _get_storage_stats(app) -> list[dict]:
    """Compute storage usage for key directories."""
    data_dir = app.state.config_path.parent
    items = []

    # Models dir
    models_dir = data_dir / "models"
    models_bytes = _dir_size(models_dir)
    items.append({
        "label": "Models",
        "path": str(models_dir),
        "size": _format_size(models_bytes),
        "bytes": models_bytes,
    })

    # Data dir
    data_bytes = _dir_size(data_dir)
    items.append({
        "label": "Data",
        "path": str(data_dir),
        "size": _format_size(data_bytes),
        "bytes": data_bytes,
    })

    # App catalog
    catalog_dir = getattr(app.state, "registry", None)
    if catalog_dir and hasattr(catalog_dir, "catalog_dir"):
        cat_path = catalog_dir.catalog_dir
    else:
        cat_path = data_dir.parent / "app-catalog"
    cat_bytes = _dir_size(cat_path)
    items.append({
        "label": "App Catalog",
        "path": str(cat_path),
        "size": _format_size(cat_bytes),
        "bytes": cat_bytes,
    })

    return items


@router.get("/api/config")
async def get_config(request: Request):
    """Get current configuration as YAML."""
    config = request.app.state.config
    return {"yaml": yaml.dump(config.to_dict(), default_flow_style=False, sort_keys=False)}


@router.put("/api/config")
async def save_config_endpoint(request: Request, body: ConfigUpdate, validate_only: bool = False):
    """Validate and save configuration from YAML."""
    try:
        data = yaml.safe_load(body.yaml)
    except yaml.YAMLError as e:
        return JSONResponse({"error": f"Invalid YAML: {e}"}, status_code=400)
    if not isinstance(data, dict):
        return JSONResponse({"error": "Config must be a YAML mapping"}, status_code=400)
    new_config = AppConfig(
        server=data.get("server", {}),
        backends=data.get("backends", []),
        qmd=data.get("qmd", {}),
        agents=data.get("agents", []),
        metrics=data.get("metrics", {}),
        webhooks=data.get("webhooks", []),
        config_path=request.app.state.config_path,
    )
    errors = validate_config(new_config)
    if errors:
        return JSONResponse({"error": "Validation failed", "details": errors}, status_code=400)
    if validate_only:
        return {"status": "valid", "message": "Config is valid"}
    await save_config_locked(new_config, request.app.state.config_path)
    request.app.state.config = new_config
    return {"status": "saved", "message": "Config saved successfully"}


@router.get("/api/settings/storage")
async def get_storage(request: Request):
    """Return storage usage as JSON."""
    storage = _get_storage_stats(request.app)
    return {"storage": storage}


@router.put("/api/settings/platform")
async def save_platform_settings(request: Request, body: PlatformUpdate):
    """Update platform settings (metrics interval/retention)."""
    config = request.app.state.config
    config.metrics["poll_interval"] = body.poll_interval
    config.metrics["retention_days"] = body.retention_days
    await save_config_locked(config, request.app.state.config_path)
    return {"status": "saved", "message": "Platform settings saved"}


@router.get("/api/settings/llm-proxy")
async def llm_proxy_status(request: Request):
    """Return LLM proxy status for the settings page."""
    proxy = request.app.state.llm_proxy
    return {
        "running": proxy.is_running() if hasattr(proxy, "is_running") else False,
        "port": proxy.port if hasattr(proxy, "port") else 4000,
        "backends": len(request.app.state.config.backends),
    }


@router.post("/api/settings/test-backend")
async def test_backend_connection(request: Request):
    """Test connectivity to a backend URL."""
    from tinyagentos.backend_adapters import get_adapter
    body = await request.json()
    url = body.get("url", "")
    backend_type = body.get("type", "rkllama")
    if not url:
        return JSONResponse({"error": "URL required"}, status_code=400)
    try:
        adapter = get_adapter(backend_type)
        http_client = request.app.state.http_client
        result = await adapter.health(http_client, url)
        return {"reachable": result["status"] == "ok", "response_ms": result.get("response_ms", 0), "models": result.get("models", [])}
    except Exception as e:
        return {"reachable": False, "error": str(e)}


@router.post("/api/backup")
async def create_backup(request: Request):
    """Create a downloadable backup of configuration and app data."""
    data_dir = request.app.state.config_path.parent
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in ["config.yaml", "installed.json", "hardware.json"]:
            path = data_dir / name
            if path.exists():
                tar.add(str(path), arcname=f"backup/{name}")
        catalog_dir = data_dir.parent / "app-catalog"
        if catalog_dir.exists():
            tar.add(str(catalog_dir), arcname="backup/app-catalog")
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/gzip",
        headers={"Content-Disposition": f"attachment; filename=tinyagentos-backup-{int(time.time())}.tar.gz"},
    )


@router.post("/api/restore")
async def restore_backup(request: Request, file: UploadFile):
    """Restore configuration from a backup tarball."""
    data_dir = request.app.state.config_path.parent
    content = await file.read()
    buf = io.BytesIO(content)
    try:
        with tarfile.open(fileobj=buf, mode="r:gz") as tar:
            for member in tar.getmembers():
                # Strip the leading "backup/" prefix when extracting
                if not member.name.startswith("backup/"):
                    continue
                relative = member.name[len("backup/"):]
                if not relative:
                    continue
                rel_path = Path(relative)
                if rel_path.is_absolute() or ".." in rel_path.parts:
                    continue
                dest = data_dir / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                f = tar.extractfile(member)
                if f is not None:
                    dest.write_bytes(f.read())
    except tarfile.TarError as e:
        return JSONResponse({"error": f"Invalid backup file: {e}"}, status_code=400)
    # Reload config if config.yaml was restored
    config_path = request.app.state.config_path
    if config_path.exists():
        try:
            with open(config_path) as fh:
                data = yaml.safe_load(fh) or {}
            new_config = AppConfig(
                server=data.get("server", {}),
                backends=data.get("backends", []),
                qmd=data.get("qmd", {}),
                agents=data.get("agents", []),
                metrics=data.get("metrics", {}),
                webhooks=data.get("webhooks", []),
                config_path=config_path,
            )
            request.app.state.config = new_config
        except Exception:
            pass
    return {"status": "restored", "message": "Backup restored successfully"}


class BackupSchedule(BaseModel):
    frequency: str  # "off", "daily", "weekly"


BACKUP_SCHEDULES = {
    "daily": "0 3 * * *",
    "weekly": "0 3 * * 0",
}


@router.get("/api/settings/backup-schedule")
async def get_backup_schedule(request: Request):
    """Get current backup schedule."""
    scheduler = request.app.state.scheduler
    tasks = await scheduler.list_tasks()
    backup_task = next((t for t in tasks if t.get("name") == "auto-backup"), None)
    if backup_task:
        # Reverse-lookup frequency from cron
        cron = backup_task.get("schedule", "")
        freq = "custom"
        for name, sched in BACKUP_SCHEDULES.items():
            if sched == cron:
                freq = name
                break
        return {"frequency": freq, "schedule": cron, "task_id": backup_task.get("id")}
    return {"frequency": "off", "schedule": None, "task_id": None}


@router.put("/api/settings/backup-schedule")
async def set_backup_schedule(request: Request, body: BackupSchedule):
    """Set or disable automatic backup schedule."""
    scheduler = request.app.state.scheduler

    # Remove existing backup task if any
    tasks = await scheduler.list_tasks()
    for task in tasks:
        if task.get("name") == "auto-backup":
            await scheduler.delete_task(task["id"])

    if body.frequency == "off":
        return {"status": "disabled", "frequency": "off"}

    cron = BACKUP_SCHEDULES.get(body.frequency)
    if not cron:
        return JSONResponse({"error": f"Invalid frequency: {body.frequency}"}, status_code=400)

    await scheduler.add_task(
        name="auto-backup",
        schedule=cron,
        command="create_backup",
        agent_name=None,
    )
    return {"status": "enabled", "frequency": body.frequency, "schedule": cron}


class WebhookAdd(BaseModel):
    url: str
    type: str = "generic"
    bot_token: str = ""
    chat_id: str = ""


@router.get("/api/settings/webhooks")
async def get_webhooks(request: Request):
    """Return configured webhooks."""
    config = request.app.state.config
    webhooks = config.webhooks if hasattr(config, "webhooks") else []
    return {"webhooks": webhooks}


@router.post("/api/settings/webhooks")
async def add_webhook(request: Request, body: WebhookAdd):
    """Add a webhook endpoint."""
    config = request.app.state.config
    if not hasattr(config, "webhooks"):
        config.webhooks = []
    wh = {"url": body.url, "type": body.type}
    if body.bot_token:
        wh["bot_token"] = body.bot_token
    if body.chat_id:
        wh["chat_id"] = body.chat_id
    config.webhooks.append(wh)
    await save_config_locked(config, request.app.state.config_path)
    # Update the notifier with new config
    from tinyagentos.webhook_notifier import WebhookNotifier
    notifier = WebhookNotifier(config.to_dict())
    request.app.state.webhook_notifier = notifier
    request.app.state.notifications.set_webhook_notifier(notifier)
    return {"status": "added", "webhooks": config.webhooks}


@router.delete("/api/settings/webhooks/{index}")
async def remove_webhook(request: Request, index: int):
    """Remove a webhook by index."""
    config = request.app.state.config
    webhooks = config.webhooks if hasattr(config, "webhooks") else []
    if index < 0 or index >= len(webhooks):
        return JSONResponse({"error": "Invalid webhook index"}, status_code=400)
    webhooks.pop(index)
    config.webhooks = webhooks
    await save_config_locked(config, request.app.state.config_path)
    from tinyagentos.webhook_notifier import WebhookNotifier
    notifier = WebhookNotifier(config.to_dict())
    request.app.state.webhook_notifier = notifier
    request.app.state.notifications.set_webhook_notifier(notifier)
    return {"status": "removed", "webhooks": config.webhooks}


@router.post("/api/settings/webhooks/test")
async def test_webhook(request: Request):
    """Send a test notification to a webhook URL."""
    body = await request.json()
    url = body.get("url", "")
    wh_type = body.get("type", "generic")
    if not url:
        return JSONResponse({"error": "URL required"}, status_code=400)
    from tinyagentos.webhook_notifier import WebhookNotifier
    test_wh = {"url": url, "type": wh_type}
    if body.get("bot_token"):
        test_wh["bot_token"] = body["bot_token"]
    if body.get("chat_id"):
        test_wh["chat_id"] = body["chat_id"]
    notifier = WebhookNotifier({"webhooks": [test_wh]})
    try:
        await notifier.notify("TinyAgentOS Test", "This is a test notification from TinyAgentOS.", "info")
        return {"status": "sent", "message": "Test notification sent"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/api/settings/notification-prefs")
async def get_notification_prefs(request: Request):
    """Return notification event preferences."""
    notif_store = request.app.state.notifications
    prefs = await notif_store.get_event_prefs()
    return {"prefs": prefs}


@router.post("/api/settings/notification-prefs/{event_type}")
async def toggle_notification_pref(request: Request, event_type: str):
    """Toggle mute for a notification event type."""
    body = await request.json()
    muted = body.get("muted", False)
    notif_store = request.app.state.notifications
    await notif_store.set_event_muted(event_type, muted)
    return {"status": "updated", "event_type": event_type, "muted": muted}


@router.get("/api/settings/container-runtime")
async def get_container_runtime(request: Request):
    """Return container runtime status."""
    from tinyagentos.containers.backend import detect_runtime, get_backend
    try:
        backend = get_backend()
        active = backend.__class__.__name__.replace("Backend", "").lower()
    except RuntimeError:
        active = "none"
    detected = detect_runtime()
    configured = getattr(request.app.state.config, "container_runtime", "auto")
    return {"active": active, "detected": detected, "configured": configured}


@router.put("/api/settings/container-runtime")
async def set_container_runtime(request: Request):
    """Set the container runtime preference."""
    body = await request.json()
    runtime = body.get("runtime", "auto")
    if runtime not in ("auto", "apple", "lxc", "docker", "podman"):
        return JSONResponse({"error": f"Invalid runtime: {runtime}"}, status_code=400)
    config = request.app.state.config
    config.container_runtime = runtime
    await save_config_locked(config, request.app.state.config_path)
    # Apply immediately
    from tinyagentos.containers.backend import detect_runtime, set_backend
    from tinyagentos.containers.lxc import LXCBackend
    from tinyagentos.containers.docker import DockerBackend
    effective = runtime
    if runtime == "auto":
        effective = detect_runtime()
    if effective == "apple":
        from tinyagentos.containers.apple_backend import AppleContainerBackend
        set_backend(AppleContainerBackend())
    elif effective == "lxc":
        set_backend(LXCBackend())
    elif effective in ("docker", "podman"):
        set_backend(DockerBackend(binary=effective))
    return {"status": "updated", "runtime": effective}


@router.get("/api/settings/update-check")
async def check_for_updates(request: Request):
    """Check if a newer version of TinyAgentOS is available on GitHub."""
    import asyncio
    project_dir = str(Path(__file__).parent.parent.parent)

    # Fetch remote refs so origin/master is current, then compare SHAs.
    # Parsing dry-run output is unreliable on shallow clones / no tracking branch.
    fetch_proc = await asyncio.create_subprocess_exec(
        "git", "fetch", "origin", "master",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        cwd=project_dir,
    )
    await fetch_proc.communicate()

    async def _rev_parse(ref: str) -> str:
        p = await asyncio.create_subprocess_exec(
            "git", "rev-parse", ref,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            cwd=project_dir,
        )
        out, _ = await p.communicate()
        return out.decode().strip() if out else ""

    local_sha, remote_sha = await asyncio.gather(
        _rev_parse("HEAD"),
        _rev_parse("origin/master"),
    )
    has_updates = bool(local_sha and remote_sha and local_sha != remote_sha)

    async def _log1(ref: str) -> str:
        p = await asyncio.create_subprocess_exec(
            "git", "log", "-1", "--format=%h %s", ref,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            cwd=project_dir,
        )
        out, _ = await p.communicate()
        return out.decode().strip() if out else "unknown"

    current = await _log1("HEAD")
    new_commit = await _log1("origin/master") if has_updates else None

    return {
        "has_updates": has_updates,
        "current_version": "0.1.0",
        "current_commit": current,
        "new_commit": new_commit,
    }


@router.get("/api/settings/update-status")
async def update_status(request: Request):
    """Return current SHA, pending restart SHA, and auto-update prefs."""
    import asyncio
    from tinyagentos.restart_orchestrator import read_pending_restart
    from tinyagentos.auto_update import PREF_NAMESPACE, DEFAULT_PREFS

    project_dir = Path(__file__).parent.parent.parent

    proc = await asyncio.create_subprocess_exec(
        "git", "rev-parse", "HEAD",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        cwd=str(project_dir),
    )
    stdout, _ = await proc.communicate()
    current_sha = stdout.decode().strip() if stdout else ""

    pending = read_pending_restart()
    pending_sha = pending.get("target_sha") if pending else None

    settings = getattr(request.app.state, "desktop_settings", None)
    prefs = dict(DEFAULT_PREFS)
    if settings:
        try:
            saved = await settings.get_preference("user", PREF_NAMESPACE)
            if saved:
                prefs.update(saved)
        except Exception:
            pass

    return {
        "current_sha": current_sha,
        "pending_restart_sha": pending_sha,
        "auto_check": prefs.get("check_enabled", True),
        "auto_apply": prefs.get("auto_apply", False),
        "auto_restart": prefs.get("auto_restart", False),
    }


@router.post("/api/settings/update-check-now")
async def force_update_check(request: Request):
    """Run the auto-updater now. Honours user auto_apply pref."""
    updater = getattr(request.app.state, "auto_updater", None)
    if updater is None:
        return JSONResponse({"error": "auto-updater not running"}, status_code=503)
    try:
        await updater._run_once()
        return {"status": "checked"}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)



@router.post("/api/settings/update")
async def apply_update(request: Request):
    """Pull latest TinyAgentOS code from GitHub."""
    import asyncio
    from tinyagentos.restart_orchestrator import write_pending_restart
    project_dir = Path(__file__).parent.parent.parent

    # Git pull
    proc = await asyncio.create_subprocess_exec(
        "git", "pull", "--ff-only", "origin", "master",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        cwd=str(project_dir),
    )
    stdout, _ = await proc.communicate()
    output = stdout.decode() if stdout else ""

    if proc.returncode != 0:
        return JSONResponse({"error": f"Update failed: {output}"}, status_code=500)

    # Pip install to pick up new deps — prefer .venv then venv, fall back to system pip
    for candidate in (project_dir / ".venv" / "bin" / "pip", project_dir / "venv" / "bin" / "pip"):
        if candidate.exists():
            pip_cmd = str(candidate)
            break
    else:
        pip_cmd = "pip"
    proc2 = await asyncio.create_subprocess_exec(
        pip_cmd, "install", "-e", ".", "-q",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        cwd=str(project_dir),
    )
    await proc2.communicate()

    # Rebuild the desktop bundle if new source files landed (belt-and-braces on
    # non-systemd hosts where ExecStartPre doesn't run: Mac .app, Docker, dev).
    from tinyagentos.desktop_rebuild import rebuild_desktop_bundle_if_stale
    _rebuilt, _rebuild_msg = await rebuild_desktop_bundle_if_stale(project_dir)
    if _rebuilt:
        logger.info("Desktop rebuild: %s", _rebuild_msg)

    # Record the new SHA so the restart modal can confirm the update was applied
    # and the Updates section can show the pending-restart banner.
    sha_proc = await asyncio.create_subprocess_exec(
        "git", "rev-parse", "HEAD",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        cwd=str(project_dir),
    )
    sha_out, _ = await sha_proc.communicate()
    new_sha = sha_out.decode().strip() if sha_out else ""
    if new_sha:
        write_pending_restart(new_sha)

    # Check if the user wants auto-restart after update
    auto_restart = False
    settings_store = getattr(request.app.state, "desktop_settings", None)
    if settings_store:
        try:
            from tinyagentos.auto_update import PREF_NAMESPACE as _PREF_NS
            saved_prefs = await settings_store.get_preference("user", _PREF_NS)
            if saved_prefs:
                auto_restart = bool(saved_prefs.get("auto_restart", False))
        except Exception:
            pass

    if auto_restart:
        import asyncio as _asyncio
        from tinyagentos.routes.system import _do_restart
        _asyncio.create_task(_do_restart(request.app.state))
        return {
            "status": "restarting",
            "output": output.strip(),
            "message": "Update applied. Restarting now…",
        }

    if hasattr(request.app.state, "notifications") and request.app.state.notifications:
        await request.app.state.notifications.add(
            "System updated",
            f"TinyAgentOS updated successfully. Restart to apply changes.\n{output.strip()}",
            level="info", source="system",
        )

    return {
        "status": "updated",
        "output": output.strip(),
        "message": "Update pulled. Restart TinyAgentOS to apply changes.",
    }
