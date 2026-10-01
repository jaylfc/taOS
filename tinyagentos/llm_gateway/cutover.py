"""Move agents onto the gateway by retargeting one proxy device.

Every local agent container reaches its LLM at its OWN ``127.0.0.1:4000``
through the incus proxy device ``taos-proxy-litellm``. Its ``connect`` side
names a host port: the gateway's agent listener (7838) or, on a container
deployed before the cutover, the old LiteLLM proxy port (4000 on legacy
installs, 7834 on newer ones, or the configured ``server.litellm_port``).
Changing that one value moves the agent. Its base URL, its config files and
its process are untouched, so nothing is redeployed or restarted. The device
keeps its ``taos-proxy-litellm`` name and its in-container listen side
(``127.0.0.1:4000``): renaming it would mean re-adding a device on every
existing container, for no behaviour change.

:func:`reconcile_agents` runs once at controller startup. LiteLLM is gone
(removal stage 2b-2a), so a device still on a LiteLLM port points at a dead
port and is moved to the listener UNCONDITIONALLY. When the agent's key is
its own local key-store row, its gateway key is minted first; a key that
cannot be mirrored (none, another agent's, a LiteLLM Postgres virtual key)
or a model the gateway cannot route no longer keeps the agent where it is.
The agent is still moved, and the reason is reported on its item so an
operator can re-key or redeploy it.

The one thing that is never done: moving a device onto a listener this start
could not verify as its own (the identity nonce, see
:func:`wait_for_listener`). A stranger on the port would otherwise receive
every agent's traffic and key.

Only a device whose current value was READ and recognised is changed; an
unreadable or unexpected value is reported and left alone. A second run
changes nothing. Every incus call names the container's own project (agent
containers live in e.g. ``user-999``, not ``default``).
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

DEVICE = "taos-proxy-litellm"
# A remote agent has no proxy device and the agent listener is loopback-only,
# so it has no path to the gateway. Decision dec-26f4cw: a remote deploy is
# refused with this reason, and an existing remote agent is reported with it.
REMOTE_NO_GATEWAY_REASON = (
    "remote agents need the network LLM gateway, not built yet (the gateway's "
    "agent listener is loopback-only and a remote agent has no proxy device)"
)
# Host ports the LiteLLM proxy listened on: 4000 (legacy installs, pinned by
# config.py) and 7834 (the later default). The configured
# ``server.litellm_port`` is added at run time. A device connecting to any of
# them points at a dead port and is moved to the gateway.
LEGACY_LITELLM_PORTS = (4000, 7834)
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


def _container_name(agent: dict) -> str:
    return agent.get("container_name") or f"taos-agent-{agent.get('name')}"


def _key_problem(agent: dict, store: LiteLLMKeyStore) -> str | None:
    """Why this agent's key cannot become a gateway key, or None."""
    key = agent.get("llm_key")
    if not isinstance(key, str) or not key:
        return "no LLM key recorded for this agent; re-key or redeploy it"
    row = store.agent_key_by_hash(token_hash(key))
    if row is None or row.get("agent") != agent.get("name"):
        return ("its key is not a local key-store row of this agent (unknown, "
                "another agent's, or a LiteLLM Postgres virtual key); re-key or "
                "redeploy it")
    return None


ModelsProblem = Callable[[list], Awaitable["str | None"]]


async def models_problem(state, models: Iterable[str]) -> str | None:
    """Why the gateway cannot serve EVERY one of ``models``, or None.

    Read from the same routing table the gateway routes with. A model is
    servable when its highest-priority route is Anthropic (translated),
    OpenAI-compatible (``openai`` / ``openrouter`` / ``deepseek``), or
    Ollama-shaped on a backend type that exposes ``/v1/chat/completions`` at
    the ref taOS installs (``forward.OLLAMA_V1_BACKEND_TYPES``: ollama,
    rkllama, and hailo-ollama, whose NDJSON stream the gateway translates).
    Anything unknown is a problem: an agent is never moved on a guess.
    """
    from tinyagentos.llm_gateway.forward import (
        OLLAMA_PROVIDERS,
        OLLAMA_V1_BACKEND_TYPES,
        OPENAI_COMPATIBLE_PROVIDERS,
    )
    from tinyagentos.llm_gateway.resolve import (
        TAOS_DEFAULT,
        default_chat_model,
        find_routes,
        routing_table,
    )

    models = [m for m in (models or []) if isinstance(m, str) and m]
    if not models:
        return "its key allows no models, so there is nothing to serve"
    table = routing_table(state)
    from tinyagentos.litellm_config import EMBEDDING_ALIAS

    for requested in models:
        if requested == EMBEDDING_ALIAS:
            # Served by the gateway's /embeddings (not a chat route): an
            # allowlist that grants it must not bounce the agent to LiteLLM.
            continue
        name = requested
        if requested == TAOS_DEFAULT:
            name = await default_chat_model(state)
            if name is None:
                return "taos-default has no default chat model behind it"
        routes = find_routes(table, name)
        if not routes:
            return f"model {requested!r} is not in the routing table"
        route = routes[0]
        if route.provider == "anthropic" or route.provider in OPENAI_COMPATIBLE_PROVIDERS:
            continue
        if route.provider in OLLAMA_PROVIDERS:
            if route.backend_type in OLLAMA_V1_BACKEND_TYPES:
                continue
            return (f"model {requested!r} is served by a {route.backend_type or 'unknown'!r} backend "
                    "without /v1/chat/completions")
        return (f"model {requested!r} is served by a {route.provider or 'unknown'!r} backend "
                "the gateway cannot forward yet")
    return None


async def reconcile_agents(
    *,
    agents: Iterable[dict],
    data_dir: Path,
    gateway_port: int,
    legacy_ports: Iterable[int] = LEGACY_LITELLM_PORTS,
    listener_ready: bool,
    models_problem: ModelsProblem | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Point each local agent's LLM proxy device at the gateway listener.

    ``legacy_ports`` are the host ports LiteLLM listened on; a device that
    connects to one of them is moved. ``models_problem(models)`` says why the
    gateway cannot serve an agent's allowlist (or None); it no longer blocks
    the move, it is reported.

    Returns ``{"repointed": [...], "unchanged": [...], "skipped": [...]}``,
    each item ``{"agent", ...}``; skipped and repointed items may carry a
    ``reason``.
    """
    report: dict[str, list[dict[str, Any]]] = {"repointed": [], "unchanged": [], "skipped": []}
    gw = _target(gateway_port)
    legacy = {_target(p) for p in legacy_ports if p} - {gw}
    agents = [a for a in agents if isinstance(a, dict) and a.get("name")]
    if not agents:
        return report

    def skip(name: str, reason: str) -> None:
        report["skipped"].append({"agent": name, "reason": reason})

    try:
        projects = await _container_projects()
    except Exception as exc:  # noqa: BLE001 - reported per agent below
        logger.warning("llm gateway cutover: listing containers failed: %s", type(exc).__name__)
        projects = None
    store = LiteLLMKeyStore(default_keystore_path(data_dir))

    for agent in agents:
        name = agent["name"]
        try:
            await _reconcile_one(
                agent, report, skip, projects=projects, store=store,
                gw=gw, legacy=legacy, listener_ready=listener_ready,
                models_problem=models_problem,
            )
        except Exception as exc:  # noqa: BLE001 - one bad container never stops the rest
            skip(name, f"reconcile failed ({type(exc).__name__}): left as it was")
    return report


async def _route_problem(models_problem, row) -> str | None:
    if row is None:
        return None
    if models_problem is None:
        return "routability of its models is unknown (no check supplied)"
    try:
        return await models_problem(list(row["allowed_models"]))
    except Exception as exc:  # noqa: BLE001 - reported, never blocks
        return f"routability check failed: {type(exc).__name__}"


async def _reconcile_one(agent, report, skip, *, projects, store, gw, legacy,
                         listener_ready, models_problem) -> None:
    name = agent["name"]
    if agent.get("remote"):
        skip(name, REMOTE_NO_GATEWAY_REASON)
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
    if current != gw and current not in legacy:
        skip(name, f"unexpected {DEVICE} connect {current!r}: left alone")
        return

    key_problem = _key_problem(agent, store)
    row = None if key_problem else store.agent_key_by_hash(token_hash(agent["llm_key"]))
    route_problem = await _route_problem(models_problem, row)
    problems = [p for p in (key_problem, route_problem) if p]
    if key_problem is None:
        # Idempotent. The gateway also accepts the plain key-store key, so a
        # failed mirror is reported, not fatal.
        if store.mint_mirror(name, agent["llm_key"]) is None or not store.live_mirror(name, agent["llm_key"]):
            problems.append("its gateway key could not be minted from its key-store row")

    if current == gw:
        if problems:
            logger.warning("llm gateway cutover: %s is on the gateway but %s", name, "; ".join(problems))
        report["unchanged"].append({"agent": name, "connect": current})
        return

    # current is a dead LiteLLM port.
    if not listener_ready:
        skip(name, f"still on old LiteLLM port {current}: the gateway agent listener "
                   "was not verified on this start, so nothing is moved onto it")
        return
    if not await _set_connect(container, project, gw):
        skip(name, f"incus refused to set {DEVICE} connect to the gateway")
        return
    item: dict[str, Any] = {"agent": name, "connect": gw, "from": current}
    if problems:
        item["reason"] = "; ".join(problems)
    report["repointed"].append(item)


# Startup probe budget: the listener starts beside the main server, so give
# it a little while. Module-level so tests can shorten it.
_PROBE_ATTEMPTS = 30
_PROBE_DELAY = 1.0
IDENTITY_HEADER = "x-taos-llm-listener"


async def wait_for_listener(port: int, *, identity: str | None, attempts: int = 30,
                            delay: float = 1.0) -> bool:
    """True once OUR agent listener answers on ``127.0.0.1:port``.

    Accepting TCP proves nothing: any process may hold the port (an
    unauthenticated model server would then receive every agent's traffic).
    The probe is ``GET /v1/models`` with no key, and it counts only when the
    answer carries this process's per-start nonce in ``x-taos-llm-listener``
    AND is the gateway's own 401 (``code: invalid_api_key``).
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
                    "llm gateway: 127.0.0.1:%s answers but is not this controller's "
                    "agent listener (status %s); nothing will be moved onto it",
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


def llm_gateway_models_check(state) -> ModelsProblem:
    """``models_problem`` bound to ``state``, for the deployer."""
    async def check(models):
        return await models_problem(state, models)

    return check


def llm_gateway_live_port(state) -> int:
    """The agent listener port new deploys may target, or 0 (not verified).

    Non-zero only after the startup reconcile verified the listener's
    identity nonce; a dead listener, or a stranger on its port, is never
    handed to an agent.
    """
    if not getattr(state, "llm_gateway_listener_ready", False):
        return 0
    return int(getattr(state, "llm_gateway_agent_port", 0) or 0)


async def run_startup_reconcile(state) -> dict | None:
    """Lifespan hook: reconcile every configured agent once, log the result.

    Runs only when ``__main__`` recorded the agent listener port on the app
    state (so tests and embedded apps never touch incus). The gateway is
    always on: ``TAOS_LLM_GATEWAY=0`` no longer moves anyone back.
    """
    from tinyagentos import llm_gateway

    port = getattr(state, "llm_gateway_agent_port", None)
    if port is None:
        return None
    llm_gateway.enabled()  # logs once if the old off switch is still set
    identity = getattr(state, "llm_gateway_listener_identity", None)
    ready = (
        await wait_for_listener(port, identity=identity, attempts=_PROBE_ATTEMPTS, delay=_PROBE_DELAY)
        if port else False
    )
    state.llm_gateway_listener_ready = ready
    if not ready:
        logger.error(
            "llm gateway: this controller's agent listener was not verified on 127.0.0.1:%s; "
            "no agent is moved onto it, and agents still on an old LiteLLM port have no LLM path",
            port,
        )
    config = getattr(state, "config", None)
    agents = list(getattr(config, "agents", None) or [])
    if not agents:
        return None
    proxy = getattr(state, "llm_proxy", None)
    legacy = {*LEGACY_LITELLM_PORTS}
    configured = getattr(proxy, "port", None)
    if configured:
        legacy.add(int(configured))
    try:
        report = await reconcile_agents(
            agents=agents,
            data_dir=Path(state.data_dir),
            gateway_port=port or llm_gateway.DEFAULT_AGENT_PORT,
            legacy_ports=sorted(legacy),
            listener_ready=ready,
            models_problem=llm_gateway_models_check(state),
        )
    except Exception:
        logger.exception("llm gateway cutover: reconcile failed; agents left where they were")
        return None
    state.llm_gateway_cutover_report = report
    for item in report["repointed"]:
        logger.warning("llm gateway cutover: %s moved from %s to the gateway (%s)%s", item["agent"],
                       item.get("from"), item["connect"],
                       f": {item['reason']}" if item.get("reason") else "")
    for item in report["skipped"]:
        logger.warning("llm gateway cutover: %s left as is: %s", item["agent"], item["reason"])
    logger.info(
        "llm gateway cutover: %d repointed, %d unchanged, %d skipped",
        len(report["repointed"]), len(report["unchanged"]), len(report["skipped"]),
    )
    return report
