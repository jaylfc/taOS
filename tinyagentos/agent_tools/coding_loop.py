"""The Coding Studio agent tool-calling loop (#86).

This is the loop the coding epic is named for. It sits on top of the dispatch
step (coding_tools.dispatch) and is deliberately model-agnostic: it drives any
``model_step`` callable that, given the running transcript, either asks for tool
calls or returns a final answer. The model glue (Anthropic / litellm / a local
model) provides ``model_step`` in a later slice; the loop, the tool execution,
and the iteration guard live here so they are testable without a live model.

Flow per turn:
  model_step(transcript) -> {"type": "tool_calls", "calls": [...]} or
                            {"type": "final", "text": "..."}
  each call -> coding_tools.dispatch(workspace_root, name, arguments)
            -> appended to the transcript as a tool_result
  repeat until the model returns a final answer or max_iterations is reached.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from tinyagentos.agent_tools import coding_tools

# A model_step is an async callable: (transcript) -> step dict.
ModelStep = Callable[[list[dict]], Awaitable[dict]]


async def run_tool_loop(
    workspace_root,
    model_step: ModelStep,
    *,
    initial_transcript: list[dict] | None = None,
    max_iterations: int = 8,
) -> dict[str, Any]:
    """Drive a tool-calling loop to completion against one workspace.

    Returns:
      {
        "final": <str | None>,        # the model's final answer, or None if the
                                      #   iteration guard tripped first
        "iterations": <int>,          # model_step turns taken
        "stopped": <"final"|"max_iterations">,
        "transcript": [...],          # full message list, including tool results
      }

    The transcript grows with two message shapes the model glue produces/reads:
      {"role": "assistant", "tool_calls": [{"id","name","arguments"}, ...]}
      {"role": "tool", "tool_call_id": <id>, "name": <name>, "result": <dispatch dict>}
    A final answer is recorded as {"role": "assistant", "content": <text>}.
    """
    transcript: list[dict] = list(initial_transcript or [])
    iterations = 0

    while iterations < max_iterations:
        iterations += 1
        step = await model_step(transcript)
        # A misbehaving model_step that returns a non-mapping must not crash the
        # loop (its whole contract is to stay resilient); treat it as a safe stop.
        kind = step.get("type") if isinstance(step, dict) else None

        if kind == "final":
            text = step.get("text", "")
            transcript.append({"role": "assistant", "content": text})
            return {
                "final": text,
                "iterations": iterations,
                "stopped": "final",
                "transcript": transcript,
            }

        if kind == "tool_calls":
            calls = step.get("calls") or []
            transcript.append({"role": "assistant", "tool_calls": calls})
            for call in calls:
                # A non-dict call entry (bare string, None) must surface as a soft
                # error, not crash the loop via .get on a non-mapping.
                if not isinstance(call, dict):
                    transcript.append(
                        {
                            "role": "tool",
                            "tool_call_id": None,
                            "name": None,
                            "result": {"ok": False, "error": "malformed tool call"},
                        }
                    )
                    continue
                result = coding_tools.dispatch(
                    workspace_root, call.get("name"), call.get("arguments")
                )
                transcript.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id"),
                        "name": call.get("name"),
                        "result": result,
                    }
                )
            continue

        # An unrecognised step shape is treated as a final, empty answer rather
        # than looping forever on a misbehaving model_step.
        transcript.append({"role": "assistant", "content": ""})
        return {
            "final": None,
            "iterations": iterations,
            "stopped": "final",
            "transcript": transcript,
        }

    # Iteration guard tripped: the model never produced a final answer.
    return {
        "final": None,
        "iterations": iterations,
        "stopped": "max_iterations",
        "transcript": transcript,
    }
