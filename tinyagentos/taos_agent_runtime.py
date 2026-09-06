"""taOS agent opencode server lifecycle helpers.

Manages the single host opencode server used exclusively by the taOS agent
chat endpoint.  The server is started lazily on first chat request and kept
alive for the process lifetime.  The persistent session id is stored on
app.state so opencode remembers conversation history across requests.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import secrets
from pathlib import Path

from tinyagentos.litellm_config import get_litellm_master_key
from tinyagentos.opencode_runtime import OpenCodeServer, OpenCodeServerConfig

logger = logging.getLogger(__name__)

TAOS_OPENCODE_PORT = 4188  # local-only port for the taOS agent opencode server

# Safe filesystem component for opencode home directories.  Agent ids and
# LiteLLM model names can contain '/' (openai/gpt-4o) and other characters
# unsafe for a path; this collapses them to a flat slug.  Must stay in sync
# with the mint-side validator in routes/agent_model_keys.py.
_SAFE_COMPONENT_RE = re.compile(r"[^A-Za-z0-9._-]")


def _safe_path_component(value: str) -> str:
    """Replace characters unsafe for a filesystem path component.

    A model id like ``openai/gpt-4o`` becomes ``openai_gpt-4o`` so the
    opencode home stays flat under data_dir.  Traversal payloads like
    ``../../x`` collapse to ``.._.._x`` — harmless without real slashes.

    Appends a short hex digest so two distinct inputs that happen to slugify
    to the same string (e.g. ``openai/gpt-4o`` and literal ``openai_gpt-4o``)
    do not share a home directory and cross-contaminate conversation history.
    """
    slug = _SAFE_COMPONENT_RE.sub("_", value)
    digest = hashlib.sha256(value.encode()).hexdigest()[:8]
    return f"{slug}-{digest}"


async def ensure_taos_opencode_server(app_state, model: str) -> OpenCodeServer:
    """Lazily create and start the taOS agent opencode server.

    Stores the server on ``app_state.taos_opencode_server``.  If the model
    changed since last start the old server is stopped and a new one is
    created so the LiteLLM provider config and key scope track the chosen model.

    The key is scoped to the full ``permitted_models`` set read from the
    ``taos_agent`` desktop_settings namespace (falls back to ``[model]``).

    If the server was created while LiteLLM was not yet ready (born degraded),
    it is torn down and rebuilt transparently on the next call once the proxy
    is running so callers never need to know about the race.

    Returns the running :class:`~tinyagentos.opencode_runtime.OpenCodeServer`.
    """
    lock = getattr(app_state, "taos_opencode_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        app_state.taos_opencode_lock = lock

    # The lock is held from the server-cache lookup through ensure_running():
    # the existing-server check and the start are separated by several awaits
    # (stopping other servers, prefs read, key mint), so without it two
    # concurrent requests both observe `existing is None` and both start a
    # server on the shared TAOS_OPENCODE_PORT.
    async with lock:
        return await _ensure_taos_opencode_server_locked(app_state, model)


async def _ensure_taos_opencode_server_locked(app_state, model: str) -> OpenCodeServer:
    # Generate a stable per-process password once.
    if not getattr(app_state, "taos_opencode_password", None):
        app_state.taos_opencode_password = secrets.token_hex(16)

    # Per-agent server cache: each consented agent_id (or LLM model, for the
    # desktop path) gets its OWN server keyed by `model`, so concurrent
    # requests for different agents do not churn a shared singleton and race.
    servers = getattr(app_state, "taos_opencode_servers", None)
    if servers is None:
        servers = {}
        app_state.taos_opencode_servers = servers
    sessions = getattr(app_state, "taos_opencode_sessions", None)
    if sessions is None:
        sessions = {}
        app_state.taos_opencode_sessions = sessions

    existing: OpenCodeServer | None = servers.get(model)
    born_degraded = getattr(app_state, "taos_opencode_born_degraded", None)
    if born_degraded is None:
        born_degraded = {}
        app_state.taos_opencode_born_degraded = born_degraded


    # Self-heal: if the cached server was born before LiteLLM was ready and
    # LiteLLM is now running, tear down the degraded server and fall through
    # to a fresh build so the key re-scope and model_ids are applied properly.
    if existing is not None and born_degraded.get(model, False):
        llm_proxy_check = getattr(app_state, "llm_proxy", None)
        if llm_proxy_check is not None and llm_proxy_check.is_running():
            logger.info(
                "taos_agent_runtime: LiteLLM now ready; rebuilding taOS opencode server "
                "for %s that was born degraded", model,
            )
            try:
                await existing.stop()
            except Exception:
                logger.debug("taos_agent_runtime: error stopping degraded server", exc_info=True)
            servers.pop(model, None)
            sessions.pop(model, None)
            born_degraded[model] = False
            existing = None

    if existing is None:
        # Stop-on-model-change: all per-model servers share TAOS_OPENCODE_PORT,
        # so only one can bind at a time. Stop any server for a different model
        # before starting the new one.  Home directories are NOT removed on
        # model switch — the per-model home is the conversation store, and
        # deleting it would discard history and force a multi-minute SQLite
        # migration on the next start (the 180s deadline at ensure_running
        # exists to absorb exactly that one-time migration).
        data_dir = getattr(app_state, "data_dir", None)
        for other_model, other_server in list(servers.items()):
            if other_model != model and other_server is not None:
                logger.info(
                    "taos_agent_runtime: model changed (%s -> %s); stopping opencode server for %s",
                    other_model, model, other_model,
                )
                try:
                    await other_server.stop()
                except Exception:
                    logger.debug("taos_agent_runtime: error stopping old server", exc_info=True)
                servers.pop(other_model, None)
                sessions.pop(other_model, None)
                born_degraded.pop(other_model, None)
        # Clear the legacy session id so the desktop chat path does not feed
        # a stale session from a now-stopped model to the new server.
        app_state.taos_opencode_session_id = None

        # Read the taos_agent prefs once: the permitted set (to scope the key)
        # and a persisted own-key (so we reuse it instead of re-minting).
        permitted_models: list[str] = [model]
        stored_key: str | None = None
        prefs: dict = {}
        desktop_settings = getattr(app_state, "desktop_settings", None)
        if desktop_settings is not None:
            try:
                prefs = await desktop_settings.get_preference("user", "taos_agent") or {}
                stored = prefs.get("permitted_models", [])
                if stored:
                    # Always ensure the current model is in the set.
                    permitted_models = list(stored)
                    if model not in permitted_models:
                        permitted_models = [model, *permitted_models]
                stored_key = prefs.get("llm_key") or None
            except Exception:
                logger.debug("taos_agent_runtime: could not read taos_agent prefs", exc_info=True)

        # The taOS agent's own LiteLLM virtual key. Reuse the persisted one
        # (re-scoping it to the current permitted set), else mint once and persist
        # it. create_agent_key uses a fixed alias, so re-minting would 400 on the
        # alias collision — persisting the value avoids that and keeps it stable.
        llm_proxy = getattr(app_state, "llm_proxy", None)
        litellm_key: str | None = None
        born_degraded_now = False
        if llm_proxy is None or not llm_proxy.is_running():
            born_degraded_now = True
        if stored_key:
            litellm_key = stored_key
            if llm_proxy is not None:
                try:
                    rescoped = await llm_proxy.update_agent_key(stored_key, permitted_models)
                    if not rescoped:
                        logger.warning(
                            "taos_agent_runtime: re-scoping the taOS agent key returned False "
                            "(key scope may be stale)"
                        )
                except Exception:
                    logger.debug("taos_agent_runtime: re-scoping stored key failed", exc_info=True)
        elif llm_proxy is not None:
            try:
                litellm_key = await llm_proxy.create_agent_key("taos-agent", models=permitted_models)
            except Exception:
                logger.debug("taos_agent_runtime: create_agent_key failed", exc_info=True)
            if litellm_key and desktop_settings is not None:
                try:
                    prefs["llm_key"] = litellm_key
                    await desktop_settings.save_preference("user", "taos_agent", prefs)
                except Exception:
                    logger.debug("taos_agent_runtime: persisting key failed", exc_info=True)
        if not litellm_key:
            litellm_key = get_litellm_master_key(getattr(app_state, "data_dir", None))
        app_state.taos_opencode_key = litellm_key

        safe_model = _safe_path_component(model)
        home = str(data_dir / f"taos-agent-opencode-{safe_model}") if data_dir else f"taos-agent-opencode-{safe_model}"

        cfg = OpenCodeServerConfig(
            home=home,
            port=TAOS_OPENCODE_PORT,
            server_password=app_state.taos_opencode_password,
            litellm_base_url=f"http://127.0.0.1:{llm_proxy.port if llm_proxy is not None else 7834}/v1",
            litellm_key=litellm_key,
            model_ids=permitted_models,
        )
        server = OpenCodeServer(cfg)
        servers[model] = server
        born_degraded[model] = born_degraded_now
        if model not in sessions:
            sessions[model] = None

    server = servers[model]
    session_id = sessions.get(model)
    # Expose the chosen server's session on the legacy singleton attr so
    # the desktop chat path (taos_agent.py) can read it without change.
    # Must be unconditional: a model with no cached session (None) must
    # clear a stale value left by a previous model.
    app_state.taos_opencode_session_id = session_id
    # Generous deadline: opencode's first run on a fresh home performs a one-time
    # SQLite migration that can take a couple of minutes; a short deadline would
    # spuriously time out the very first taOS-agent chat.
    await server.ensure_running(deadline_s=180.0)
    return server


async def stop_taos_opencode_server(app_state) -> None:
    """Stop all per-agent taOS opencode servers if they were started.

    Safe to call even if no server was ever created. Iterates the per-agent
    cache (taos_opencode_servers) added for concurrent agent support.

    Home directories are NOT removed here — this runs on every ordinary app
    shutdown; deleting homes would discard conversation history and force a
    multi-minute SQLite migration on the next start.  Homes are likewise NOT
    removed on model switch for the same reason: the per-model home is the
    conversation store, and the switch path stops the server but keeps the home.
    """
    servers = getattr(app_state, "taos_opencode_servers", None)
    if not servers:
        return
    for _model, server in list(servers.items()):
        if server is None:
            continue
        try:
            await server.stop()
        except Exception:
            logger.debug("taos_agent_runtime: error during stop", exc_info=True)
    app_state.taos_opencode_servers = {}
    app_state.taos_opencode_sessions = {}
    app_state.taos_opencode_born_degraded = {}
    app_state.taos_opencode_session_id = None