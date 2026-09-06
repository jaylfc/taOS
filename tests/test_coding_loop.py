"""Tests for the Coding Studio tool-calling loop engine (#86).

The loop is model-agnostic: a scripted ``model_step`` stands in for the real
model glue, so we can exercise the drive/dispatch/guard logic deterministically.
"""
import pytest

from tinyagentos.agent_tools import coding_loop


def _scripted(steps):
    """Return a model_step that yields the given steps in order, then finals."""
    seq = list(steps)
    last_transcript = {}

    async def model_step(transcript):
        last_transcript["value"] = transcript
        if seq:
            return seq.pop(0)
        return {"type": "final", "text": "done"}

    model_step.last = last_transcript
    return model_step


@pytest.mark.asyncio
async def test_loop_writes_then_reads_then_finishes(tmp_path):
    """A two-tool plan runs against the workspace and the loop returns final."""
    steps = [
        {
            "type": "tool_calls",
            "calls": [
                {"id": "c1", "name": "write_file", "arguments": {"path": "a.txt", "content": "hi"}},
            ],
        },
        {
            "type": "tool_calls",
            "calls": [{"id": "c2", "name": "read_file", "arguments": {"path": "a.txt"}}],
        },
        {"type": "final", "text": "the file says hi"},
    ]
    out = await coding_loop.run_tool_loop(tmp_path, _scripted(steps))
    assert out["stopped"] == "final"
    assert out["final"] == "the file says hi"
    assert out["iterations"] == 3
    assert (tmp_path / "a.txt").read_text() == "hi"
    # The read result is in the transcript for the model to have consumed.
    tool_msgs = [m for m in out["transcript"] if m["role"] == "tool"]
    assert tool_msgs[-1]["result"] == {"ok": True, "result": "hi"}


@pytest.mark.asyncio
async def test_loop_immediate_final(tmp_path):
    out = await coding_loop.run_tool_loop(tmp_path, _scripted([{"type": "final", "text": "nothing to do"}]))
    assert out["iterations"] == 1
    assert out["final"] == "nothing to do"


@pytest.mark.asyncio
async def test_loop_tool_error_is_fed_back_not_raised(tmp_path):
    """A failing tool call surfaces as a soft error in the transcript; loop continues."""
    steps = [
        {
            "type": "tool_calls",
            "calls": [{"id": "c1", "name": "read_file", "arguments": {"path": "../escape"}}],
        },
        {"type": "final", "text": "recovered"},
    ]
    out = await coding_loop.run_tool_loop(tmp_path, _scripted(steps))
    assert out["final"] == "recovered"
    tool_msg = next(m for m in out["transcript"] if m["role"] == "tool")
    assert tool_msg["result"]["ok"] is False


@pytest.mark.asyncio
async def test_loop_respects_max_iterations(tmp_path):
    """A model that never finishes is stopped by the iteration guard."""
    # Always asks for a (harmless) list_dir, never finals.
    async def never_done(transcript):
        return {"type": "tool_calls", "calls": [{"id": "x", "name": "list_dir", "arguments": {}}]}

    out = await coding_loop.run_tool_loop(tmp_path, never_done, max_iterations=3)
    assert out["stopped"] == "max_iterations"
    assert out["final"] is None
    assert out["iterations"] == 3


@pytest.mark.asyncio
async def test_loop_unknown_step_shape_stops_safely(tmp_path):
    async def garbage(transcript):
        return {"type": "???"}

    out = await coding_loop.run_tool_loop(tmp_path, garbage)
    assert out["stopped"] == "final"
    assert out["final"] is None
    assert out["iterations"] == 1


@pytest.mark.asyncio
async def test_loop_passes_growing_transcript_to_model(tmp_path):
    steps = [
        {"type": "tool_calls", "calls": [{"id": "c1", "name": "list_dir", "arguments": {}}]},
        {"type": "final", "text": "ok"},
    ]
    ms = _scripted(steps)
    out = await coding_loop.run_tool_loop(tmp_path, ms, initial_transcript=[{"role": "user", "content": "go"}])
    # By the final step the model saw the user msg, the assistant tool_calls, and the tool result.
    seen_roles = [m["role"] for m in ms.last["value"]]
    assert seen_roles[0] == "user"
    assert "tool" in seen_roles
    assert out["final"] == "ok"


@pytest.mark.asyncio
async def test_loop_non_dict_step_stops_safely(tmp_path):
    """model_step returning None / a list must not crash the loop."""
    async def returns_none(transcript):
        return None

    out = await coding_loop.run_tool_loop(tmp_path, returns_none)
    assert out["stopped"] == "final"
    assert out["final"] is None
    assert out["iterations"] == 1


@pytest.mark.asyncio
async def test_loop_malformed_call_entry_is_soft_error(tmp_path):
    """A non-dict entry in calls surfaces as a soft error, loop continues."""
    steps = [
        {"type": "tool_calls", "calls": ["not-a-dict", None]},
        {"type": "final", "text": "kept going"},
    ]
    seq = list(steps)

    async def model_step(transcript):
        return seq.pop(0) if seq else {"type": "final", "text": "done"}

    out = await coding_loop.run_tool_loop(tmp_path, model_step)
    assert out["final"] == "kept going"
    tool_msgs = [m for m in out["transcript"] if m["role"] == "tool"]
    assert all(m["result"]["ok"] is False for m in tool_msgs)
    assert tool_msgs[0]["result"]["error"] == "malformed tool call"
