"""Headless Headscale mesh membership for taOSgo (Slice 2).

The always-on host joins the account's Headscale mesh using the single-use
preauth key from the cluster-join ready payload, so that
``<subdomain>.taos.my`` (a claimed subdomain; see
``docs/design/account-username-subdomain-model.md``) and off-LAN desktop access can
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

A host may already run a tailscaled for its OWNER'S tailnet (the taOS handset
does, and it is the only remote path to it). Re-pointing that daemon at the
mesh, or logging it out, strands the host. So:

* ``TAOS_TAILSCALE_SOCKET`` (optional) names a dedicated tailscaled for the
  mesh; every call here then passes ``--socket=<path>`` and drives only it.
* Without it, the default daemon's ControlURL is read from
  ``tailscale debug prefs`` (``status --json`` does not carry it). Unless that
  daemon is logged out or already on the configured login server, mesh_up and
  mesh_down refuse with ``foreign-control-server`` and run nothing, and
  mesh_status reports ``joined=False``, so the join poll does not mistake the
  owner's tailnet for the mesh.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_DEFAULT_LOGIN_SERVER = "https://hs.taos.my"


def login_server() -> str:
    """The Headscale control server URL, trailing slash stripped."""
    return os.environ.get("TAOS_HEADSCALE_URL", _DEFAULT_LOGIN_SERVER).rstrip("/")


def is_tailscale_installed() -> bool:
    """True when the ``tailscale`` CLI is on PATH (the installer provides it on
    supported hosts). When False, every operation degrades to "not available"."""
    return shutil.which("tailscale") is not None


def _ts(*args: str) -> list[str]:
    """A tailscale argv, aimed at ``TAOS_TAILSCALE_SOCKET`` when it is set."""
    sock = os.environ.get("TAOS_TAILSCALE_SOCKET", "").strip()
    return ["tailscale", *([f"--socket={sock}"] if sock else []), *args]


def _origin(url: str) -> str:
    """scheme://host[:port], lowercased, so trailing slashes and paths never
    make the same control server look foreign."""
    parsed = urlparse(url.strip())
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


async def _foreign_daemon(server: str) -> str | None:
    """Why the daemon must not be driven, or None when it may be.

    A dedicated socket is the mesh's own daemon, so it is never foreign. On the
    default socket, a daemon whose prefs cannot be read is refused too: a guess
    here is what logs a host out of its owner's tailnet.
    """
    if os.environ.get("TAOS_TAILSCALE_SOCKET", "").strip():
        return None
    rc, out, _err = await _run(_ts("debug", "prefs"), timeout=10.0)
    if rc != 0:
        return "control-server-unknown"
    try:
        prefs = json.loads(out)
    except (ValueError, TypeError):
        return "control-server-unknown"
    if not isinstance(prefs, dict):
        return "control-server-unknown"
    if prefs.get("LoggedOut") is True:
        return None
    control = str(prefs.get("ControlURL") or "")
    if control and _origin(control) == _origin(server):
        return None
    return "foreign-control-server"


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
    refusal = await _foreign_daemon(server)
    if refusal:
        logger.warning("taosgo: mesh join refused (%s): the default tailscaled belongs elsewhere", refusal)
        return {"ok": False, "detail": refusal}
    rc, _out, err = await _run(
        _ts(
            "up",
            "--login-server", server,
            "--authkey", preauth_key,
            "--hostname", hostname,
        ),
        timeout=timeout,
    )
    if rc == 0:
        logger.info("taosgo: joined mesh %s as %s", server, hostname)
        return {"ok": True, "detail": f"joined {server}"}
    detail = (err.strip() or f"exit {rc}")[:200]
    logger.warning("taosgo: mesh join failed (%s): %s", server, detail)
    return {"ok": False, "detail": detail}


_GUEST_TAG = "tag:guest"


def _is_guest_node(node: dict) -> bool:
    """True when a peer node carries the ``tag:guest`` tag (ACL-pinned guest)."""
    tags: list[str] = node.get("Tags") or []
    return _GUEST_TAG in tags


def _guest_peer_entry(node: dict) -> dict:
    """Extract the standard guest-peer summary from a tailscale peer dict.

    ``node_ip`` uses ``TailscaleIPs[0]`` (the first address reported), which
    is consistent with the host's own ``mesh_status`` output. For multi-homed
    peers this may be an IPv4 or IPv6 address (or ``None`` when the list is
    empty). Callers that need a specific address family should resolve the
    hostname, not rely on this field as the sole network identifier.
    """
    ips = node.get("TailscaleIPs") or []
    return {
        "hostname": node.get("HostName"),
        "node_ip": ips[0] if ips else None,
        "online": bool(node.get("Online")),
    }


async def mesh_status() -> dict:
    """Report mesh membership from ``tailscale status --json``. On success:
    ``{joined, online, tailnet, node_ip, hostname, guests}`` where ``guests``
    is a list of peer nodes tagged ``tag:guest`` (ACL-pinned guest instances that
    joined this host's mesh). On the not-available / error path:
    ``{joined: False, detail}``. Fail-soft: not-installed / not-up returns
    ``joined=False`` with a detail, never raises."""
    if not is_tailscale_installed():
        return {"joined": False, "detail": "tailscale not installed"}
    refusal = await _foreign_daemon(login_server())
    if refusal:
        return {"joined": False, "detail": refusal}
    rc, out, err = await _run(_ts("status", "--json"), timeout=10.0)
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

    # --- guest peer nodes (C2: cross-user guest preauth) -------------------
    guests: list[dict] = []
    peers = data.get("Peer") or {}
    if isinstance(peers, dict):
        for peer in peers.values():
            if not isinstance(peer, dict):
                continue
            if _is_guest_node(peer):
                guests.append(_guest_peer_entry(peer))
    # ------------------------------------------------------------------------

    return {
        "joined": joined,
        "online": online,
        "tailnet": (data.get("CurrentTailnet") or {}).get("Name"),
        "node_ip": ips[0] if ips else None,
        "hostname": self_node.get("HostName"),
        "guests": guests,
    }


async def is_joined() -> bool:
    """True when this host is up on the tailnet (used to avoid a redundant
    re-join when a poll re-delivers a ready payload)."""
    return bool((await mesh_status()).get("joined"))


async def mesh_down() -> dict:
    """Leave the mesh (``tailscale logout``). Fail-soft. Never logs out a
    daemon that is not on the mesh's login server (see the module docstring)."""
    if not is_tailscale_installed():
        return {"ok": False, "detail": "tailscale not installed"}
    refusal = await _foreign_daemon(login_server())
    if refusal:
        return {"ok": False, "detail": refusal}
    rc, _out, err = await _run(_ts("logout"), timeout=30.0)
    if rc == 0:
        return {"ok": True, "detail": "left mesh"}
    return {"ok": False, "detail": (err.strip() or f"exit {rc}")[:200]}
