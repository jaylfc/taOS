"""A concrete ``model_step`` for the coding tool-calling loop (#86).

The loop engine (coding_loop.run_tool_loop) is model-agnostic; this module
supplies the real driver: it converts the loop transcript to OpenAI-style chat
messages, hands the model the workspace tools, calls the completion, and parses
the reply back into the loop's step shape ({"type": "tool_calls"|"final"}).

The completion call is injectable (``completion_fn``) so this is testable
without a live model or a network round trip; the default lazily imports
litellm so importing this module never requires litellm to be installed.
"""

from __future__ import annotations

import json
from typing import Any

from tinyagentos.agent_tools.coding_tools import TOOL_SCHEMAS


def to_openai_tools(schemas: list[dict[str, Any]] | None = None) -> list[dict]:
    """Convert the Anthropic-style tool schemas to OpenAI function-tool format."""
    out = []
    for s in schemas if schemas is not None else TOOL_SCHEMAS:
        out.append(
            {
                "type": "function",
                "function": {
                    "name": s["name"],
                    "description": s.get("description", ""),
                    "parameters": s.get("input_schema", {"type": "object", "properties": {}}),
                },
            }
        )
    return out


def transcript_to_messages(transcript: list[dict], system: str | None = None) -> list[dict]:
    """Render the loop transcript as OpenAI chat messages.

    Maps the loop's internal message shapes:
      {"role": "user"|"assistant", "content": ...}            -> passthrough
      {"role": "assistant", "tool_calls": [{id,name,arguments}]} -> assistant w/ tool_calls
      {"role": "tool", "tool_call_id", "name", "result": {...}}  -> tool message (JSON result)
    """
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    for m in transcript:
        role = m.get("role")
        if role == "assistant" and "tool_calls" in m:
            messages.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": c.get("id"),
                            "type": "function",
                            "function": {
                                "name": c.get("name"),
                                "arguments": json.dumps(c.get("arguments") or {}),
                            },
                        }
                        for c in m["tool_calls"]
                    ],
                }
            )
        elif role == "tool":
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": m.get("tool_call_id"),
                    "name": m.get("name"),
                    "content": json.dumps(m.get("result")),
                }
            )
        else:
            messages.append({"role": role, "content": m.get("content", "")})
    return messages


def _parse_arguments(raw) -> dict:
    """Tool-call arguments arrive as a JSON string from the model; tolerate dicts."""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _message_field(message, key):
    """Read a field off either an object-style or dict-style completion message."""
    if isinstance(message, dict):
        return message.get(key)
    return getattr(message, key, None)


def parse_completion(response) -> dict:
    """Turn a chat-completion response into the loop's step shape.

    A content-filtered or error-shaped response can come back with no choices or
    no message; fall back to an empty final answer rather than raising, so the
    loop's never-raise contract holds.
    """
    choices = (response.get("choices") if isinstance(response, dict) else getattr(response, "choices", None)) or []
    if not choices:
        return {"type": "final", "text": ""}
    first = choices[0]
    message = (first.get("message") if isinstance(first, dict) else getattr(first, "message", None))
    if message is None:
        return {"type": "final", "text": ""}
    tool_calls = _message_field(message, "tool_calls")
    if tool_calls:
        calls = []
        for tc in tool_calls:
            fn = tc["function"] if isinstance(tc, dict) else tc.function
            calls.append(
                {
                    "id": (tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)),
                    "name": (fn["name"] if isinstance(fn, dict) else fn.name),
                    "arguments": _parse_arguments(
                        fn["arguments"] if isinstance(fn, dict) else fn.arguments
                    ),
                }
            )
        return {"type": "tool_calls", "calls": calls}
    return {"type": "final", "text": _message_field(message, "content") or ""}


async def _default_completion(model: str, messages: list[dict], tools: list[dict]):
    # Lazy import so this module never hard-requires litellm at import time.
    import litellm

    return await litellm.acompletion(model=model, messages=messages, tools=tools)


def make_litellm_model_step(model: str, *, system: str | None = None, completion_fn=None):
    """Build a model_step for run_tool_loop backed by a chat-completions model.

    completion_fn(model, messages, tools) -> response is injectable (defaults to
    litellm.acompletion). The returned async callable takes the loop transcript
    and returns the next step.
    """
    call = completion_fn or _default_completion
    tools = to_openai_tools()

    async def model_step(transcript: list[dict]) -> dict:
        messages = transcript_to_messages(transcript, system=system)
        response = await call(model, messages, tools)
        return parse_completion(response)

    return model_step
