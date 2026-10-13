"""Agent proxy device cutover to the agent controller listener.

Every local agent container reaches the controller at its OWN ``127.0.0.1:6969``
through the incus proxy device ``taos-proxy-taos``. Its ``connect`` side names a
host port: the agent controller listener (6972) or, on a container deployed before
the cutover, the old controller port (6969). Changing that one value moves the
agent. Its base URL, its config files and its process are untouched, so nothing
is redeployed or restarted. The device keeps its historical
``taos-proxy-taos`` name (renaming it would mean re-adding a device on every
existing container), so the behavior change is minimal.

:func:`reconcile_agents` runs once at controller startup. The agent controller
listener is the only controller path for agents, so a device still on an old
controller port is moved UNCONDITIONALLY (there is nothing to fall back to);
when the agent's key is its own local key-store row, its controller key is
minted first; a key that cannot be mirrored (none, another agent's, a LiteLLM
Postgres virtual key) or a model the controller cannot route no longer keeps
the agent where it is. The agent is still moved, and the reason is reported on
its item so an operator can re-key or redeploy it.

The one thing that is never done: moving a device onto a listener this start
could not verify as its own (the identity nonce, see
:func:`wait_for_listener`). A stranger on the port would otherwise receive
every agent's traffic and key.

Only a device whose current value was READ and recognised is changed; an
unreadable or unexpected value is reported and left alone. A second run
changes nothing. Every incus call names the container's own project (agent
containers live in e.g. ``user-999``, not ``default``), so no legacy agent stays
on the main port.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

from tinyagentos import containers
from tinyagentos.litellm_keystore import LiteLLMKeyStore, default_keystore_path, token_hash

logger = logging.getLogger(__name__)

DEVICE = "taos-proxy-taos"
# A remote agent has no proxy device and the agent controller listener is
# loopback-only, so it has no path to the controller. Decision dec-26f4cw: a
# remote deploy is refused with this reason, and an existing remote agent is
# reported with it.
REMOTE_NO_CONTROLLER_REASON = (
    "remote agents need the controller, not built yet (the agent controller "
    "listener is loopback-only and a remote agent has no proxy device)"
)
# Per incus call; a hung container costs this much, then the next agent runs.
_INCUS_TIMEOUT = 30


def _target(port: int) -> str:
    return f"tcp:127.0.0.1:{int(port)}"


async def _container_projects() -> dict[str, str] | None:
    """``{container: project}`` across ALL incus projects, or None if unknown."""
    try:
        code, output = await containers._run(
            ["incus", "list", "--all-projects", "-f", "json"], timeout=_INCUS_TIMEOUT
        )
    except (FileNotFoundError, OSError):
        return None
    if code != 0:
        return None
    try:
        instances = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        return None
    out: dict[str, str] = {}
    for inst in instances if isinstance(instances, list) else []:
        if isinstance(inst, dict) and isinstance(inst.get("name"), str):
            out[inst["name"]] = inst.get("project") or "default"
    return out


async def _get_connect(name: str, project: str) -> str | None:
    code, output = await containers._run(
        ["incus", "config", "device", "get", name, DEVICE, "connect", "--project", project],
        timeout=_INCUS_TIMEOUT,
    )
    if code != 0:
        return None
    value = (output or "").strip()
    return value or None


async def _set_connect(name: str, project: str, value: str) -> bool:
    code, _ = await containers._run(
        ["incus", "config", "device", "set", name, DEVICE, f"connect={value}", "--project", project],
        timeout=_INCUS_TIMEOUT,
    )
    return code == 0


async def _reconcile_agents(
    *,
    agents: Iterable[dict],
    data_dir: Path,
    controller_port: int,
    listener_ready: bool,
    models_problem: Callable[[list], Awaitable["str | None"]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Point each local agent's LLM proxy device at the controller listener.

    ``controller_port`` is the host port the agent controller listener is bound to.
    ``models_problem(models)`` says why the controller cannot serve an agent's
    allowlist (or None); it no longer blocks the move, it is reported.

    Returns ``{"repointed": [...], "unchanged": [...], "skipped": [...]}``,
    each item ``{"agent", ...}``; skipped and repointed items may carry a
    ``reason``.
    """
    report: dict[str, list[dict[str, Any]]] = {"repointed": [], "unchanged": [], "skipped": []}
    gw = _target(controller_port)
    agents = [a for a in agents if isinstance(a, dict) and a.get("name")]
    if not agents:
        return report

    def skip(name: str, reason: str) -> None:
        report["skipped"].append({"agent": name, "reason": reason})

    try:
        projects = await _container_projects()
    except Exception as exc:  # noqa: BLE001 - reported per agent below
        logger.warning("agent controller: listing containers failed: %s", type(exc).__name__)
        projects = None
    store = LiteLLMKeyStore(default_keystore_path(data_dir))

    for agent in agents:
        name = agent["name"]
        try:
            await _reconcile_one(
                agent, report, skip, projects=projects, store=store,
                gw=gw, listener_ready=listener_ready,
                models_problem=models_problem,
            )
        except Exception as exc:  # noqa: BLE001 - one bad container never stops the rest
            skip(name, f"reconcile failed ({type(exc).__name__}): left as it was")
    return report


async def _reconcile_one(agent, report, skip, *, projects, store, gw, listener_ready,
                         models_problem) -> None:
    name = agent["name"]
    if agent.get("remote"):
        skip(name, REMOTE_NO_CONTROLLER_REASON)
        return
    container = _container_name(agent)
    if projects is None:
        skip(name, "incus unavailable: container project unknown")
        return
    project = projects.get(container)
    if project is None:
        skip(name, f"container {container} not found in any incus project")
        return
    current = await _get_connect(container, project)
    if current is None:
        skip(name, f"could not read {DEVICE} connect on {container} (project {project})")
        return
    if current != gw and current != "tcp:127.0.0.1:6969":  # legacy main port
        skip(name, f"unexpected {DEVICE} connect {current!r}: left alone")
        return

    key_problem = _key_problem(agent, store)
    row = None if key_problem else store.agent_key_by_hash(token_hash(agent["llm_key"]))
    route_problem = await _route_problem(models_problem, row)
    problems = [p for p in (key_problem, route_problem) if p]
    if key_problem is None:
        # Idempotent. The controller also accepts the plain key-store key, so a
        # failed mirror is reported, not fatal.
        if store.mint_mirror(name, agent["llm_key"]) is None or not store.live_mirror(name, agent["llm_key"]):
            problems.append("its controller key could not be minted from its key-store row")

    if current == gw:
        if problems:
            logger.warning("agent controller: %s is on the controller but %s", name, "; ".join(problems))
        report["unchanged"].append({"agent": name, "connect": current})
        return

    # current is a dead controller port.
    if not listener_ready:
        skip(name, f"still on old controller port {current}: the agent controller listener "
                   "was not verified on this start, so nothing is moved onto it")
        return
    if not await _set_connect(container, project, gw):
        skip(name, f"incus refused to set {DEVICE} connect to the agent controller")
        return
    item: dict[str, Any] = {"agent": name, "connect": gw, "from": current}
    if problems:
        item["reason"] = "; ".join(problems)
    report["repointed"].append(item)


async def _route_problem(models_problem, row) -> str | None:
    if row is None:
        return None
    if models_problem is None:
        return "routability of its models is unknown (no check supplied)"
    try:
        return await models_problem(list(row["allowed_models"]))
    except Exception as exc:  # noqa: BLE001 - reported, never blocks
        return f"routability check failed: {type(exc).__name__}"


async def reconcile_agents(
    *,
    agents: Iterable[dict],
    data_dir: Path,
    controller_port: int,
    listener_ready: bool,
    models_problem: Callable[[list], Awaitable["str | None"]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Public wrapper for _reconcile_agents."""
    return await _reconcile_agents(
        agents=agents,
        data_dir=data_dir,
        controller_port=controller_port,
        listener_ready=listener_ready,
        models_problem=models_problem,
    )


# Startup probe budget: the listener starts beside the main server, so give
# it a little while. Module-level so tests can shorten it.
_PROBE_ATTEMPTS = 30
_PROBE_DELAY = 1.0
IDENTITY_HEADER = "x-taos-agent-listener"


async def wait_for_listener(port: int, *, identity: str | None, attempts: int = 30,
                             delay: float = 1.0) -> bool:
    """True once OUR agent controller listener answers on ``127.0.0.1:port``.

    Accepting TCP proves nothing: any process may hold the port (an
    unauthenticated model server would then receive every agent's traffic).
    The probe is ``GET /v1/models`` with no key, and it counts only when the
    answer carries this process's per-start nonce in ``x-taos-agent-listener``
    AND is the controller's own 401 (``code: invalid_api_key``).
    """
    import httpx

    if not port or not identity:
        return False
    url = f"http://127.0.0.1:{int(port)}/v1/models"
    async with httpx.AsyncClient(timeout=httpx.Timeout(3.0), follow_redirects=False) as client:
        for _ in range(max(1, attempts)):
            try:
                resp = await client.get(url)
            except httpx.HTTPError:
                await asyncio.sleep(delay)
                continue
            if resp.headers.get(IDENTITY_HEADER) != identity:
                # A definite answer from something that is not us.
                logger.error(
                    "agent controller: 127.0.0.1:%s answers but is not this controller's "
                    "agent controller listener (status %s); nothing will be moved onto it",
                    port, resp.status_code,
                )
                return False
            if resp.status_code == 401:
                try:
                    code = (resp.json().get("error") or {}).get("code")
                except (ValueError, AttributeError):
                    code = None
                if code == "invalid_api_key":
                    return True
            await asyncio.sleep(delay)  # ours, but the app is still starting up
    return False


def controller_live_port(state) -> int:
    """The agent controller listener port new deploys may target, or 0 (not verified).

    Non-zero only after the startup reconcile verified the listener's
    identity nonce; a dead listener, or a stranger on its port, is never
    handed to an agent.
    """
    if not getattr(state, "agent_controller_listener_ready", False):
        return 0
    return int(getattr(state, "agent_controller_agent_port", 0) or 0)


async def run_startup_reconcile(state) -> dict | None:
    """Lifespan hook: reconcile every configured agent once, log the result.

    Runs only when ``__main__`` recorded the agent listener port on the app
    state (so tests and embedded apps never touch incus). The agent controller
    is always on: ``TAOS_AGENT_CONTROLLER=0`` no longer moves anyone back.
    """
    port = getattr(state, "agent_controller_agent_port", None)
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
            "agent controller: this controller's agent controller listener was not verified on 127.0.0.1:%s; "
            "no agent is moved onto it, and agents still on an old controller port have no controller path",
            port,
        )
    config = getattr(state, "config", None)
    agents = list(getattr(config, "agents", None) or [])
    if not agents:
        return None
    proxy = getattr(state, "llm_proxy", None)
    legacy = {"tcp:127.0.0.1:6969"}  # old main controller port
    try:
        report = await reconcile_agents(
            agents=agents,
            data_dir=Path(state.data_dir),
            controller_port=port,
            legacy_ports=[6969],
            listener_ready=ready,
            models_problem=None,  # controller has no routing for models
        )
    except Exception:
        logger.exception("agent controller: reconcile failed; agents left where they were")
        return None
    state.agent_controller_cutover_report = report
    for item in report["repointed"]:
        logger.warning("agent controller: %s moved from %s to the agent controller (%s)%s", item["agent"],
                       item.get("from"), item["connect"],
                       f": {item['reason']}" if item.get("reason") else "")
    for item in report["skipped"]:
        logger.warning("agent controller: %s left as is: %s", item["agent"], item["reason"])
    logger.info(
        "agent controller: %d repointed, %d unchanged, %d skipped",
        len(report["repointed"]), len(report["unchanged"]), len(report["skipped"]),
    )
    return report


def _key_problem(agent: dict, store: LiteLLMKeyStore) -> str | None:
    """Why this agent's key cannot become a controller key, or None."""
    key = agent.get("llm_key")
    if not isinstance(key, str) or not key:
        return "no LLM key recorded for this agent; re-key or redeploy it"
    row = store.agent_key_by_hash(token_hash(key))
    if row is None or row.get("agent") != agent.get("name"):
        return ("its key is not a local key-store row of this agent (unknown, "
                "another agent's, or a LiteLLM Postgres virtual key); re-key or "
                "redeploy it")
    return None


def _container_name(agent: dict) -> str:
    return agent.get("container_name") or f"taos-agent-{agent.get('name')}"
