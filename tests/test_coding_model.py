"""Tests for the litellm-backed model_step adapter (#86).

A fake completion_fn stands in for litellm so the conversion + parsing logic is
exercised without a live model or network.
"""
import json

import pytest

from tinyagentos.agent_tools import coding_loop, coding_model


def test_to_openai_tools_shape():
    tools = coding_model.to_openai_tools()
    names = {t["function"]["name"] for t in tools}
    assert names == {"read_file", "write_file", "file_exists", "list_dir"}
    assert all(t["type"] == "function" for t in tools)
    # parameters carry the input_schema verbatim.
    read = next(t for t in tools if t["function"]["name"] == "read_file")
    assert read["function"]["parameters"]["required"] == ["path"]


def test_transcript_to_messages_maps_shapes():
    transcript = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "tool_calls": [{"id": "c1", "name": "list_dir", "arguments": {"path": "."}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "list_dir", "result": {"ok": True, "result": ["a"]}},
    ]
    msgs = coding_model.transcript_to_messages(transcript, system="be helpful")
    assert msgs[0] == {"role": "system", "content": "be helpful"}
    assert msgs[1]["content"] == "go"
    # assistant tool_calls become OpenAI function tool_calls with stringified args.
    tc = msgs[2]["tool_calls"][0]
    assert tc["function"]["name"] == "list_dir"
    assert json.loads(tc["function"]["arguments"]) == {"path": "."}
    # tool result is JSON-encoded into content.
    assert json.loads(msgs[3]["content"]) == {"ok": True, "result": ["a"]}


class _Obj:
    """Minimal object-style stand-in for a litellm response."""

    def __init__(self, d):
        self.__dict__.update(d)


def test_parse_completion_object_style_tool_call():
    resp = _Obj(
        {
            "choices": [
                _Obj(
                    {
                        "message": _Obj(
                            {
                                "content": None,
                                "tool_calls": [
                                    _Obj(
                                        {
                                            "id": "c1",
                                            "function": _Obj(
                                                {"name": "write_file", "arguments": '{"path":"a.txt","content":"hi"}'}
                                            ),
                                        }
                                    )
                                ],
                            }
                        )
                    }
                )
            ]
        }
    )
    step = coding_model.parse_completion(resp)
    assert step["type"] == "tool_calls"
    assert step["calls"][0] == {"id": "c1", "name": "write_file", "arguments": {"path": "a.txt", "content": "hi"}}


def test_parse_completion_dict_style_final():
    resp = {"choices": [{"message": {"content": "all done", "tool_calls": None}}]}
    assert coding_model.parse_completion(resp) == {"type": "final", "text": "all done"}


def test_parse_completion_tolerates_bad_arguments():
    resp = {"choices": [{"message": {"tool_calls": [{"id": "c1", "function": {"name": "list_dir", "arguments": "not-json"}}]}}]}
    step = coding_model.parse_completion(resp)
    assert step["calls"][0]["arguments"] == {}


@pytest.mark.asyncio
async def test_model_step_drives_loop_end_to_end(tmp_path):
    """A scripted completion_fn + the real loop writes then reads a file."""
    # Two model turns: first a write tool call, then a final answer.
    scripted = [
        {"choices": [{"message": {"tool_calls": [
            {"id": "c1", "function": {"name": "write_file", "arguments": '{"path":"hello.py","content":"print(1)"}'}}
        ]}}]},
        {"choices": [{"message": {"content": "wrote hello.py", "tool_calls": None}}]},
    ]
    seen = {"models": [], "tools": None}

    async def fake_completion(model, messages, tools):
        seen["models"].append(model)
        seen["tools"] = tools
        return scripted.pop(0)

    step = coding_model.make_litellm_model_step("openai/gpt-x", completion_fn=fake_completion)
    out = await coding_loop.run_tool_loop(tmp_path, step)

    assert out["stopped"] == "final"
    assert out["final"] == "wrote hello.py"
    assert (tmp_path / "hello.py").read_text() == "print(1)"
    # The model was actually called with the workspace tools.
    assert seen["models"] == ["openai/gpt-x", "openai/gpt-x"]
    assert {t["function"]["name"] for t in seen["tools"]} == {"read_file", "write_file", "file_exists", "list_dir"}


def test_parse_completion_empty_choices_is_safe_final():
    assert coding_model.parse_completion({"choices": []}) == {"type": "final", "text": ""}


def test_parse_completion_missing_choices_is_safe_final():
    assert coding_model.parse_completion({}) == {"type": "final", "text": ""}


def test_parse_completion_null_message_is_safe_final():
    assert coding_model.parse_completion({"choices": [{"message": None}]}) == {"type": "final", "text": ""}
