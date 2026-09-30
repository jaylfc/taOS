"""Move agents between LiteLLM and the gateway by retargeting one proxy device.

Every local agent container reaches its LLM at its OWN ``127.0.0.1:4000``
through the incus proxy device ``taos-proxy-litellm``. Its ``connect`` side
names a host port: the LiteLLM proxy (``server.litellm_port``, 7834 on new
installs) or, once cut over, the gateway's agent listener (7838). Changing
that one value moves the agent. Its base URL, its config files and its
process are untouched, so nothing is redeployed or restarted.

:func:`reconcile_agents` runs once at controller startup:

- gateway ON: for each agent, mint its gateway key from its LiteLLM
  ``agent_keys`` row FIRST, confirm the key is live, then point the device at
  the listener. An agent whose key cannot be read (no key, the shared master
  key, a key the local store does not hold, a key of another agent) stays on
  LiteLLM and is reported. Nothing unscoped is ever minted.
- gateway OFF (rollback): every device that points at the listener is pointed
  back at LiteLLM. The minted gateway rows are left in place; LiteLLM never
  reads them.

Both directions only ever change a device whose current value they have READ
and recognised; an unreadable or unexpected value is reported and left
alone. A second run changes nothing. Every incus call names the container's
own project (agent containers live in e.g. ``user-999``, not ``default``).
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
# Per incus call; a hung container costs this much, then the next agent runs.
_INCUS_TIMEOUT = 30


def _target(port: int) -> str:
    return f"tcp:127.0.0.1:{int(port)}"


def _master_key(data_dir: Path) -> str | None:
    try:
        value = (Path(data_dir) / ".litellm_master_key").read_text().strip()
    except OSError:
        return None
    return value or None


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


def _key_problem(agent: dict, store: LiteLLMKeyStore, master: str | None) -> str | None:
    """Why this agent's LiteLLM key cannot become a gateway key, or None."""
    key = agent.get("llm_key")
    if not isinstance(key, str) or not key:
        return "no LiteLLM key recorded for this agent"
    if master is not None and key == master:
        return ("agent holds the shared LiteLLM master key, which the gateway "
                "refuses; re-key or redeploy it to move it to the gateway")
    return None


ModelsProblem = Callable[[list], Awaitable["str | None"]]


async def models_problem(state, models: Iterable[str]) -> str | None:
    """Why the gateway cannot serve EVERY one of ``models``, or None.

    Read from the same routing table the gateway routes with. A model is
    servable when its highest-priority route is Anthropic (translated),
    OpenAI-compatible (``openai`` / ``openrouter``), or Ollama-shaped on a
    backend of type ``ollama`` (the only one known to expose
    ``/v1/chat/completions``; rkllama and hailo-ollama do not). Anything
    unknown is a problem: an agent is never moved on a guess.
    """
    from tinyagentos.llm_gateway.forward import OLLAMA_PROVIDERS, OPENAI_COMPATIBLE_PROVIDERS
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
    config = getattr(state, "config", None)
    backend_types = {
        b.get("name"): b.get("type")
        for b in (getattr(config, "backends", None) or []) if isinstance(b, dict)
    }
    for requested in models:
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
            btype = backend_types.get(route.backend_name)
            if btype == "ollama":
                continue
            return (f"model {requested!r} is served by a {btype or 'unknown'!r} backend "
                    "without /v1/chat/completions")
        return (f"model {requested!r} is served by a {route.provider or 'unknown'!r} backend "
                "the gateway cannot forward yet")
    return None


async def reconcile_agents(
    *,
    agents: Iterable[dict],
    data_dir: Path,
    gateway_on: bool,
    gateway_port: int,
    litellm_port: int,
    listener_ready: bool,
    models_problem: ModelsProblem | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Point each local agent's LLM proxy device where the flag says.

    ``models_problem(models)`` says why the gateway cannot serve an agent's
    allowlist (or None). Without it routability is unknown and, with the
    gateway on, nobody is moved onto it.

    Returns ``{"repointed": [...], "unchanged": [...], "skipped": [...]}``,
    each item ``{"agent", ...}``; skipped items carry a ``reason``.
    """
    report: dict[str, list[dict[str, Any]]] = {"repointed": [], "unchanged": [], "skipped": []}
    gw, lite = _target(gateway_port), _target(litellm_port)
    agents = [a for a in agents if isinstance(a, dict) and a.get("name")]
    if not agents:
        return report

    def skip(name: str, reason: str) -> None:
        report["skipped"].append({"agent": name, "reason": reason})

    if gw == lite:
        for a in agents:
            skip(a["name"], "gateway listener port equals the LiteLLM port")
        return report

    try:
        projects = await _container_projects()
    except Exception as exc:  # noqa: BLE001 - reported per agent below
        logger.warning("llm gateway cutover: listing containers failed: %s", type(exc).__name__)
        projects = None
    store = LiteLLMKeyStore(default_keystore_path(data_dir)) if gateway_on else None
    master = _master_key(data_dir) if gateway_on else None

    for agent in agents:
        name = agent["name"]
        try:
            await _reconcile_one(
                agent, report, skip, projects=projects, store=store, master=master,
                gateway_on=gateway_on, gw=gw, lite=lite, listener_ready=listener_ready,
                models_problem=models_problem,
            )
        except Exception as exc:  # noqa: BLE001 - one bad container never stops the rest
            skip(name, f"reconcile failed ({type(exc).__name__}): left as it was")
    return report


async def _reconcile_one(agent, report, skip, *, projects, store, master, gateway_on,
                         gw, lite, listener_ready, models_problem) -> None:
    name = agent["name"]
    if agent.get("remote"):
        skip(name, "remote agent: no proxy device (it reaches LiteLLM over the network)")
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

    if not gateway_on:
        if current == lite:
            report["unchanged"].append({"agent": name, "connect": current})
        elif current == gw:
            if await _set_connect(container, project, lite):
                report["repointed"].append({"agent": name, "connect": lite})
            else:
                skip(name, f"incus refused to set {DEVICE} connect back to LiteLLM")
        else:
            skip(name, f"unexpected {DEVICE} connect {current!r}: left alone")
        return

    if current not in (gw, lite):
        skip(name, f"unexpected {DEVICE} connect {current!r}: left alone")
        return

    # Gateway ON. 1) the key must be this agent's local key-store key.
    problem = _key_problem(agent, store, master)
    row = None
    if problem is None:
        row = store.agent_key_by_hash(token_hash(agent["llm_key"]))
        if row is None or row.get("agent") != name:
            problem = ("its LiteLLM key is not a local key-store row of this agent "
                       "(unknown, another agent's, or a LiteLLM Postgres virtual key)")
    # 2) the gateway must be able to serve every model the key allows.
    route_problem = None
    if problem is None:
        if models_problem is None:
            route_problem = "routability of its models is unknown (no check supplied)"
        else:
            route_problem = await models_problem(list(row["allowed_models"]))

    if current == gw and not listener_ready:
        # Pointed at a listener this start could not verify (dead, or a
        # stranger on the port): back to LiteLLM, which is known.
        if await _set_connect(container, project, lite):
            report["repointed"].append({"agent": name, "connect": lite,
                                        "reason": "gateway agent listener not verified"})
        else:
            skip(name, f"incus refused to set {DEVICE} connect back to LiteLLM")
        return
    if current == gw:
        if route_problem is not None:
            # On the gateway but the gateway can no longer serve it: back to
            # LiteLLM, which can.
            if await _set_connect(container, project, lite):
                report["repointed"].append({"agent": name, "connect": lite, "reason": route_problem})
            else:
                skip(name, f"incus refused to set {DEVICE} connect back to LiteLLM")
            return
        if problem is None:
            store.mint_mirror(name, agent["llm_key"])  # idempotent
        else:
            logger.warning("llm gateway cutover: %s is on the gateway but %s", name, problem)
        report["unchanged"].append({"agent": name, "connect": current})
        return

    # current == lite
    if problem is not None or route_problem is not None:
        skip(name, problem or route_problem)
        return
    if not listener_ready:
        skip(name, "the gateway agent listener was not verified: left on LiteLLM")
        return
    # 3) mint the gateway key BEFORE the device moves, and check it is live.
    if store.mint_mirror(name, agent["llm_key"]) is None or not store.live_mirror(name, agent["llm_key"]):
        skip(name, "its gateway key could not be minted from its LiteLLM key row")
        return
    if await _set_connect(container, project, gw):
        report["repointed"].append({"agent": name, "connect": gw})
    else:
        skip(name, f"incus refused to set {DEVICE} connect to the gateway")


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
    """The agent listener port new deploys may target, or 0 (stay on LiteLLM).

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
    state (so tests and embedded apps never touch incus).
    """
    from tinyagentos import llm_gateway

    port = getattr(state, "llm_gateway_agent_port", None)
    if port is None:
        return None
    # Listener disabled (port 0) counts as OFF: agents go back to LiteLLM,
    # recognised by the default listener port.
    on = llm_gateway.enabled() and bool(port)
    identity = getattr(state, "llm_gateway_listener_identity", None)
    ready = (
        await wait_for_listener(port, identity=identity, attempts=_PROBE_ATTEMPTS, delay=_PROBE_DELAY)
        if on else False
    )
    state.llm_gateway_listener_ready = ready
    if on and not ready:
        logger.error(
            "llm gateway: this controller's agent listener was not verified on 127.0.0.1:%s; "
            "no agent is moved onto it (and any already there go back to LiteLLM)", port,
        )
    config = getattr(state, "config", None)
    agents = list(getattr(config, "agents", None) or [])
    if not agents:
        return None
    proxy = getattr(state, "llm_proxy", None)
    litellm_port = int(getattr(proxy, "port", None) or 7834)
    try:
        report = await reconcile_agents(
            agents=agents,
            data_dir=Path(state.data_dir),
            gateway_on=on,
            gateway_port=port or llm_gateway.DEFAULT_AGENT_PORT,
            litellm_port=litellm_port,
            listener_ready=ready,
            models_problem=llm_gateway_models_check(state),
        )
    except Exception:
        logger.exception("llm gateway cutover: reconcile failed; agents left where they were")
        return None
    state.llm_gateway_cutover_report = report
    direction = "gateway" if on else "LiteLLM"
    for item in report["repointed"]:
        where = "the gateway" if item["connect"] == _target(port or 0) else "LiteLLM"
        logger.warning("llm gateway cutover: %s now on %s (%s)%s", item["agent"], where, item["connect"],
                       f": {item['reason']}" if item.get("reason") else "")
    for item in report["skipped"]:
        logger.warning("llm gateway cutover: %s left as is: %s", item["agent"], item["reason"])
    logger.info(
        "llm gateway cutover (%s): %d repointed, %d unchanged, %d skipped",
        direction, len(report["repointed"]), len(report["unchanged"]), len(report["skipped"]),
    )
    return report
