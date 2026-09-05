from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import secrets
import tempfile
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from tinyagentos.auth_context import CurrentUser, current_user, require_owner_or_admin

logger = logging.getLogger(__name__)
router = APIRouter()

_DESKTOP_STATES = ("not_installed", "installed", "starting", "running", "stopping", "stopped", "error")

# ``agent_name`` is concatenated into an incus instance name and handed to
# ``incus exec`` as an argv element. Instance names are limited to letters,
# digits, hyphens and underscores, so anything else is not a name we could ever
# have created -- reject it at the door rather than deriving a container name
# from it. The 63-character bound matches the slug pattern the project and
# element routes already use, so a handle the registry accepted at
# registration is not refused here.
_AGENT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$")


def _get_desktop_store(request: Request) -> dict[str, dict[str, Any]]:
    store = getattr(request.app.state, "agent_desktops", None)
    if store is None:
        store = {}
        request.app.state.agent_desktops = store
    return store


def _desktop_state(request: Request, agent_name: str) -> dict[str, Any]:
    store = _get_desktop_store(request)
    return store.setdefault(agent_name, {"state": "not_installed", "installed": False})


def _agent_lock(request: Request, agent_name: str) -> asyncio.Lock:
    """Return the per-agent lifecycle lock, creating it on first use.

    Every handler holds this for the whole of its read-modify-await-write
    sequence, so two requests for one agent can never interleave an exec with
    another request's state transition: no double ``apt-get``, and no stop that
    finishes while a start is still launching processes behind it.
    """
    locks = getattr(request.app.state, "agent_desktop_locks", None)
    if locks is None:
        locks = {}
        request.app.state.agent_desktop_locks = locks
    lock = locks.get(agent_name)
    if lock is None:
        lock = asyncio.Lock()
        locks[agent_name] = lock
    return lock


def _container_name(agent_name: str) -> str:
    return f"taos-agent-{agent_name}"


def _validate_agent_name(agent_name: str) -> None:
    if not _AGENT_NAME_RE.match(agent_name):
        raise HTTPException(status_code=400, detail="invalid agent name")


async def _authorize(request: Request, user: CurrentUser, agent_name: str) -> None:
    """Reject callers who neither own *agent_name* nor administer the host.

    The registry row is the authoritative record of who owns an agent, so it
    decides. When no such row can be read -- the store is unavailable, or the
    name belongs to no registered agent -- there is no owner to compare the
    caller against, and the request is refused for everyone but an
    administrator. Authenticating is not authorising: without this, any signed-in
    user could install packages in, start, stop, or read the VNC password of
    somebody else's agent.
    """
    registry = getattr(request.app.state, "agent_registry", None)
    agent = None
    if registry is not None:
        try:
            agent = await registry.get_by_handle(agent_name)
        except RuntimeError as exc:
            # The store is uninitialised or its connection dropped, so no row
            # can be read. The outcome is deliberately the same as for a name
            # with no row: administrators are already authorised for every
            # agent whatever the registry says, and everyone else is refused.
            # The degraded mode is therefore a strict subset of the healthy
            # one -- it can only take access away, never grant it.
            logger.warning("agent registry unreadable for desktop authz: %s", exc)
            agent = None
    if agent is None:
        if not user.is_admin:
            raise HTTPException(status_code=403, detail="forbidden")
        return
    require_owner_or_admin(user, agent.get("user_id") or "")


@router.post("/api/agents/{agent_name}/desktop/install")
async def install_desktop(request: Request, agent_name: str, user: CurrentUser = Depends(current_user)):
    """Install XFCE + x11vnc into the agent container on demand.

    This mutates the container rootfs by installing packages. It is idempotent:
    a second call returns success without re-running apt. Only the agent's owner
    or an administrator may call it (403 otherwise).

    A failed install is retryable: completion is tracked on its own ``installed``
    flag rather than inferred from the lifecycle state, so a transient apt error
    leaves the desktop in ``error`` but still un-installed, and the next call
    runs apt again.
    """
    from tinyagentos.containers import exec_in_container

    _validate_agent_name(agent_name)
    await _authorize(request, user, agent_name)

    async with _agent_lock(request, agent_name):
        state = _desktop_state(request, agent_name)
        current = state["state"]

        if current == "running":
            return JSONResponse({"error": "desktop is running; stop it before reinstalling"}, status_code=409)
        if current == "starting":
            return JSONResponse({"error": "desktop is starting; wait or stop it first"}, status_code=409)

        container = _container_name(agent_name)

        if not state.get("installed"):
            code, output = await exec_in_container(
                container,
                ["bash", "-c", "apt-get update -qq && apt-get install -y -qq xfce4 xfce4-goodies x11vnc xdotool dbus-x11"],
                timeout=300,
            )
            if code != 0:
                state["state"] = "error"
                state["last_error"] = output
                return JSONResponse({"error": f"desktop install failed: {output}"}, status_code=500)
            state["installed"] = True
            state["state"] = "installed"
            state.pop("last_error", None)
        elif state["state"] == "error":
            # The packages are already present, so this call did succeed; the
            # error was left behind by a later start or stop. Returning 200
            # with state 'error' would report someone else's failure, so clear
            # it back to the installed baseline that start expects.
            state["state"] = "installed"
            state.pop("last_error", None)

        return JSONResponse({"agent_name": agent_name, "state": state["state"]})


@router.post("/api/agents/{agent_name}/desktop/start")
async def start_desktop(request: Request, agent_name: str, user: CurrentUser = Depends(current_user)):
    """Start the XFCE desktop session inside the agent container.

    Only the agent's owner or an administrator may call it (403 otherwise). The
    response carries the freshly generated VNC password; it is issued once per
    start and is not readable from any other route.
    """
    from tinyagentos.containers import exec_in_container, push_file

    _validate_agent_name(agent_name)
    await _authorize(request, user, agent_name)

    async with _agent_lock(request, agent_name):
        state = _desktop_state(request, agent_name)

        if state["state"] == "running":
            return JSONResponse({"agent_name": agent_name, "state": "running"})

        # An install that never completed leaves a container with no x11vnc in
        # it; starting against that half-built rootfs fails in confusing ways.
        if not state.get("installed"):
            return JSONResponse(
                {"error": f"cannot start desktop in state '{state['state']}'; install it first"},
                status_code=409,
            )

        if state["state"] not in ("installed", "stopped", "error"):
            return JSONResponse({"error": f"cannot start desktop in state '{state['state']}'"}, status_code=409)

        container = _container_name(agent_name)
        state["state"] = "starting"

        # A random VNC password per start call (no hardcoded 'testpass').
        password = secrets.token_urlsafe(12)

        # The password never becomes an argv element. An argv is world-readable
        # from /proc/<pid>/cmdline for as long as the process lives, and this
        # command line would be exposed twice over: once on the host, where
        # ``incus exec`` carries every element, and once inside the container on
        # the ``bash -c`` line (CWE-214). It travels as a mode-600 file instead:
        # written on the host, pushed in, read by vncpasswd from stdin, and
        # unlinked on both sides in the ``finally`` -- including when the push
        # or the setup command fails.
        remote_secret = f"/tmp/.taos-vnc-{secrets.token_hex(8)}"
        fd, host_secret = tempfile.mkstemp(prefix="taos-vnc-")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as handle:
                handle.write(password)
            push_rc, push_out = await push_file(container, host_secret, remote_secret)
            if push_rc != 0:
                setup_rc, setup_out = push_rc, push_out
            else:
                setup_rc, setup_out = await exec_in_container(
                    container,
                    [
                        "bash", "-c",
                        # ``incus file push`` has no mode of its own here, so the
                        # pushed copy is narrowed before it is read. Its name is
                        # unpredictable and it is unlinked straight after, so the
                        # window between the push and this chmod is not one an
                        # in-container process can aim at.
                        "umask 077 && chmod 600 \"$1\" && mkdir -p ~/.vnc "
                        "&& vncpasswd -f < \"$1\" > ~/.vnc/passwd "
                        "&& chmod 600 ~/.vnc/passwd",
                        "taos-desktop",
                        remote_secret,
                    ],
                    timeout=30,
                )
        finally:
            with contextlib.suppress(OSError):
                os.unlink(host_secret)
            rm_rc, rm_out = await exec_in_container(
                container, ["rm", "-f", remote_secret], timeout=30,
            )
            if rm_rc != 0:
                # Never log the secret itself; the path is enough to clean up by
                # hand, and leaving the file behind is a finding of its own.
                logger.error(
                    "could not remove the VNC secret %s from %s: %s",
                    remote_secret, container, rm_out,
                )

        if setup_rc != 0:
            state["state"] = "error"
            state["last_error"] = setup_out
            return JSONResponse({"error": f"vnc setup failed: {setup_out}"}, status_code=500)

        # Launch Xvfb, dbus and x11vnc, then wait for the VNC server to be
        # *reachable*. Matching a process name is not enough: 'pgrep -f x11vnc'
        # also matches the wrapper shell that backgrounded it, so it reports
        # ready before -- or without ever -- binding 5900. The loop instead
        # watches the two PIDs it started and connects to the port, and gives up
        # early with a diagnostic if either process dies.
        start_rc, start_out = await exec_in_container(
            container,
            ["bash", "-c",
             "nohup Xvfb :1 -screen 0 1024x768x16 >/dev/null 2>&1 & "
             "XVFB_PID=$!; "
             "sleep 1; "
             "DISPLAY=:1 nohup dbus-launch --exit-with-session startxfce4 >/dev/null 2>&1 & "
             "XFCE_PID=$!; "
             "nohup x11vnc -display :1 -rfbport 5900 -forever -shared "
             "-passwdfile ~/.vnc/passwd >/dev/null 2>&1 & "
             "VNC_PID=$!; "
             "for i in $(seq 1 45); do "
             "   if ! kill -0 \"$XVFB_PID\" 2>/dev/null; then echo 'FAILED: Xvfb exited'; exit 1; fi; "
             "   if ! kill -0 \"$VNC_PID\" 2>/dev/null; then echo 'FAILED: x11vnc exited'; exit 1; fi; "
             "   if (: < /dev/tcp/127.0.0.1/5900) 2>/dev/null; then echo READY; exit 0; fi; "
             "   sleep 1; "
             "done; "
             "echo TIMEOUT; exit 1"],
            timeout=90,
        )

        if start_rc != 0 or "TIMEOUT" in start_out or "FAILED" in start_out:
            state["state"] = "error"
            state["last_error"] = start_out
            return JSONResponse({"error": f"desktop start failed: {start_out}"}, status_code=500)

        state["state"] = "running"
        state.pop("last_error", None)
        return JSONResponse({"agent_name": agent_name, "state": "running", "vnc_password": password})


@router.post("/api/agents/{agent_name}/desktop/stop")
async def stop_desktop(request: Request, agent_name: str, user: CurrentUser = Depends(current_user)):
    """Stop the running XFCE desktop session inside the agent container.

    Only the agent's owner or an administrator may call it (403 otherwise). A
    stop that could not be executed is reported as a failure, not as a stop.
    """
    from tinyagentos.containers import exec_in_container

    _validate_agent_name(agent_name)
    await _authorize(request, user, agent_name)

    async with _agent_lock(request, agent_name):
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

        # A timeout, a missing container or an unreachable host all surface here
        # as a non-zero rc. The desktop processes are then still running, so
        # recording 'stopped' would hand the caller a success that never
        # happened; keep the error and let 'status' reconcile.
        if stop_rc != 0:
            state["state"] = "error"
            state["last_error"] = stop_out
            return JSONResponse(
                {"agent_name": agent_name, "state": "error", "error": f"desktop stop failed: {stop_out}"},
                status_code=500,
            )

        state["state"] = "stopped"
        state.pop("last_error", None)
        return JSONResponse({"agent_name": agent_name, "state": "stopped"})


@router.get("/api/agents/{agent_name}/desktop/status")
async def desktop_status(request: Request, agent_name: str, user: CurrentUser = Depends(current_user)):
    """Report whether a desktop is installed and running for the agent.

    Only the agent's owner or an administrator may call it (403 otherwise). The
    VNC password is never part of this response.
    """
    from tinyagentos.containers import exec_in_container

    _validate_agent_name(agent_name)
    await _authorize(request, user, agent_name)

    async with _agent_lock(request, agent_name):
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
            ["bash", "-c",
             "pgrep -f 'x11vnc .*-rfbport 5900' > /dev/null "
             "&& (: < /dev/tcp/127.0.0.1/5900) 2>/dev/null "
             "&& echo RUNNING || echo STOPPED"],
            timeout=10,
        )
        # A non-zero rc means the probe never ran -- exec timeout, missing
        # container, unreachable host -- so it says nothing about the desktop.
        # Recording 'stopped' on that would be inventing an observation: the
        # processes keep running, and because start accepts 'stopped', the next
        # start would bind a second Xvfb on :1 and a second x11vnc on 5900.
        # Leave the tracked state alone and report the failure, exactly as
        # stop_desktop separates a failed command from a stopped desktop.
        if code != 0:
            state["last_error"] = output
            return JSONResponse(
                {
                    "agent_name": agent_name,
                    "state": current,
                    "running": False,
                    "error": f"desktop status probe failed: {output}",
                },
                status_code=500,
            )

        if "RUNNING" not in output:
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
