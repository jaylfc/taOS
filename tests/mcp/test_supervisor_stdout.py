"""The MCP supervisor must drain the child's stdout, and the proxy must not lie.

Two defects, one subsystem:

* ``MCPSupervisor.start`` spawns with ``stdout=PIPE`` but only ever reads
  stderr.  Once the OS pipe buffer (64 KiB on Linux) fills, the child blocks in
  ``write()`` forever while ``get_status()`` keeps reporting ``running: True``
  — a hang indistinguishable from a working server.  For a stdio-transport MCP
  server stdout *is* the JSON-RPC channel, so this is the primary data path,
  not an edge case.
* ``proxy.call_tool`` returned ``{"ok": True, "result": "stub ..."}``, which is
  indistinguishable from a real result to any caller.

The quiet-server tests that already live in ``tests/test_mcp.py`` pass today;
only a chatty server exposes the deadlock, so these are deliberately chatty.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
import pytest_asyncio

from tinyagentos.mcp.proxy import call_tool
from tinyagentos.mcp.registry import MCPServerStore
from tinyagentos.mcp.supervisor import MCPSupervisor

# Well over the 64 KiB Linux pipe buffer: a child that gets no reader blocks
# after the first ~64 KiB and never reaches the sentinel.
STDOUT_BYTES = 1024 * 1024

# The sentinel is written to *stderr* after the stdout write completes.  stderr
# is drained today, so its arrival is a positive signal that the child got past
# its stdout write, rather than an inference from the absence of a crash.
SENTINEL = "DRAINED-OK"

# Generous enough that a slow CI box cannot fail it, short enough that a real
# deadlock is reported in seconds rather than at the 120 s pytest timeout.
DRAIN_TIMEOUT_S = 10.0


@pytest_asyncio.fixture
async def store(tmp_path: Path):
    s = MCPServerStore(tmp_path / "mcp.db")
    await s.init()
    yield s
    await s.close()


@pytest_asyncio.fixture
async def supervisor(store):
    sup = MCPSupervisor(store=store, catalog=None, notif_store=None)
    yield sup
    await sup.stop_all()


def _chatty_cmd(script: str) -> list[str]:
    """Unbuffered python -c, so the child's writes hit the pipe immediately."""
    return [sys.executable, "-u", "-c", script]


async def _wait_for_sentinel(supervisor: MCPSupervisor, server_id: str) -> bool:
    """Poll the log buffer for the stderr sentinel until DRAIN_TIMEOUT_S."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + DRAIN_TIMEOUT_S
    while loop.time() < deadline:
        if any(SENTINEL in e["line"] for e in supervisor.logs(server_id, limit=10_000)):
            return True
        await asyncio.sleep(0.05)
    return False


@pytest.mark.asyncio
async def test_chatty_stdout_does_not_deadlock(store, supervisor):
    """1 MiB of short lines on stdout must not wedge the child.

    Without a stdout reader the child fills the pipe buffer and blocks, so the
    stderr sentinel that follows the write never arrives — while ``get_status``
    still claims the server is running.
    """
    script = (
        "import sys\n"
        "line = 'x' * 63 + '\\n'\n"
        f"for _ in range({STDOUT_BYTES} // 64):\n"
        "    sys.stdout.write(line)\n"
        "sys.stdout.flush()\n"
        f"sys.stderr.write({SENTINEL!r} + '\\n')\n"
        "sys.stderr.flush()\n"
        "import time; time.sleep(30)\n"
    )
    await store.register_server(
        "chatty-srv", "1.0", "stdio", config={"cmd": _chatty_cmd(script)}
    )
    assert await supervisor.start("chatty-srv") is True

    drained = await _wait_for_sentinel(supervisor, "chatty-srv")
    status = supervisor.get_status("chatty-srv")
    assert drained, (
        f"child never got past its {STDOUT_BYTES}-byte stdout write within "
        f"{DRAIN_TIMEOUT_S}s — blocked on a full pipe buffer, while "
        f"get_status() reports {status}"
    )


@pytest.mark.asyncio
async def test_single_oversized_stdout_line_does_not_deadlock(store, supervisor):
    """One line larger than StreamReader's 64 KiB limit must still drain.

    ``async for line in reader`` raises ValueError once a line exceeds the
    stream limit, which aborts the drain task and re-opens exactly the deadlock
    a stdout reader is meant to close.  A JSON-RPC frame on a stdio-transport
    server is a single line and is routinely larger than the limit, so this is
    the shape of the real traffic.
    """
    script = (
        "import sys\n"
        f"sys.stdout.write('y' * {STDOUT_BYTES} + '\\n')\n"
        "sys.stdout.flush()\n"
        f"sys.stderr.write({SENTINEL!r} + '\\n')\n"
        "sys.stderr.flush()\n"
        "import time; time.sleep(30)\n"
    )
    await store.register_server(
        "jumbo-srv", "1.0", "stdio", config={"cmd": _chatty_cmd(script)}
    )
    assert await supervisor.start("jumbo-srv") is True

    drained = await _wait_for_sentinel(supervisor, "jumbo-srv")
    status = supervisor.get_status("jumbo-srv")
    assert drained, (
        f"child never got past its single {STDOUT_BYTES}-byte stdout line "
        f"within {DRAIN_TIMEOUT_S}s, while get_status() reports {status}"
    )


@pytest.mark.asyncio
async def test_stdout_lines_are_tagged_and_tailable(store, supervisor):
    """Drained stdout has to reach the log tail, tagged apart from stderr.

    The MCP app's Logs tab is specified as a live stdout/stderr tail, so
    draining stdout into a black hole would trade one defect for another.
    """
    script = (
        "import sys\n"
        "sys.stdout.write('hello-from-stdout\\n')\n"
        "sys.stdout.flush()\n"
        f"sys.stderr.write({SENTINEL!r} + '\\n')\n"
        "sys.stderr.flush()\n"
        "import time; time.sleep(30)\n"
    )
    await store.register_server(
        "tagged-srv", "1.0", "stdio", config={"cmd": _chatty_cmd(script)}
    )
    assert await supervisor.start("tagged-srv") is True
    assert await _wait_for_sentinel(supervisor, "tagged-srv")

    entries = supervisor.logs("tagged-srv", limit=10_000)
    stdout_lines = [e for e in entries if e.get("stream") == "stdout"]
    stderr_lines = [e for e in entries if e.get("stream") == "stderr"]
    assert any("hello-from-stdout" in e["line"] for e in stdout_lines), (
        f"stdout never reached the log tail: {entries}"
    )
    assert any(SENTINEL in e["line"] for e in stderr_lines), (
        f"stderr lost its stream tag: {entries}"
    )


@pytest.mark.asyncio
async def test_stop_cancels_the_stdout_drain(store, supervisor):
    """stop() must reap the stdout drain the way it already reaps stderr's.

    An un-cancelled drain task outlives the server it was reading for and is
    reported by the event loop as a pending task at shutdown.
    """
    script = (
        "import sys, time\n"
        "sys.stdout.write('tick\\n')\n"
        "sys.stdout.flush()\n"
        "time.sleep(30)\n"
    )
    await store.register_server(
        "reap-srv", "1.0", "stdio", config={"cmd": _chatty_cmd(script)}
    )
    assert await supervisor.start("reap-srv") is True
    sp = supervisor._processes["reap-srv"]
    stdout_task = sp.stdout_task
    assert stdout_task is not None, "start() never created a stdout drain task"

    assert await supervisor.stop("reap-srv") is True
    assert stdout_task.done(), "stop() left the stdout drain task running"


@pytest.mark.asyncio
async def test_proxy_call_does_not_return_success_shaped_stub(store, supervisor):
    """The proxy must report an explicit error, never a fake success.

    A caller cannot tell ``{"ok": True, "result": "stub ..."}`` from a real
    tool result, so a stub that reports success is worse than one that errors.
    """
    await store.register_server(
        "sleep-srv", "1.0", "stdio", config={"cmd": ["sleep", "infinity"]}
    )
    await store.add_attachment("sleep-srv", "all", None)

    result = await call_tool(
        supervisor=supervisor,
        store=store,
        agent_name="bot1",
        agent_groups=[],
        server_id="sleep-srv",
        tool="fetch_url",
        arguments={"url": "https://example.invalid"},
    )

    assert result.get("ok") is not True, (
        f"proxy returned a success-shaped result with no call made: {result}"
    )
    assert "error" in result, f"proxy returned neither a result nor an error: {result}"
    assert result.get("status") == 501, (
        f"an unwired transport must surface as 501 Not Implemented, got {result}"
    )
