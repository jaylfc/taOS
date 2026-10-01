"""taOS's in-process LLM gateway: the replacement for the LiteLLM proxy.

An OpenAI-compatible surface at ``/api/llm/v1`` served by the controller itself.
Deliberately NOT bare ``/v1``: on this controller ``/v1/chat/completions`` is
already Agent-as-a-Model (``routes/agent_model_api.py``), and one path with two
meanings is the trap this prefix avoids.

ALWAYS ON. Since LiteLLM removal stage 2b-2a there is no LiteLLM process to
roll back to, so ``TAOS_LLM_GATEWAY=0`` (or ``false`` / ``no`` / ``off``) is a
logged no-op: the gateway serves models, chat completions AND embeddings.

Agents reach the gateway at their own ``127.0.0.1:4000`` exactly as before.
The incus proxy device behind that address is retargeted from the LiteLLM
host port to the agent listener (``listener``, host ``127.0.0.1:7838``),
which serves ``/v1/models``, ``/v1/chat/completions`` and ``/v1/embeddings``
from this package.

Modules, smallest first:
  errors   OpenAI-shaped error envelope + the exception the routes raise
  auth     ``gateway_caller``: the ONE auth / model-permission seam (G2 swaps its body)
  resolve  model name -> backend, from the SAME table the LiteLLM config uses
  forward  one POST to an OpenAI-compatible backend, failures mapped to 502
  anthropic Anthropic Messages API translator for the taOS gateway
  embeddings embeddings routing (probe-discovered table) + the backend call
  router   the three routes, and ``mount``
  listener the agent-facing ASGI app on its own host port (base URL ``/v1``)
  cutover  per-agent key mint + proxy-device repoint at startup
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

FLAG_ENV = "TAOS_LLM_GATEWAY"
_OFF_VALUES = frozenset({"0", "false", "no", "off"})

# Host port of the agent listener: the target of each agent container's
# ``taos-proxy-litellm`` device once it is on the gateway. Loopback only.
AGENT_PORT_ENV = "TAOS_LLM_GATEWAY_PORT"
DEFAULT_AGENT_PORT = 7838


_off_warned = False


def enabled() -> bool:
    """Always True: the gateway is the only LLM path (LiteLLM removal 2b-2a).

    ``TAOS_LLM_GATEWAY`` set to 0 / false / no / off used to roll agents back
    to LiteLLM. LiteLLM is gone, so the value is ignored and logged once.
    """
    global _off_warned
    if not _off_warned and os.environ.get(FLAG_ENV, "").strip().lower() in _OFF_VALUES:
        _off_warned = True
        logger.warning(
            "llm gateway: %s=%s is ignored; the gateway is always on since LiteLLM "
            "was removed (there is nothing to roll back to)",
            FLAG_ENV, os.environ.get(FLAG_ENV),
        )
    return True


def agent_port(config=None) -> int:
    """The agent listener's host port: env > ``server.llm_gateway_port`` > 7838.

    0 disables the listener, and then agents have no LLM path. Not 7837: that is the MLX backend."""
    raw = os.environ.get(AGENT_PORT_ENV)
    if raw is not None and raw.strip():
        return int(raw.strip())
    server = getattr(config, "server", None) or {}
    value = server.get("llm_gateway_port", DEFAULT_AGENT_PORT)
    return int(value) if value is not None else DEFAULT_AGENT_PORT

# Export Anthropic translator
from tinyagentos.llm_gateway.anthropic import chat_completion_anthropic, chat_completion_stream_anthropic
