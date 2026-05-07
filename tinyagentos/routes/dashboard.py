from __future__ import annotations

import asyncio
import shutil
import time

import tinyagentos

import psutil
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from tinyagentos.agent_db import get_agent_summaries
from tinyagentos.backend_adapters import check_backend_health
from tinyagentos.first_boot import mark_setup_complete

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def root_redirect(request: Request):
    """Root URL serves the desktop shell directly (skip first-boot setup)."""
    return RedirectResponse(url="/desktop", status_code=303)


@router.post("/setup/complete")
async def setup_complete(request: Request):
    data_dir = request.app.state.config_path.parent
    form = await request.form()
    password = form.get("password", "")
    if password:
        auth_mgr = request.app.state.auth
        if not auth_mgr.is_configured():
            auth_mgr.set_password(password)
    mark_setup_complete(data_dir)
    response = RedirectResponse(url="/", status_code=303)
    # If password was just set, create a session so user is logged in
    if password:
        token = auth_mgr.create_session()
        response.set_cookie("taos_session", token, httponly=True, samesite="lax", max_age=auth_mgr.session_ttl)
    return response


@router.get("/api/capabilities")
async def api_capabilities(request: Request):
    """Return all capabilities with their available/locked status and unlock hints."""
    cap_checker = request.app.state.capabilities
    return {"capabilities": cap_checker.get_all_capabilities()}


@router.get("/api/health")
async def api_health(request: Request, detailed: bool = False):
    """System health check.

    Without query params: a cheap liveness probe suitable for systemd
    or a load balancer. Returns agent and backend counts and a status
    of ``ok`` when the controller is responsive.

    With ``?detailed=true``: a richer status blob used by monitoring
    tools. Adds per-subsystem state (cluster, scheduler, qmd) so an
    operator can tell *what* is wrong, not just *that* something is
    wrong. Each subsystem reports independently; one failing subsystem
    does not drag the whole response to a failure state — callers can
    read each block and decide.
    """
    config = request.app.state.config
    basic = {
        "status": "ok",
        "agents": len(config.agents),
        "backends": len(config.backends),
    }
    if not detailed:
        return basic

    subsystems: dict = {}

    cluster = getattr(request.app.state, "cluster_manager", None) or getattr(
        request.app.state, "cluster", None
    )
    if cluster is not None:
        workers = cluster.get_workers()
        online = [w for w in workers if w.status == "online"]
        subsystems["cluster"] = {
            "status": "ok" if online or not workers else "degraded",
            "workers_total": len(workers),
            "workers_online": len(online),
        }
    else:
        subsystems["cluster"] = {"status": "unavailable"}

    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is not None:
        subsystems["scheduler"] = {
            "status": "ok",
            "backends": len(getattr(scheduler, "backends", []) or []),
        }
    else:
        subsystems["scheduler"] = {"status": "unavailable"}

    qmd = getattr(request.app.state, "qmd_client", None)
    if qmd is not None:
        subsystems["qmd"] = {"status": "ok", "url": getattr(qmd, "url", None)}
    else:
        subsystems["qmd"] = {"status": "unavailable"}

    return {**basic, "subsystems": subsystems}


@router.get("/api/version")
async def get_version():
    """Cheap version probe. Auth-exempt — the frontend uses this to
    detect when a new backend version has shipped after a restart."""
    return {"version": tinyagentos.__version__}


@router.get("/api/system/info")
@router.get("/api/system")
async def api_system(request: Request):
    """Comprehensive system overview -- hardware, resources, platform stats."""
    import psutil
    from dataclasses import asdict

    from tinyagentos.system_stats import get_npu_usage, get_vram_usage

    config = request.app.state.config
    hw = request.app.state.hardware_profile
    registry = request.app.state.registry

    hw_data = asdict(hw)
    hw_data["profile_id"] = hw.profile_id

    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")

    vram_pct, vram_used_mb, vram_total_mb = get_vram_usage(hw.gpu.type)
    npu_pct = get_npu_usage(hw.npu.type)

    return {
        "hardware": hw_data,
        "resources": {
            "cpu_percent": psutil.cpu_percent(),
            "ram_total_mb": mem.total // (1024 * 1024),
            "ram_used_mb": mem.used // (1024 * 1024),
            "ram_percent": mem.percent,
            "disk_total_gb": disk.total // (1024 ** 3),
            "disk_used_gb": disk.used // (1024 ** 3),
            "disk_percent": disk.percent,
            "vram_percent": vram_pct,
            "vram_used_mb": vram_used_mb,
            "vram_total_mb": vram_total_mb,
            "npu_percent": npu_pct,
        },
        "platform": {
            "version": "0.1.0",
            "agents": len(config.agents),
            "backends": len(config.backends),
            "catalog_apps": len(registry.list_available()),
            "installed_apps": (
                request.app.state.installation_state.installed_count()
                if getattr(request.app.state, "installation_state", None)
                else len(registry.list_installed())
            ),
        },
        "cluster": _get_cluster_stats(request),
    }


def _get_cluster_stats(request) -> dict:
    """Aggregate cluster statistics."""
    cluster = getattr(request.app.state, "cluster", None)
    if not cluster:
        return {"workers": 0, "online": 0, "total_vram_mb": 0, "total_ram_mb": 0, "capabilities": []}
    workers = cluster.get_workers()
    online = [w for w in workers if w.status == "online"]
    total_vram = 0
    total_ram = 0
    all_caps = set()
    for w in online:
        hw = w.hardware if isinstance(w.hardware, dict) else {}
        total_ram += hw.get("ram_mb", 0)
        gpu = hw.get("gpu", {})
        if isinstance(gpu, dict):
            total_vram += gpu.get("vram_mb", 0)
        all_caps.update(w.capabilities)
    return {
        "workers": len(workers),
        "online": len(online),
        "total_vram_mb": total_vram,
        "total_ram_mb": total_ram,
        "capabilities": sorted(all_caps),
    }


@router.get("/api/dashboard/cluster-summary")
async def cluster_summary(request: Request):
    """Cluster KPIs for the dashboard."""
    stats = _get_cluster_stats(request)
    return {
        "workers": stats["workers"],
        "online": stats["online"],
        "total_ram_gb": round(stats["total_ram_mb"] / 1024, 1),
        "total_vram_gb": round(stats["total_vram_mb"] / 1024, 1),
        "capabilities": stats["capabilities"],
    }


@router.get("/api/backends")
async def api_backends(request: Request):
    """List all configured backends with live health status and fallback info."""
    config = request.app.state.config
    http_client = request.app.state.http_client
    fallback = request.app.state.fallback
    results = []
    for backend in config.backends:
        result = await check_backend_health(http_client, backend)
        results.append(result)
    primary = fallback.get_primary_backend()
    return {
        "backends": results,
        "primary": primary["name"] if primary else None,
        "fallback_status": fallback.get_status(),
    }


@router.get("/api/dashboard/activity")
async def dashboard_activity(request: Request, limit: int = 15):
    """Recent platform activity as JSON."""
    notif_store = request.app.state.notifications
    return {"events": await notif_store.list(limit=limit)}


@router.get("/api/metrics/{name}")
async def api_metrics(request: Request, name: str, range: str = "24h"):
    """Query time-series metrics by name and time range."""
    metrics = request.app.state.metrics
    now = int(time.time())
    range_map = {"1h": 3600, "24h": 86400, "7d": 604800, "30d": 2592000}
    seconds = range_map.get(range, 86400)
    results = await metrics.query(name, start=now - seconds, end=now)
    return results


# --- Health Debug Page ---

async def _timed_check(name: str, coro) -> dict:
    """Run a health check coroutine and return {name, status, detail, response_ms}."""
    start = time.monotonic()
    try:
        result = await asyncio.wait_for(coro, timeout=10)
        elapsed = int((time.monotonic() - start) * 1000)
        return {"name": name, "status": result.get("status", "ok"), "detail": result.get("detail", ""), "response_ms": elapsed}
    except asyncio.TimeoutError:
        elapsed = int((time.monotonic() - start) * 1000)
        return {"name": name, "status": "timeout", "detail": "Check timed out after 10s", "response_ms": elapsed}
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        return {"name": name, "status": "error", "detail": str(e), "response_ms": elapsed}


async def _check_incusd() -> dict:
    """Check if incusd is running."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "incus", "version",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        if proc.returncode == 0:
            return {"status": "ok", "detail": stdout.decode().strip()}
        return {"status": "error", "detail": f"exit code {proc.returncode}"}
    except FileNotFoundError:
        return {"status": "unavailable", "detail": "incus not installed"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


async def _check_docker() -> dict:
    """Check Docker daemon connectivity."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "info", "--format", "{{.ServerVersion}}",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5)
        if proc.returncode == 0:
            return {"status": "ok", "detail": f"Docker {stdout.decode().strip()}"}
        return {"status": "error", "detail": stderr.decode().strip()[:200]}
    except FileNotFoundError:
        return {"status": "unavailable", "detail": "docker not installed"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@router.get("/api/health-check")
async def api_health_check(request: Request):
    """Run all health checks and return results."""
    config = request.app.state.config
    http_client = request.app.state.http_client
    checks = []

    # 1. TinyAgentOS itself
    checks.append({"name": "TinyAgentOS", "status": "ok", "detail": "Running", "response_ms": 0})

    # 2. incusd
    checks.append(await _timed_check("incusd", _check_incusd()))

    # 3. Docker
    checks.append(await _timed_check("Docker", _check_docker()))

    # 4. Each backend
    for backend in config.backends:
        name = f"Backend: {backend.get('name', backend.get('url', 'unknown'))}"

        async def _check_backend(b=backend):
            result = await check_backend_health(http_client, b)
            return {"status": result["status"], "detail": f"{result.get('response_ms', 0)}ms, {len(result.get('models', []))} models"}

        checks.append(await _timed_check(name, _check_backend()))

    # 5. QMD
    async def _check_qmd():
        qmd = request.app.state.qmd_client
        result = await qmd.health()
        if result.get("status") == "error":
            return {"status": "error", "detail": result.get("error", "unreachable")}
        return {"status": "ok", "detail": f"{result.get('response_ms', 0)}ms"}

    checks.append(await _timed_check("QMD Server", _check_qmd()))

    # Per-agent QMD health checks removed — there is no longer a
    # per-agent QMD. One shared qmd.service on the host serves every
    # agent's embed/rerank/expand traffic, and that single instance is
    # already covered by the "QMD Server" check above. See
    # docs/design/framework-agnostic-runtime.md.

    # 6. Disk space
    disk = shutil.disk_usage("/")
    disk_pct = (disk.used / disk.total) * 100
    disk_status = "ok" if disk_pct < 85 else ("warning" if disk_pct < 95 else "error")
    free_gb = disk.free / (1024 ** 3)
    checks.append({
        "name": "Disk Space",
        "status": disk_status,
        "detail": f"{disk_pct:.1f}% used, {free_gb:.1f} GB free",
        "response_ms": 0,
    })

    # 8. RAM usage
    mem = psutil.virtual_memory()
    ram_status = "ok" if mem.percent < 85 else ("warning" if mem.percent < 95 else "error")
    avail_gb = mem.available / (1024 ** 3)
    checks.append({
        "name": "RAM Usage",
        "status": ram_status,
        "detail": f"{mem.percent:.1f}% used, {avail_gb:.1f} GB available",
        "response_ms": 0,
    })

    # 9. Cluster workers
    cluster = request.app.state.cluster_manager
    for worker in cluster.get_workers():
        age = int(time.time() - worker.last_heartbeat)
        checks.append({
            "name": f"Worker: {worker.name}",
            "status": "ok" if worker.status == "online" else "error",
            "detail": f"{worker.status}, last heartbeat {age}s ago, {worker.platform}",
            "response_ms": 0,
        })

    return {"checks": checks}
