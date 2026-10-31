"""The agent controller listener: the whole controller, for agent containers.

Agent containers reach the controller through an incus proxy device whose
host side connects from the host's own loopback. Served on the main port,
those requests carry a loopback client address, the same as the device's own
console. Agents therefore connect here instead.

This is a pass-through ASGI wrapper over the WHOLE main app (http and
websocket): every request is handed to ``main_app`` unchanged except that
``scope["client"]`` is replaced with the constant ``(AGENT_CLIENT_HOST, 0)``,
a valid IPv4 literal (RFC 5737 TEST-NET-1) that is never loopback and never
routable. All agents share that one client address.

Every http response carries ``x-taos-agent-listener: <nonce>``, the per-start
identity the startup reconcile and the deployer check before an agent is
pointed here (so a different process that grabbed the port is never mistaken
for this one). The wrapper answers lifespan itself; the main app's lifespan
runs on the main server. Bound to loopback only by ``__main__``.
"""
from __future__ import annotations

AGENT_CLIENT_HOST = "192.0.2.1"
IDENTITY_HEADER = b"x-taos-agent-listener"


def create_agent_controller_app(main_app, *, identity: str | None = None):
    """ASGI app for the agent controller listener, wrapping ``main_app``."""
    stamp = identity.encode("ascii") if identity else None

    async def app(scope, receive, send):
        kind = scope["type"]
        if kind == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if kind not in ("http", "websocket"):
            return

        rewritten = dict(scope)
        rewritten["client"] = (AGENT_CLIENT_HOST, 0)
        if kind == "websocket" or stamp is None:
            await main_app(rewritten, receive, send)
            return

        async def stamped_send(message):
            if message["type"] == "http.response.start":
                message = dict(message)
                message["headers"] = [*message.get("headers", []), (IDENTITY_HEADER, stamp)]
            await send(message)

        await main_app(rewritten, receive, stamped_send)

    return app


# ---------------------------------------------------------------------------
# Port, liveness probe and the startup reconcile for existing agents
# ---------------------------------------------------------------------------

import asyncio
import logging
from typing import Any, Iterable

logger = logging.getLogger(__name__)

PORT_ENV = "TAOS_AGENT_CONTROLLER_PORT"
DEFAULT_PORT = 6972
DEVICE = "taos-proxy-taos"
PROBE_PATH = "/api/health"
_PROBE_ATTEMPTS = 30
_PROBE_DELAY = 1.0


def agent_controller_port(config=None) -> int:
    """The listener's host port: env > ``server.agent_controller_port`` > 6972.

    0 disables the listener; new agent deploys are then refused."""
    import os

    raw = os.environ.get(PORT_ENV)
    if raw is not None and raw.strip():
        return int(raw.strip())
    server = getattr(config, "server", None) or {}
    value = server.get("agent_controller_port", DEFAULT_PORT)
    return int(value) if value is not None else DEFAULT_PORT


async def wait_for_listener(port: int, *, identity: str | None, attempts: int = 30,
                            delay: float = 1.0) -> bool:
    """True once OUR agent controller listener answers on ``127.0.0.1:port``.

    Accepting TCP proves nothing: any process may hold the port. The probe is
    ``GET /api/health`` through the listener, and it counts only when the
    response (whatever its status: the wrapper stamps it even while the main
    app is still starting) carries this start's nonce in
    ``x-taos-agent-listener``.
    """
    import httpx

    if not port or not identity:
        return False
    url = f"http://127.0.0.1:{int(port)}{PROBE_PATH}"
    header = IDENTITY_HEADER.decode("ascii")
    async with httpx.AsyncClient(timeout=httpx.Timeout(3.0), follow_redirects=False) as client:
        for _ in range(max(1, attempts)):
            try:
                resp = await client.get(url)
            except httpx.HTTPError:
                await asyncio.sleep(delay)
                continue
            if resp.headers.get(header) == identity:
                return True
            logger.error(
                "agent controller listener: 127.0.0.1:%s answers but is not this "
                "controller's listener (status %s); nothing will be moved onto it",
                port, resp.status_code,
            )
            return False
    return False


def agent_controller_live_port(state) -> int:
    """The listener port new deploys may target, or 0 (not verified)."""
    if not getattr(state, "agent_controller_listener_ready", False):
        return 0
    return int(getattr(state, "agent_controller_port", 0) or 0)


def _target(port: int) -> str:
    return f"tcp:127.0.0.1:{int(port)}"


async def _get_connect(name: str, project: str) -> str | None:
    from tinyagentos import containers
    from tinyagentos.llm_gateway.cutover import _INCUS_TIMEOUT

    code, output = await containers._run(
        ["incus", "config", "device", "get", name, DEVICE, "connect", "--project", project],
        timeout=_INCUS_TIMEOUT,
    )
    if code != 0:
        return None
    return (output or "").strip() or None


async def _set_connect(name: str, project: str, value: str) -> bool:
    from tinyagentos import containers
    from tinyagentos.llm_gateway.cutover import _INCUS_TIMEOUT

    code, _ = await containers._run(
        ["incus", "config", "device", "set", name, DEVICE, f"connect={value}", "--project", project],
        timeout=_INCUS_TIMEOUT,
    )
    return code == 0


async def reconcile_agents(*, agents: Iterable[dict], main_port: int, agent_port: int,
                           listener_ready: bool) -> dict[str, list[dict[str, Any]]]:
    """Move each local agent's ``taos-proxy-taos`` connect side onto the listener.

    Only a device whose connect side was READ and is exactly the main port is
    changed; one already on the listener is left alone, anything else is
    reported and left alone. Nothing is moved unless ``listener_ready``.
    Returns ``{"repointed": [...], "unchanged": [...], "skipped": [...]}``.
    """
    from tinyagentos.llm_gateway.cutover import _container_name, _container_projects

    report: dict[str, list[dict[str, Any]]] = {"repointed": [], "unchanged": [], "skipped": []}
    agents = [a for a in agents if isinstance(a, dict) and a.get("name")]
    if not agents:
        return report
    old, new = _target(main_port), _target(agent_port)

    def skip(name: str, reason: str) -> None:
        report["skipped"].append({"agent": name, "reason": reason})

    try:
        projects = await _container_projects()
    except Exception as exc:  # noqa: BLE001 - reported per agent below
        logger.warning("agent controller listener: listing containers failed: %s", type(exc).__name__)
        projects = None
    for agent in agents:
        name = agent["name"]
        try:
            if agent.get("remote"):
                skip(name, "remote agent: no proxy device")
                continue
            container = _container_name(agent)
            if projects is None:
                skip(name, "incus unavailable: container project unknown")
                continue
            project = projects.get(container)
            if project is None:
                skip(name, f"container {container} not found in any incus project")
                continue
            current = await _get_connect(container, project)
            if current is None:
                skip(name, f"could not read {DEVICE} connect on {container} (project {project})")
            elif current == new:
                report["unchanged"].append({"agent": name, "connect": current})
            elif current != old:
                skip(name, f"unexpected {DEVICE} connect {current!r}: left alone")
            elif not listener_ready:
                skip(name, "the agent controller listener was not verified on this start, "
                           "so nothing is moved onto it")
            elif not await _set_connect(container, project, new):
                skip(name, f"incus refused to set {DEVICE} connect")
            else:
                report["repointed"].append({"agent": name, "connect": new, "from": current})
        except Exception as exc:  # noqa: BLE001 - one bad container never stops the rest
            skip(name, f"reconcile failed ({type(exc).__name__}): left as it was")
    return report


async def run_startup_reconcile(state) -> dict | None:
    """Lifespan hook: verify the listener, then move every configured agent.

    Runs only when ``__main__`` recorded the listener port on the app state
    (so tests and embedded apps never touch incus)."""
    port = getattr(state, "agent_controller_port", None)
    if port is None:
        return None
    identity = getattr(state, "agent_controller_listener_identity", None)
    ready = (
        await wait_for_listener(port, identity=identity, attempts=_PROBE_ATTEMPTS, delay=_PROBE_DELAY)
        if port else False
    )
    state.agent_controller_listener_ready = ready
    if not ready:
        logger.error(
            "agent controller listener not verified on 127.0.0.1:%s; no agent is moved "
            "onto it and new agent deploys are refused", port,
        )
    config = getattr(state, "config", None)
    agents = list(getattr(config, "agents", None) or [])
    main_port = getattr(state, "main_port", None) or 0
    if not agents or not main_port:
        return None
    try:
        report = await reconcile_agents(
            agents=agents, main_port=main_port, agent_port=port, listener_ready=ready,
        )
    except Exception:
        logger.exception("agent controller listener: reconcile failed; agents left where they were")
        return None
    state.agent_controller_reconcile_report = report
    for item in report["repointed"]:
        logger.warning("agent controller listener: %s moved from %s to %s",
                       item["agent"], item.get("from"), item["connect"])
    for item in report["skipped"]:
        logger.warning("agent controller listener: %s left as is: %s", item["agent"], item["reason"])
    logger.info("agent controller listener: %d repointed, %d unchanged, %d skipped",
                len(report["repointed"]), len(report["unchanged"]), len(report["skipped"]))
    return report
