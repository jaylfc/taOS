"""taOS agent opencode server lifecycle helpers.

Manages the single host opencode server used exclusively by the taOS agent
chat endpoint.  The server is started lazily on first chat request and kept
alive for the process lifetime.  The persistent session id is stored on
app.state so opencode remembers conversation history across requests.
"""
from __future__ import annotations

import logging
import secrets

from tinyagentos.litellm_config import get_litellm_master_key
from tinyagentos.opencode_runtime import OpenCodeServer, OpenCodeServerConfig

logger = logging.getLogger(__name__)

TAOS_OPENCODE_PORT = 4188  # local-only port for the taOS agent opencode server


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
    # Generate a stable per-process password once.
    if not getattr(app_state, "taos_opencode_password", None):
        app_state.taos_opencode_password = secrets.token_hex(16)

    existing: OpenCodeServer | None = getattr(app_state, "taos_opencode_server", None)
    existing_model: str | None = getattr(app_state, "taos_opencode_model", None)

    # Self-heal: if the cached server was born before LiteLLM was ready and
    # LiteLLM is now running, tear down the degraded server and fall through
    # to a fresh build so the key re-scope and model_ids are applied properly.
    if existing is not None and getattr(app_state, "taos_opencode_born_degraded", False):
        llm_proxy_check = getattr(app_state, "llm_proxy", None)
        if llm_proxy_check is not None and llm_proxy_check.is_running():
            logger.info(
                "taos_agent_runtime: LiteLLM now ready; rebuilding taOS opencode server "
                "that was born degraded"
            )
            try:
                await existing.stop()
            except Exception:
                logger.debug("taos_agent_runtime: error stopping degraded server", exc_info=True)
            app_state.taos_opencode_server = None
            app_state.taos_opencode_session_id = None
            app_state.taos_opencode_born_degraded = False
            existing = None

    if existing is not None and existing_model != model:
        # Model changed — stop old server so it picks up the new config.
        logger.info(
            "taos_agent_runtime: model changed (%s -> %s); restarting opencode server",
            existing_model, model,
        )
        try:
            await existing.stop()
        except Exception:
            logger.debug("taos_agent_runtime: error stopping old server", exc_info=True)
        app_state.taos_opencode_server = None
        app_state.taos_opencode_session_id = None
        existing = None

    if existing is None:
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
        born_degraded = False
        if llm_proxy is None or not llm_proxy.is_running():
            born_degraded = True
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
                        born_degraded = True
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

        data_dir = getattr(app_state, "data_dir", None)
        home = str(data_dir / "taos-agent-opencode") if data_dir else "taos-agent-opencode"

        cfg = OpenCodeServerConfig(
            home=home,
            port=TAOS_OPENCODE_PORT,
            server_password=app_state.taos_opencode_password,
            litellm_base_url=f"http://127.0.0.1:{llm_proxy.port if llm_proxy is not None else 7834}/v1",
            litellm_key=litellm_key,
            model_ids=permitted_models,
        )
        server = OpenCodeServer(cfg)
        app_state.taos_opencode_server = server
        app_state.taos_opencode_model = model
        app_state.taos_opencode_born_degraded = born_degraded
        if not hasattr(app_state, "taos_opencode_session_id"):
            app_state.taos_opencode_session_id = None

    server = app_state.taos_opencode_server
    # Generous deadline: opencode's first run on a fresh home performs a one-time
    # SQLite migration that can take a couple of minutes; a short deadline would
    # spuriously time out the very first taOS-agent chat.
    await server.ensure_running(deadline_s=180.0)
    return server


async def ensure_agent_opencode_server(
    app_state,
    agent_id: str,
    model: str,
    llm_key: str | None = None,
) -> OpenCodeServer:
    """Ensure an opencode server is running for the given agent.

    Per-agent version of :func:`ensure_taos_opencode_server`.  Each agent gets
    its own server with its own home directory, port, password, and LiteLLM
    key so conversation history, memory, and identity are isolated per agent.
    """
    if not hasattr(app_state, "opencode_servers"):
        app_state.opencode_servers = {}
    if not hasattr(app_state, "opencode_server_passwords"):
        app_state.opencode_server_passwords = {}
    if not hasattr(app_state, "opencode_server_keys"):
        app_state.opencode_server_keys = {}
    if not hasattr(app_state, "opencode_server_models"):
        app_state.opencode_server_models = {}
    if not hasattr(app_state, "opencode_server_session_ids"):
        app_state.opencode_server_session_ids = {}

    if agent_id not in app_state.opencode_server_passwords:
        app_state.opencode_server_passwords[agent_id] = secrets.token_hex(16)
    password = app_state.opencode_server_passwords[agent_id]

    existing = app_state.opencode_servers.get(agent_id)
    existing_model = app_state.opencode_server_models.get(agent_id)

    if existing is not None and existing.is_running() and existing_model == model:
        return existing

    if existing is not None:
        try:
            await existing.stop()
        except Exception:
            logger.debug("taos_agent_runtime: error stopping agent server", exc_info=True)

    if llm_key is None:
        llm_key = get_litellm_master_key(getattr(app_state, "data_dir", None))

    from tinyagentos.installers.port_allocator import allocate_host_port

    data_dir = getattr(app_state, "data_dir", None)
    home = str(data_dir / f"{agent_id}-opencode") if data_dir else f"{agent_id}-opencode"
    port = allocate_host_port(f"opencode-{agent_id}")

    llm_proxy = getattr(app_state, "llm_proxy", None)
    litellm_base_url = (
        f"http://127.0.0.1:{llm_proxy.port}/v1"
        if llm_proxy is not None
        else "http://127.0.0.1:7834/v1"
    )

    cfg = OpenCodeServerConfig(
        home=home,
        port=port,
        server_password=password,
        litellm_base_url=litellm_base_url,
        litellm_key=llm_key,
        model_ids=[model],
    )
    server = OpenCodeServer(cfg)
    app_state.opencode_servers[agent_id] = server
    app_state.opencode_server_models[agent_id] = model
    app_state.opencode_server_keys[agent_id] = llm_key
    app_state.opencode_server_session_ids[agent_id] = None

    await server.ensure_running(deadline_s=180.0)
    return server


async def stop_agent_opencode_server(app_state, agent_id: str) -> None:
    """Stop the opencode server for a single agent.

    Safe to call even if the server was never created.
    """
    servers = getattr(app_state, "opencode_servers", {})
    server = servers.get(agent_id)
    if server is None:
        return
    try:
        await server.stop()
    except Exception:
        logger.debug("taos_agent_runtime: error during agent server stop", exc_info=True)
    finally:
        servers.pop(agent_id, None)
        session_ids = getattr(app_state, "opencode_server_session_ids", {})
        session_ids.pop(agent_id, None)


async def stop_taos_opencode_server(app_state) -> None:
    """Stop the taOS agent opencode server if it was started.

    Safe to call even if the server was never created.
    """
    server = getattr(app_state, "taos_opencode_server", None)
    if server is None:
        return
    try:
        await server.stop()
    except Exception:
        logger.debug("taos_agent_runtime: error during stop", exc_info=True)
    finally:
        app_state.taos_opencode_server = None
        app_state.taos_opencode_session_id = None
