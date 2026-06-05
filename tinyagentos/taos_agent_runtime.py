"""taOS agent opencode server lifecycle helpers.

Manages the single host opencode server used exclusively by the taOS assistant
chat endpoint.  The server is started lazily on first chat request and kept
alive for the process lifetime.  The persistent session id is stored on
app.state so opencode remembers conversation history across requests.
"""
from __future__ import annotations

import logging
import secrets

from tinyagentos.llm_proxy import TAOS_LITELLM_MASTER_KEY
from tinyagentos.opencode_runtime import OpenCodeServer, OpenCodeServerConfig

logger = logging.getLogger(__name__)

TAOS_OPENCODE_PORT = 4188  # local-only port for the taOS agent opencode server


async def ensure_taos_opencode_server(app_state, model: str) -> OpenCodeServer:
    """Lazily create and start the taOS agent opencode server.

    Stores the server on ``app_state.taos_opencode_server``.  If the model
    changed since last start the old server is stopped and a new one is
    created so the LiteLLM provider config and key scope track the chosen model.

    Returns the running :class:`~tinyagentos.opencode_runtime.OpenCodeServer`.
    """
    # Generate a stable per-process password once.
    if not getattr(app_state, "taos_opencode_password", None):
        app_state.taos_opencode_password = secrets.token_hex(16)

    existing: OpenCodeServer | None = getattr(app_state, "taos_opencode_server", None)
    existing_model: str | None = getattr(app_state, "taos_opencode_model", None)

    if existing is not None and existing_model != model:
        # Model changed — stop old server so it picks up the new config.
        logger.info(
            "taos_agent_runtime: model changed (%s → %s); restarting opencode server",
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
        # Mint the taOS agent's own LiteLLM virtual key.
        llm_proxy = getattr(app_state, "llm_proxy", None)
        litellm_key: str | None = None
        if llm_proxy is not None:
            try:
                litellm_key = await llm_proxy.create_agent_key("taos-agent", models=[model])
            except Exception:
                logger.debug("taos_agent_runtime: create_agent_key failed", exc_info=True)
        if not litellm_key:
            litellm_key = TAOS_LITELLM_MASTER_KEY
        app_state.taos_opencode_key = litellm_key

        data_dir = getattr(app_state, "data_dir", None)
        home = str(data_dir / "taos-agent-opencode") if data_dir else "taos-agent-opencode"

        cfg = OpenCodeServerConfig(
            home=home,
            port=TAOS_OPENCODE_PORT,
            server_password=app_state.taos_opencode_password,
            litellm_base_url="http://127.0.0.1:4000/v1",
            litellm_key=litellm_key,
            model_ids=[model],
        )
        server = OpenCodeServer(cfg)
        app_state.taos_opencode_server = server
        app_state.taos_opencode_model = model
        if not hasattr(app_state, "taos_opencode_session_id"):
            app_state.taos_opencode_session_id = None

    server = app_state.taos_opencode_server
    await server.ensure_running()
    return server


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
