"""Retry behaviour of every bridge's embedded post_reply.

Each bridge ships its reply POST inside a shell heredoc. This module extracts
that Python the same way tests/test_deer_flow_bridge.py loads the deer-flow
bridge and asserts the retry contract:

  * connection errors, 429 and 5xx -> retry with capped backoff
    (1, 2, 4, 8, 15 s steps, 15 s repeating) inside a total window of at
    least 180 s, so a reply survives a controller restart
    (TimeoutStartSec=180 in scripts/systemd/tinyagentos.service).
  * other 4xx (e.g. 400) -> no retry, treated as failure.
  * httpx.TransportError -> retried inside the window; exhausting the
    window fails.
  * any other exception -> no retry, treated as failure.
  * once a 2xx lands, post_reply stops: the reply is delivered exactly
    once and keeps its id so the controller's duplicate guard
    (tinyagentos/bridge_session.py _seen_final_ids) makes a retry safe.

All nine bridges return True on a 2xx reply and False on every failure
path (non-retryable 4xx, exhausted window, TransportError, other
Exception).

Additional tests:
  * deer-flow: back-pressure at _PENDING_CAP waits for a slot instead of
    dropping user_message.
  * all bridges: non-TransportError exceptions from c.post are caught, logged
    as 'reply POST failed', and return False.
  * all bridges: post_reply and the SSE loop carry the same retry/resume
    contract so no copy can drift (TestBridgeCopiesDontDrift).
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
        """All-503 responses: retries continue inside the 180s window and
        only give up once it is exhausted (about 16 attempts, not 3)."""
        post_reply = bridge["post_reply"]
        client = MagicMock()
        client.post = AsyncMock(return_value=_ok(503))

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = asyncio.run(post_reply(client, "http://x/reply", "tok", "m1", "t1", "hi"))

        assert client.post.call_count == 16
        assert result is False
        delays = [float(call.args[0]) for call in mock_sleep.call_args_list]
        # Capped steps: 1, 2, 4, 8 then 15 repeating; the window totals 180s.
        assert delays[:4] == [1.0, 2.0, 4.0, 8.0]
        assert all(d == 15.0 for d in delays[4:])
        assert sum(delays) >= 180.0

    def test_transport_error_exhausts_retries(self, bridge):
        post_reply = bridge["post_reply"]
        client = MagicMock()
        client.post = AsyncMock(side_effect=httpx.TransportError("boom"))

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = asyncio.run(post_reply(client, "http://x/reply", "tok", "m1", "t1", "hi"))

        assert client.post.call_count == 16
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

        assert client.post.call_count == 16
        assert result is False


class TestPostReplyRestartWindow:
    """A final reply must survive a controller restart: the unit allows
    TimeoutStartSec=180, so post_reply keeps retrying for at least 180 s
    on connection errors, 429 and 5xx, and stops on the first 2xx so the
    reply is delivered exactly once."""

    def test_refuses_for_30s_then_accepts_delivered_once(self, bridge):
        """Fake server refuses connections for 30 s of capped backoff
        (1+2+4+8+15) then accepts: the reply lands exactly once."""
        post_reply = bridge["post_reply"]
        client = MagicMock()
        client.post = AsyncMock(
            side_effect=[httpx.ConnectError("refused")] * 5 + [_ok(200)]
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = asyncio.run(post_reply(client, "http://x/reply", "tok", "m1", "t1", "hi"))

        assert result is True
        # Delivered exactly once: post stops at the first 2xx, no extra calls.
        assert client.post.call_count == 6
        for call in client.post.call_args_list:
            assert call.args[0] == "http://x/reply"
            assert call.kwargs["headers"]["Authorization"] == "Bearer tok"

    def test_other_4xx_not_retried(self, bridge):
        """A non-429 4xx is rejected outright, never retried."""
        post_reply = bridge["post_reply"]
        client = MagicMock()
        client.post = AsyncMock(return_value=_ok(403))

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = asyncio.run(post_reply(client, "http://x/reply", "tok", "m1", "t1", "hi"))

        assert client.post.call_count == 1
        mock_sleep.assert_not_called()
        assert result is False

    def test_gives_up_after_180s_window(self, bridge):
        """A permanently failing server: post_reply retries inside the
        180s window and only returns False once it is exhausted."""
        post_reply = bridge["post_reply"]
        client = MagicMock()
        client.post = AsyncMock(return_value=_ok(503))

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = asyncio.run(post_reply(client, "http://x/reply", "tok", "m1", "t1", "hi"))

        assert result is False
        assert client.post.call_count == 16
        delays = [float(call.args[0]) for call in mock_sleep.call_args_list]
        assert sum(delays) >= 180.0


# -- divergence guard: all nine copies carry the same contract ---------


class TestBridgeCopiesDontDrift:
    """The nine bridge scripts must not drift apart: post_reply keeps the
    same retry contract and the SSE loop tracks id: lines and resends
    Last-Event-ID on reconnect in every copy."""

    POST_REPLY_MARKERS = [
        "REPLY_BACKOFF_STEPS = (1.0, 2.0, 4.0, 8.0, 15.0)",
        "REPLY_WINDOW_S = 180.0",
        "if resp.status_code == 429 or 500 <= resp.status_code < 600:",
        "except httpx.TransportError as e:",
        "await asyncio.sleep(delay)",
        "elapsed += delay",
    ]

    SSE_MARKERS = [
        'headers["Last-Event-ID"] = last_event_id',
        "elif raw.startswith(\"id:\"):",
        "last_event_id = raw[3:].strip()",
    ]

    @pytest.mark.parametrize("script", BRIDGES, ids=lambda s: s.replace(".sh", ""))
    def test_post_reply_carries_contract(self, script):
        src = _extract(script)
        for marker in self.POST_REPLY_MARKERS:
            assert marker in src, f"{script}: post_reply missing contract marker {marker!r}"

    @pytest.mark.parametrize("script", BRIDGES, ids=lambda s: s.replace(".sh", ""))
    def test_sse_loop_carries_last_event_id(self, script):
        src = _extract(script)
        for marker in self.SSE_MARKERS:
            assert marker in src, f"{script}: SSE loop missing contract marker {marker!r}"


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


# -- SSE Last-Event-ID on reconnect, real execution for every bridge -----


class TestSseLastEventIdReal:
    """The real sse loop of every bridge records the last id: frame seen on
    the stream and resends it as the Last-Event-ID header when it reconnects,
    so an event position is not lost across a reconnect."""

    def _make_channel(self):
        return {
            "events_url": "http://test/events",
            "reply_url": "http://test/reply",
            "auth_bearer": "test-token",
        }

    @pytest.mark.parametrize("script", BRIDGES, ids=lambda s: s.replace(".sh", ""))
    def test_reconnect_sends_last_event_id(self, script):
        ns = _load(script)
        sse_loop = ns.get("sse_loop") or ns.get("sse")
        assert sse_loop is not None, f"{script}: no sse loop found"
        channel = self._make_channel()
        streamed_headers = []
        stop = asyncio.Event()
        calls = {"n": 0}

        class _FakeResp:
            status_code = 200

            async def aiter_lines(self):
                if calls["n"] == 1:
                    # First connection: an id: 7 frame plus a user_message,
                    # then the link drops (the generator returns).
                    yield "id: 7"
                    yield "event: user_message"
                    yield 'data: {"id": "m-1", "text": "hello"}'
                    yield ""
                    return
                # Later connections carry no frames; stop is set on entry.
                return

        class _FakeStream:
            def __init__(self, headers=None):
                self._headers = dict(headers or {})

            async def __aenter__(self):
                calls["n"] += 1
                streamed_headers.append(self._headers)
                if calls["n"] == 2:
                    stop.set()
                return _FakeResp()

            async def __aexit__(self, *args):
                return False

        class _FakeClient:
            def stream(self, method, url, headers=None, timeout=None):
                return _FakeStream(headers)

        async def fake_limited(*args, **kwargs):
            return True

        handler_name = "_run_handle_user_message_limited" \
            if "_run_handle_user_message_limited" in ns else "handle"
        with patch.dict(ns, {handler_name: fake_limited}):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                asyncio.run(sse_loop(_FakeClient(), channel, stop))

        assert len(streamed_headers) == 2, streamed_headers
        assert "Last-Event-ID" not in streamed_headers[0], streamed_headers[0]
        assert streamed_headers[1].get("Last-Event-ID") == "7", streamed_headers[1]