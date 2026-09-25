"""taOS's in-process LLM gateway: the replacement for the LiteLLM proxy.

An OpenAI-compatible surface at ``/api/llm/v1`` served by the controller itself.
Deliberately NOT bare ``/v1``: on this controller ``/v1/chat/completions`` is
already Agent-as-a-Model (``routes/agent_model_api.py``), and one path with two
meanings is the trap this prefix avoids.

G1 (this package today): non-streaming chat completions and the model list,
OpenAI-compatible backends only, session / local-token callers only, mounted
only when ``TAOS_LLM_GATEWAY=1``. LiteLLM keeps running beside it, unchanged.

Modules, smallest first:
  errors   OpenAI-shaped error envelope + the exception the routes raise
  auth     ``gateway_caller``: the ONE auth / model-permission seam (G2 swaps its body)
  resolve  model name -> backend, from the SAME table the LiteLLM config uses
  forward  one POST to an OpenAI-compatible backend, failures mapped to 502
  anthropic Anthropic Messages API translator for the taOS gateway
  router   the two routes, and ``mount``
"""
from __future__ import annotations

import os

FLAG_ENV = "TAOS_LLM_GATEWAY"


def enabled() -> bool:
    """True only for ``TAOS_LLM_GATEWAY=1``; anything else leaves it unmounted."""
    return os.environ.get(FLAG_ENV) == "1"

# Export Anthropic translator
from tinyagentos.llm_gateway.anthropic import chat_completion_anthropic, chat_completion_stream_anthropic
