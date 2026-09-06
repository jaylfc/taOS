"""Per-agent worker failure handling.

Three-layer retry + fallback strategy:

1. Transient retry (via tinyagentos.clients.retry.with_retry): 100ms,
   300ms, 900ms, 2700ms, then give up.
2. Heartbeat-recovery grace: if the retry wrapper gives up and the worker
   is actually unreachable, wait up to HEARTBEAT_GRACE_SECS for the worker
   to send a fresh heartbeat.  If it recovers, retry the call once more.
3. Fallback chain walk: if the worker is confirmed down, walk the agent's
   fallback_models list in order, repeating steps 1 and 2 for each.

Policy modifiers on the agent manifest control which steps run:
- "pause"               -- skips step 3, goes straight to pause+notify.
- "fallback"            -- full pipeline (default).
- "escalate-immediately"-- skips steps 2 and 3, pauses immediately after
                           step 1 fails.

When everything fails the agent is marked paused (agent["paused"] = True)
and a notification is emitted with type "agent.paused" so the frontend can
surface action buttons.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Awaitable, Any

logger = logging.getLogger(__name__)

# How long to wait for a fresh heartbeat before declaring the worker down.
HEARTBEAT_GRACE_SECS = 30
# How often to poll for a new heartbeat during the grace window.
HEARTBEAT_POLL_INTERVAL = 1.0


class WorkerUnavailableError(Exception):
    """Raised when a worker is unreachable and no fallback succeeded."""


class AgentPausedError(Exception):
    """Raised when an agent is currently paused."""


# Type alias: a call factory is a zero-arg async callable that returns a
# coroutine performing the actual inference call.
CallFactory = Callable[[], Awaitable[Any]]


async def _wait_for_heartbeat(
    worker_name: str,
    cluster_manager,
    grace_secs: float = HEARTBEAT_GRACE_SECS,
    poll_interval: float = HEARTBEAT_POLL_INTERVAL,
) -> bool:
    """Poll cluster_manager until worker_name sends a fresh heartbeat.

    Returns True if the worker came back online within grace_secs, False
    otherwise.  The "fresh" threshold is that last_heartbeat advanced past
    the timestamp we recorded when we entered the grace window.
    """
    worker = cluster_manager.get_worker(worker_name)
    if worker is None:
        return False

    baseline = worker.last_heartbeat
    deadline = time.monotonic() + grace_secs

    while time.monotonic() < deadline:
        await asyncio.sleep(poll_interval)
        worker = cluster_manager.get_worker(worker_name)
        if worker is None:
            return False
        if worker.last_heartbeat > baseline and worker.status == "online":
            logger.info(
                "failure_handler: worker '%s' recovered (new heartbeat at %.1f)",
                worker_name, worker.last_heartbeat,
            )
            return True

    return False


async def _try_call_with_grace(
    call_factory: CallFactory,
    worker_name: str,
    cluster_manager,
    grace_secs: float = HEARTBEAT_GRACE_SECS,
) -> Any:
    """Run call_factory() with retry; if retry is exhausted wait for the
    heartbeat-recovery grace window and try once more.

    Raises WorkerUnavailableError if both the retry run and the post-grace
    retry fail, or if the worker does not recover within the grace window.
    """
    from tinyagentos.clients.retry import with_retry

    last_exc: Exception | None = None

    # Step 1: transient retry wrapper
    try:
        return await with_retry(call_factory)
    except Exception as exc:
        last_exc = exc
        logger.warning(
            "failure_handler: retry exhausted for worker '%s': %s",
            worker_name, exc,
        )

    # Step 2: heartbeat-recovery grace
    recovered = await _wait_for_heartbeat(worker_name, cluster_manager, grace_secs)
    if not recovered:
        raise WorkerUnavailableError(
            f"worker '{worker_name}' did not recover within {grace_secs}s grace window"
        ) from last_exc

    # One more attempt after recovery
    try:
        return await with_retry(call_factory)
    except Exception as exc:
        raise WorkerUnavailableError(
            f"worker '{worker_name}' recovered but call still failed: {exc}"
        ) from exc


async def handle_call(
    agent_name: str,
    primary_call: CallFactory,
    fallback_calls: list[tuple[str, CallFactory]],
    policy: str,
    cluster_manager,
    notif_store,
    config,
    *,
    primary_worker: str = "unknown",
    grace_secs: float = HEARTBEAT_GRACE_SECS,
) -> Any:
    """Dispatch an inference call for agent_name with full failure handling.

    Parameters
    ----------
    agent_name:
        Name of the agent this call belongs to.
    primary_call:
        Zero-arg callable returning the primary inference coroutine.
    fallback_calls:
        Ordered list of (model_name, call_factory) pairs.  Only consulted
        when policy is "fallback".
    policy:
        One of "pause", "fallback", "escalate-immediately".
    cluster_manager:
        ClusterManager instance, used for heartbeat-grace polling.
    notif_store:
        NotificationStore instance, used to emit the pause notification.
    config:
        AppConfig instance, used to mark the agent as paused in-process.
    primary_worker:
        Name of the worker serving the primary model.  Included in the
        notification payload.
    grace_secs:
        Override the heartbeat grace window duration (useful in tests).

    Returns
    -------
    Whatever the successful call factory returns.

    Raises
    ------
    AgentPausedError
        If the agent is already paused.
    WorkerUnavailableError
        If every attempt (primary + fallbacks) failed and the agent has
        been paused.
    """
    # Check paused state first
    agent = _find_agent(config, agent_name)
    if agent and agent.get("paused"):
        raise AgentPausedError(
            f"agent '{agent_name}' is paused, resume it before submitting calls"
        )

    # "escalate-immediately": skip grace window and fallback chain entirely,
    # just run step 1 (retry) and if it fails, pause immediately.
    if policy == "escalate-immediately":
        from tinyagentos.clients.retry import with_retry
        try:
            return await with_retry(primary_call)
        except Exception as exc:
            logger.error(
                "failure_handler: escalate-immediately, primary call failed for '%s': %s",
                agent_name, exc,
            )
            await _pause_and_notify(
                agent_name, primary_worker, config, notif_store
            )
            raise WorkerUnavailableError(
                f"escalate-immediately: primary call failed for '{agent_name}'"
            ) from exc

    # "pause": run steps 1+2, skip step 3.
    if policy == "pause":
        try:
            return await _try_call_with_grace(
                primary_call, primary_worker, cluster_manager, grace_secs
            )
        except WorkerUnavailableError as exc:
            logger.error(
                "failure_handler: pause policy, worker '%s' down for agent '%s': %s",
                primary_worker, agent_name, exc,
            )
            await _pause_and_notify(
                agent_name, primary_worker, config, notif_store
            )
            raise

    # "fallback" (default): run steps 1+2 on primary, then walk fallback chain.
    assert policy == "fallback", f"unknown policy: {policy!r}"

    try:
        return await _try_call_with_grace(
            primary_call, primary_worker, cluster_manager, grace_secs
        )
    except WorkerUnavailableError:
        logger.warning(
            "failure_handler: primary worker '%s' down for agent '%s', "
            "walking fallback chain (%d entries)",
            primary_worker, agent_name, len(fallback_calls),
        )

    # Step 3: walk fallback chain
    for fallback_model, fallback_factory in fallback_calls:
        logger.info(
            "failure_handler: trying fallback model '%s' for agent '%s'",
            fallback_model, agent_name,
        )
        try:
            return await _try_call_with_grace(
                fallback_factory, fallback_model, cluster_manager, grace_secs
            )
        except WorkerUnavailableError:
            logger.warning(
                "failure_handler: fallback model '%s' also failed for agent '%s'",
                fallback_model, agent_name,
            )
            continue

    # Every option exhausted
    await _pause_and_notify(agent_name, primary_worker, config, notif_store)
    raise WorkerUnavailableError(
        f"all workers and fallbacks exhausted for agent '{agent_name}'; agent paused"
    )


async def _pause_and_notify(
    agent_name: str,
    failing_worker: str,
    config,
    notif_store,
) -> None:
    """Mark the agent as paused and emit a notification."""
    agent = _find_agent(config, agent_name)
    if agent is not None:
        agent["paused"] = True
        logger.info("failure_handler: agent '%s' marked paused", agent_name)

    if notif_store is not None:
        try:
            await notif_store.add(
                title=f"Agent '{agent_name}' paused",
                message=(
                    f"Worker '{failing_worker}' is unreachable and all fallbacks failed. "
                    f"Agent '{agent_name}' has been paused.\n\n"
                    f"Actions: check-worker-health | pick-alternate-model | keep-paused\n"
                    f"agent:{agent_name} worker:{failing_worker}"
                ),
                level="error",
                source="agent.paused",
            )
        except Exception as exc:
            logger.warning(
                "failure_handler: could not emit pause notification: %s", exc
            )


def _find_agent(config, agent_name: str) -> dict | None:
    """Look up an agent dict by name from config.agents."""
    if config is None:
        return None
    for a in getattr(config, "agents", []):
        if a.get("name") == agent_name:
            return a
    return None
