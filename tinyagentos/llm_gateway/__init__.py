"""taOS's in-process LLM gateway: the replacement for the LiteLLM proxy.

An OpenAI-compatible surface at ``/api/llm/v1`` served by the controller itself.
Deliberately NOT bare ``/v1``: on this controller ``/v1/chat/completions`` is
already Agent-as-a-Model (``routes/agent_model_api.py``), and one path with two
meanings is the trap this prefix avoids.

ON BY DEFAULT (cutover stage 1). ``TAOS_LLM_GATEWAY=0`` (or ``false`` /
``no`` / ``off``) turns it back off: the routes are not mounted and the
startup reconcile points every agent's proxy device back at LiteLLM. LiteLLM
still runs beside it in stage 1: the agent listener forwards every path the
gateway does not serve (embeddings, for one) to it.

Agents reach the gateway at their own ``127.0.0.1:4000`` exactly as before.
The incus proxy device behind that address is retargeted from the LiteLLM
host port to the agent listener (``listener``, host ``127.0.0.1:7838``),
which serves ``/v1/models`` and ``/v1/chat/completions`` from this package.

Modules, smallest first:
  errors   OpenAI-shaped error envelope + the exception the routes raise
  auth     ``gateway_caller``: the ONE auth / model-permission seam (G2 swaps its body)
  resolve  model name -> backend, from the SAME table the LiteLLM config uses
  forward  one POST to an OpenAI-compatible backend, failures mapped to 502
  anthropic Anthropic Messages API translator for the taOS gateway
  router   the two routes, and ``mount``
  listener the agent-facing ASGI app on its own host port (base URL ``/v1``)
  cutover  per-agent key mint + proxy-device repoint / rollback at startup
"""
from __future__ import annotations

import os

FLAG_ENV = "TAOS_LLM_GATEWAY"
_OFF_VALUES = frozenset({"0", "false", "no", "off"})

# Host port of the agent listener: the target of each agent container's
# ``taos-proxy-litellm`` device once it is on the gateway. Loopback only.
AGENT_PORT_ENV = "TAOS_LLM_GATEWAY_PORT"
DEFAULT_AGENT_PORT = 7838


def enabled() -> bool:
    """On unless ``TAOS_LLM_GATEWAY`` is explicitly 0 / false / no / off."""
    return os.environ.get(FLAG_ENV, "").strip().lower() not in _OFF_VALUES


def agent_port(config=None) -> int:
    """The agent listener's host port: env > ``server.llm_gateway_port`` > 7838.

    0 disables the listener (agents then stay on LiteLLM). Not 7837: that is the MLX backend."""
    raw = os.environ.get(AGENT_PORT_ENV)
    if raw is not None and raw.strip():
        return int(raw.strip())
    server = getattr(config, "server", None) or {}
    value = server.get("llm_gateway_port", DEFAULT_AGENT_PORT)
    return int(value) if value is not None else DEFAULT_AGENT_PORT

# Export Anthropic translator
from tinyagentos.llm_gateway.anthropic import chat_completion_anthropic, chat_completion_stream_anthropic
