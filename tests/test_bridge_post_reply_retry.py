"""Retry behaviour of every bridge's embedded post_reply.

Each bridge ships its reply POST inside a shell heredoc. This module extracts
that Python the same way tests/test_deer_flow_bridge.py loads the deer-flow
bridge and asserts the retry contract:

  * 5xx / 429 -> retry up to 3 attempts total, with a 1.0s backoff between
    attempts, until a 2xx lands.
  * other 4xx (e.g. 400) -> no retry, treated as failure.
  * httpx.TransportError -> retried; exhausting all attempts fails.

All nine bridges return True on a 2xx reply and False on every failure
path (non-retryable 4xx, exhausted retries, TransportError, other
Exception). The compact variants (pocketflow, smolagents,
openai-agents-sdk, openai_agents_sdk) used to end both except blocks
with a bare return (None); this module now asserts the bool contract
for every bridge.

Additional tests:
  * deer-flow: back-pressure at _PENDING_CAP waits for a slot instead of
    dropping user_message.
  * all bridges: non-TransportError exceptions from c.post are caught, logged
    as 'reply POST failed', and return False.
"""

import asyncio
import json
import os
import re
import tempfile
import py_compile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "tinyagentos" / "scripts"

BRIDGES = [
    "install_hermes.sh",
    "install_nanobot.sh",
    "install_thclaws.sh",
    "install_langroid.sh",
    "install_openai-agents-sdk.sh",
    "install_openai_agents_sdk.sh",
    "install_pocketflow.sh",
    "install_smolagents.sh",
    "install_deer-flow.sh",
]


def _extract(script: str) -> str:
    text = (SCRIPTS / script).read_text()
    m = re.search(r"<<'BRIDGE_EOF'\n(.*?)BRIDGE_EOF", text, re.DOTALL)
    if not m:
        raise RuntimeError(f"no BRIDGE_EOF heredoc in {script}")
    return m.group(1)


def _load(script: str):
    """Exec the bridge Python (without running main) and return its globals."""
    code = _extract(script)
    m = re.search(r'\nif __name__\s*==\s*["\']__main__["\']', code)
    if m:
        code = code[: m.start()]
    # Compact bridges end with a bare asyncio.run(main()) rather than a guard.
    code = re.sub(r'\nasyncio\.run\(main\(\)\)\s*\Z', '', code)
    os.environ.setdefault("TAOS_BRIDGE_URL", "http://127.0.0.1:8989")
    os.environ.setdefault("TAOS_AGENT_NAME", "test-agent")
    os.environ.setdefault("TAOS_LOCAL_TOKEN", "test-token")
    ns: dict = {}
    exec(code, ns)
    return ns


def _ok(status=200):
    return MagicMock(status_code=status, text="")


@pytest.fixture(params=BRIDGES, ids=lambda s: s.replace(".sh", ""))
def bridge(request):
    return _load(request.param)


# -- compilation ---------------------------------------------------------------

@pytest.mark.parametrize("script", BRIDGES, ids=lambda s: s.replace(".sh", ""))
def test_bridge_python_compiles(script):
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir) / "bridge.py"
        tmp.write_text(_extract(script))
        try:
            py_compile.compile(tmp, cfile=Path(tmpdir) / "bridge.pyc", doraise=True)
        finally:
            tmp.unlink()


# -- retry contract ------------------------------------------------------------

class TestPostReplyRetry:
    def test_503_then_200_retries_and_succeeds(self, bridge):
        post_reply = bridge["post_reply"]
        client = MagicMock()
        client.post = AsyncMock(side_effect=[_ok(503), _ok(200)])

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = asyncio.run(post_reply(client, "http://x/reply", "tok", "m1", "t1", "hi"))

        assert client.post.call_count == 2
        assert mock_sleep.call_count == 1
        assert mock_sleep.call_args_list[0].args[0] == 1.0
        for call in client.post.call_args_list:
            assert call.args[0] == "http://x/reply"
            assert call.kwargs["headers"]["Authorization"] == "Bearer tok"
        assert result is True

    def test_400_is_not_retried(self, bridge):
        post_reply = bridge["post_reply"]
        client = MagicMock()
        client.post = AsyncMock(return_value=_ok(400))

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = asyncio.run(post_reply(client, "http://x/reply", "tok", "m1", "t1", "hi"))

        assert client.post.call_count == 1
        mock_sleep.assert_not_called()
        assert result is False

    def test_503_exhausts_retries(self, bridge):
        """All-503 responses: retries are exhausted and post_reply returns False."""
        post_reply = bridge["post_reply"]
        client = MagicMock()
        client.post = AsyncMock(return_value=_ok(503))

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = asyncio.run(post_reply(client, "http://x/reply", "tok", "m1", "t1", "hi"))

        assert client.post.call_count == 3
        assert result is False

    def test_transport_error_exhausts_retries(self, bridge):
        post_reply = bridge["post_reply"]
        client = MagicMock()
        client.post = AsyncMock(side_effect=httpx.TransportError("boom"))

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = asyncio.run(post_reply(client, "http://x/reply", "tok", "m1", "t1", "hi"))

        assert client.post.call_count == 3
        assert result is False

    def test_429_is_retried(self, bridge):
        post_reply = bridge["post_reply"]
        client = MagicMock()
        client.post = AsyncMock(side_effect=[_ok(429), _ok(200)])

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = asyncio.run(post_reply(client, "http://x/reply", "tok", "m1", "t1", "hi"))

        assert client.post.call_count == 2
        assert mock_sleep.call_count == 1
        assert mock_sleep.call_args_list[0].args[0] == 1.0
        assert result is True

    def test_429_exhausts_retries(self, bridge):
        """All-429 responses: retries are exhausted and post_reply returns False."""
        post_reply = bridge["post_reply"]
        client = MagicMock()
        client.post = AsyncMock(return_value=_ok(429))

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = asyncio.run(post_reply(client, "http://x/reply", "tok", "m1", "t1", "hi"))

        assert client.post.call_count == 3
        assert result is False


# -- deer-flow back-pressure ----------------------------------------------------


class _FakeResp:
    """HTTP response stand-in whose aiter_lines() yields the given SSE lines."""

    status_code = 200

    def __init__(self, lines):
        self._lines = lines

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeStream:
    """Async context manager returned by _FakeClient.stream()."""

    def __init__(self, lines, stop=None, stop_on_exit=False):
        self._lines = lines
        self._stop = stop
        self._stop_on_exit = stop_on_exit

    async def __aenter__(self):
        return _FakeResp(self._lines)

    async def __aexit__(self, *args):
        if self._stop_on_exit and self._stop is not None:
            self._stop.set()
        return False


class _FakeClient:
    """Minimal httpx.AsyncClient stand-in exposing only stream()."""

    def __init__(self, lines, stop=None, stop_on_exit=False):
        self._lines = lines
        self._stop = stop
        self._stop_on_exit = stop_on_exit

    def stream(self, method, url, headers=None, timeout=None):
        return _FakeStream(self._lines, self._stop, self._stop_on_exit)


class TestDeerFlowBackPressure:
    """Tests driving the real sse_loop loaded from install_deer-flow.sh."""

    def _make_channel(self):
        return {
            "events_url": "http://test/events",
            "reply_url": "http://test/reply",
            "auth_bearer": "test-token",
        }

    def _make_evt(self, msg_id, text="hello"):
        return {
            "id": msg_id,
            "trace_id": msg_id,
            "text": text,
            "force_respond": False,
            "context": [],
            "attachments": [],
            "channel_id": "ch1",
        }

    def _sse_lines(self, count):
        lines = []
        for i in range(count):
            evt = self._make_evt(f"msg-{i}", f"text {i}")
            lines.append("event: user_message")
            lines.append("data: " + json.dumps(evt))
            lines.append("")
        return lines

    def test_pending_cap_backpressure_waits_for_slot(self):
        """Test A: real sse_loop handles every one of cap+1 user_messages,
        waiting for a free slot at the cap instead of dropping."""
        ns = _load("install_deer-flow.sh")
        cap = ns["_MAX_INFLIGHT"] * 4
        handled = []

        async def fake_limited(client, evt, channel, seen_ids, error_until):
            handled.append(evt["id"])
            await asyncio.sleep(0.05)
            return True

        ns["_run_handle_user_message_limited"] = fake_limited

        lines = self._sse_lines(cap + 1)
        channel = self._make_channel()

        async def run():
            stop = asyncio.Event()
            client = _FakeClient(lines, stop=stop, stop_on_exit=True)
            await ns["sse_loop"](client, channel, stop)

        asyncio.run(run())

        assert len(handled) == cap + 1
        assert set(handled) == {f"msg-{i}" for i in range(cap + 1)}

    def test_pending_cap_shutdown_breaks_wait(self):
        """Test B: sse_loop returns inside the stop timeout when shutdown begins
        while waiting for a free slot at the cap. The unfixed code ignored stop
        in asyncio.wait and missed the unit's TimeoutStopSec=75."""
        ns = _load("install_deer-flow.sh")
        cap = ns["_MAX_INFLIGHT"] * 4
        ns["_DRAIN_TIMEOUT_S"] = 0.1
        ns["RECONNECT_DELAY"] = 0

        lines = self._sse_lines(cap + 1)
        channel = self._make_channel()

        async def run():
            stop = asyncio.Event()
            release = asyncio.Event()

            async def fake_limited(client, evt, channel, seen_ids, error_until):
                await release.wait()
                return True

            ns["_run_handle_user_message_limited"] = fake_limited

            async def set_stop_later():
                await asyncio.sleep(0.2)
                stop.set()

            asyncio.create_task(set_stop_later())
            client = _FakeClient(lines)
            await ns["sse_loop"](client, channel, stop)

        asyncio.run(asyncio.wait_for(run(), 2))


# -- non-TransportError exception handling --------------------------------------


class TestPostReplyNonTransportError:
    """Tests for post_reply handling of non-TransportError exceptions."""

    @pytest.mark.parametrize("script", BRIDGES, ids=lambda s: s.replace(".sh", ""))
    def test_post_reply_catches_runtime_error(self, script, caplog):
        """c.post raising RuntimeError -> post_reply returns False and logs 'reply POST failed'."""
        ns = _load(script)
        post_reply = ns["post_reply"]

        client = MagicMock()
        client.post = AsyncMock(side_effect=RuntimeError("client closed"))

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = asyncio.run(post_reply(client, "http://x/reply", "tok", "m1", "t1", "hi"))

        # Should return False and log 'reply POST failed'
        assert result is False

        # Should log 'reply POST failed'
        fail_logs = [r for r in caplog.records if "reply POST failed" in r.getMessage()]
        assert len(fail_logs) >= 1, f"Expected 'reply POST failed' log, got: {[r.getMessage() for r in caplog.records]}"

    @pytest.mark.parametrize("script", BRIDGES, ids=lambda s: s.replace(".sh", ""))
    def test_post_reply_catches_generic_exception(self, script, caplog):
        """c.post raising generic Exception -> post_reply returns False and logs 'reply POST failed'."""
        ns = _load(script)
        post_reply = ns["post_reply"]

        client = MagicMock()
        client.post = AsyncMock(side_effect=Exception("generic error"))

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = asyncio.run(post_reply(client, "http://x/reply", "tok", "m1", "t1", "hi"))

        assert result is False

        fail_logs = [r for r in caplog.records if "reply POST failed" in r.getMessage()]
        assert len(fail_logs) >= 1, f"Expected 'reply POST failed' log, got: {[r.getMessage() for r in caplog.records]}"