"""Headless Headscale mesh membership for taOSgo (Slice 2).

The always-on host joins the account's Headscale mesh using the single-use
preauth key from the cluster-join ready payload, so that
``<label>.<handle>.taos.my`` (Web Studio publish) and off-LAN desktop access can
route to this host over the tailnet. Jay decided the mechanism is **system
tailscale** (tailscale + tailscaled managed as a service), not userspace tsnet:
real kernel networking, boot-persistent, best for the always-on host.

Everything here is fail-soft and never raises to the caller: on a host without
``tailscale`` installed (dev boxes, CI) every call returns a structured
"not available" result so the join flow degrades cleanly. The installer is
responsible for actually placing the ``tailscale`` binary + daemon on supported
hosts; this module only drives it.

The login server (Headscale) defaults to ``https://hs.taos.my`` and is
overridable via ``TAOS_HEADSCALE_URL`` for staging/self-hosted control servers.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_LOGIN_SERVER = "https://hs.taos.my"


def login_server() -> str:
    """The Headscale control server URL, trailing slash stripped."""
    return os.environ.get("TAOS_HEADSCALE_URL", _DEFAULT_LOGIN_SERVER).rstrip("/")


def is_tailscale_installed() -> bool:
    """True when the ``tailscale`` CLI is on PATH (the installer provides it on
    supported hosts). When False, every operation degrades to "not available"."""
    return shutil.which("tailscale") is not None


async def _run(args: list[str], timeout: float = 30.0) -> tuple[int, str, str]:
    """Run a subprocess, returning ``(returncode, stdout, stderr)``. Fail-soft:
    a missing binary or timeout yields a non-zero code and a detail string, never
    raises. On timeout the child is killed and reaped so it is not orphaned."""
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode, out.decode(errors="replace"), err.decode(errors="replace")
    except asyncio.TimeoutError:
        if proc is not None:
            try:
                proc.kill()
                await proc.wait()
            except Exception:  # pragma: no cover - defensive
                pass
        return 124, "", f"timed out after {timeout}s"
    except FileNotFoundError:
        return 127, "", "tailscale not installed"
    except Exception as exc:  # noqa: BLE001 - never raise into the join flow
        return 1, "", str(exc)[:200]


async def mesh_up(
    preauth_key: str, hostname: str, *, ls: Optional[str] = None, timeout: float = 60.0
) -> dict:
    """Join the account Headscale mesh with a single-use preauth key.

    Runs ``tailscale up --login-server <ls> --authkey <key> --hostname <name>``.
    Returns ``{"ok": bool, "detail": str}``. Fail-soft: a host without tailscale,
    a bad key, or a timeout all return ``ok=False`` with a reason rather than
    raising, so a poll that triggers the join is never broken by it.
    """
    if not preauth_key or not hostname:
        return {"ok": False, "detail": "missing preauth key or hostname"}
    if not is_tailscale_installed():
        return {"ok": False, "detail": "tailscale not installed"}
    server = ls or login_server()
    rc, _out, err = await _run(
        [
            "tailscale", "up",
            "--login-server", server,
            "--authkey", preauth_key,
            "--hostname", hostname,
        ],
        timeout=timeout,
    )
    if rc == 0:
        logger.info("taosgo: joined mesh %s as %s", server, hostname)
        return {"ok": True, "detail": f"joined {server}"}
    detail = (err.strip() or f"exit {rc}")[:200]
    logger.warning("taosgo: mesh join failed (%s): %s", server, detail)
    return {"ok": False, "detail": detail}


async def mesh_status() -> dict:
    """Report mesh membership from ``tailscale status --json``. On success:
    ``{joined, online, tailnet, node_ip, hostname}``. On the not-available /
    error path: ``{joined: False, detail}``. Fail-soft: not-installed / not-up
    returns ``joined=False`` with a detail, never raises."""
    if not is_tailscale_installed():
        return {"joined": False, "detail": "tailscale not installed"}
    rc, out, err = await _run(["tailscale", "status", "--json"], timeout=10.0)
    if rc != 0:
        return {"joined": False, "detail": (err.strip() or f"exit {rc}")[:200]}
    try:
        data = json.loads(out)
    except (ValueError, TypeError):
        return {"joined": False, "detail": "unparseable status"}
    self_node = data.get("Self") or {}
    online = bool(self_node.get("Online"))
    ips = self_node.get("TailscaleIPs") or []
    # BackendState "Running" + a Self node means we are up on the tailnet.
    joined = data.get("BackendState") == "Running" and bool(self_node)
    return {
        "joined": joined,
        "online": online,
        "tailnet": (data.get("CurrentTailnet") or {}).get("Name"),
        "node_ip": ips[0] if ips else None,
        "hostname": self_node.get("HostName"),
    }


async def is_joined() -> bool:
    """True when this host is up on the tailnet (used to avoid a redundant
    re-join when a poll re-delivers a ready payload)."""
    return bool((await mesh_status()).get("joined"))


async def mesh_down() -> dict:
    """Leave the mesh (``tailscale logout``). Fail-soft."""
    if not is_tailscale_installed():
        return {"ok": False, "detail": "tailscale not installed"}
    rc, _out, err = await _run(["tailscale", "logout"], timeout=30.0)
    if rc == 0:
        return {"ok": True, "detail": "left mesh"}
    return {"ok": False, "detail": (err.strip() or f"exit {rc}")[:200]}
