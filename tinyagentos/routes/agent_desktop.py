from __future__ import annotations

import logging
import secrets
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from tinyagentos.auth_context import CurrentUser, current_user

logger = logging.getLogger(__name__)
router = APIRouter()

_DESKTOP_STATES = ("not_installed", "installed", "starting", "running", "stopping", "stopped", "error")


def _get_desktop_store(request: Request) -> dict[str, dict[str, Any]]:
    store = getattr(request.app.state, "agent_desktops", None)
    if store is None:
        store = {}
        request.app.state.agent_desktops = store
    return store


def _desktop_state(request: Request, agent_name: str) -> dict[str, Any]:
    store = _get_desktop_store(request)
    return store.setdefault(agent_name, {"state": "not_installed"})


def _container_name(agent_name: str) -> str:
    return f"taos-agent-{agent_name}"


@router.post("/api/agents/{agent_name}/desktop/install")
async def install_desktop(request: Request, agent_name: str, user: CurrentUser = Depends(current_user)):
    """Install XFCE + x11vnc into the agent container on demand.

    This mutates the container rootfs by installing packages. It is idempotent:
    a second call returns success without re-running apt.
    """
    from tinyagentos.containers import exec_in_container

    state = _desktop_state(request, agent_name)
    current = state["state"]

    if current == "running":
        return JSONResponse({"error": "desktop is running; stop it before reinstalling"}, status_code=409)
    if current == "starting":
        return JSONResponse({"error": "desktop is starting; wait or stop it first"}, status_code=409)

    container = _container_name(agent_name)

    if current == "not_installed":
        code, output = await exec_in_container(
            container,
            ["bash", "-c", "apt-get update -qq && apt-get install -y -qq xfce4 xfce4-goodies x11vnc xdotool dbus-x11"],
            timeout=300,
        )
        if code != 0:
            state["state"] = "error"
            state["last_error"] = output
            return JSONResponse({"error": f"desktop install failed: {output}"}, status_code=500)
        state["state"] = "installed"
        state.pop("last_error", None)

    return JSONResponse({"agent_name": agent_name, "state": state["state"]})


@router.post("/api/agents/{agent_name}/desktop/start")
async def start_desktop(request: Request, agent_name: str, user: CurrentUser = Depends(current_user)):
    """Start the XFCE desktop session inside the agent container."""
    from tinyagentos.containers import exec_in_container

    state = _desktop_state(request, agent_name)

    if state["state"] == "running":
        return JSONResponse({"agent_name": agent_name, "state": "running"})

    if state["state"] not in ("installed", "stopped", "error"):
        return JSONResponse({"error": f"cannot start desktop in state '{state['state']}'"}, status_code=409)

    container = _container_name(agent_name)
    state["state"] = "starting"

    # Generate a random VNC password per start call (no hardcoded 'testpass')
    password = secrets.token_urlsafe(12)

    setup_rc, setup_out = await exec_in_container(
        container,
        ["bash", "-c", f"mkdir -p ~/.vnc && printf '{password}' | vncpasswd -f > ~/.vnc/passwd && chmod 600 ~/.vnc/passwd"],
        timeout=30,
    )
    if setup_rc != 0:
        state["state"] = "error"
        state["last_error"] = setup_out
        return JSONResponse({"error": f"vnc setup failed: {setup_out}"}, status_code=500)

    # Background Xvfb, dbus, and x11vnc, then poll until x11vnc is alive listening on :1
    start_rc, start_out = await exec_in_container(
        container,
        ["bash", "-c",
         "Xvfb :1 -screen 0 1024x768x16 & "
         "dbus-launch --exit-with-session startxfce4 & "
         "x11vnc -display :1 -rfbport 5900 -forever -shared -passwdfile ~/.vnc/passwd & "
         "for i in $(seq 1 30); do "
         "   if pgrep -f x11vnc > /dev/null; then "
         "       echo READY; exit 0; "
         "   fi; "
         "   sleep 1; "
         "done; "
         "echo TIMEOUT; exit 1"],
        timeout=60,
    )

    if "TIMEOUT" in start_out or start_rc != 0:
        state["state"] = "error"
        state["last_error"] = start_out
        return JSONResponse({"error": f"desktop start failed: {start_out}"}, status_code=500)

    state["state"] = "running"
    state.pop("last_error", None)
    return JSONResponse({"agent_name": agent_name, "state": "running", "vnc_password": password})


@router.post("/api/agents/{agent_name}/desktop/stop")
async def stop_desktop(request: Request, agent_name: str, user: CurrentUser = Depends(current_user)):
    """Stop the running XFCE desktop session inside the agent container."""
    from tinyagentos.containers import exec_in_container

    state = _desktop_state(request, agent_name)

    if state["state"] not in ("running", "starting"):
        return JSONResponse({"agent_name": agent_name, "state": state["state"]})

    container = _container_name(agent_name)
    state["state"] = "stopping"

    stop_rc, stop_out = await exec_in_container(
        container,
        ["bash", "-c", "pkill -f x11vnc || true; pkill -f Xvfb || true; pkill -f xfce4-session || true; echo OK"],
        timeout=30,
    )

    state["state"] = "stopped"
    state.pop("last_error", None)
    return JSONResponse({"agent_name": agent_name, "state": "stopped"})


@router.get("/api/agents/{agent_name}/desktop/status")
async def desktop_status(request: Request, agent_name: str, user: CurrentUser = Depends(current_user)):
    """Report whether a desktop is installed and running for the agent."""
    from tinyagentos.containers import exec_in_container

    state = _desktop_state(request, agent_name)
    current = state["state"]

    if current not in ("running", "starting"):
        return JSONResponse({
            "agent_name": agent_name,
            "state": current,
            "running": False,
        })

    container = _container_name(agent_name)
    code, output = await exec_in_container(
        container,
        ["bash", "-c", "pgrep -f x11vnc > /dev/null && echo RUNNING || echo STOPPED"],
        timeout=10,
    )
    probe = "running" if code == 0 and "RUNNING" in output else "stopped"

    if probe == "stopped":
        state["state"] = "stopped"
        return JSONResponse({
            "agent_name": agent_name,
            "state": "stopped",
            "running": False,
        })

    return JSONResponse({
        "agent_name": agent_name,
        "state": "running",
        "running": True,
    })
