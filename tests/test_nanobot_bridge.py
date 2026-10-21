"""Tests for the nanobot bridge embedded in install_nanobot.sh.

The bridge Python code lives inside a shell heredoc. These tests extract it,
verify it compiles, and exercise the retry/backoff/dedup/cooldown logic with
mocked httpx clients.
"""

import ast
import asyncio
import py_compile
import re
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# -- extraction helpers --------------------------------------------------------

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "tinyagentos" / "scripts" / "install_nanobot.sh"


def extract_bridge_python() -> str:
    """Extract the embedded Python from the shell heredoc."""
    text = SCRIPT_PATH.read_text()
    m = re.search(r"<<'BRIDGE_EOF'\n(.*?)BRIDGE_EOF", text, re.DOTALL)
    if not m:
        raise RuntimeError("Could not find BRIDGE_EOF heredoc in install_nanobot.sh")
    return m.group(1)


BRIDGE_PY = extract_bridge_python()


# -- compilation ---------------------------------------------------------------

def test_bridge_python_compiles():
    """The embedded Python must compile without syntax errors."""
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(BRIDGE_PY)
        tmp = f.name
    try:
        py_compile.compile(tmp, doraise=True)
    finally:
        Path(tmp).unlink()


# -- helpers for loading bridge functions into a test namespace -----------------

def _load_bridge_functions():
    """exec the bridge code (without running main) and return the globals dict."""
    # Strip the 'if __name__ == "__main__":' guard so main() doesn't fire.
    code = BRIDGE_PY
    # Remove from the last occurrence of "if __name__" to end-of-file
    m = re.search(r'\nif __name__\s*==\s*["\']__main__["\']', code)
    if m:
        code = code[: m.start()]
    # Set env vars the bridge expects at import time
    import os as _os
    _os.environ.setdefault("TAOS_BRIDGE_URL", "http://127.0.0.1:8989")
    _os.environ.setdefault("TAOS_AGENT_NAME", "test-agent")
    _os.environ.setdefault("TAOS_LOCAL_TOKEN", "test-token")
    _os.environ.setdefault("LITELLM_API_KEY", "")
    _os.environ.setdefault("TAOS_MODEL", "test-model")
    ns: dict = {}
    exec(code, ns)
    return ns


@pytest.fixture(scope="module")
def bridge_ns():
    """Module-scoped fixture: loads the bridge functions once."""
    return _load_bridge_functions()


# -- call_nanobot retry tests ---------------------------------------------------

class TestCallNanobotRetry:
    """call_nanobot(client, content, session_id) -> str"""

    def test_200_returns_content(self, bridge_ns):
        call_nanobot = bridge_ns["call_nanobot"]
        client = MagicMock()
        client.post = AsyncMock(return_value=MagicMock(
            status_code=200,
            json=MagicMock(return_value={"choices": [{"message": {"content": "hello"}}]}),
        ))

        result = asyncio.run(call_nanobot(client, "hi", "test-agent-ch1"))
        assert result == "hello"
        assert client.post.call_count == 1

    def test_5xx_retries_with_backoff(self, bridge_ns):
        call_nanobot = bridge_ns["call_nanobot"]
        client = MagicMock()

        # Fail twice with 503, succeed on third attempt
        client.post = AsyncMock(side_effect=[
            MagicMock(status_code=503, text="Service Unavailable"),
            MagicMock(status_code=503, text="Service Unavailable"),
            MagicMock(status_code=200, json=MagicMock(
                return_value={"choices": [{"message": {"content": "recovered"}}]},
            )),
        ])

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = asyncio.run(call_nanobot(client, "hi", "test-agent-ch1"))

        assert result == "recovered"
        assert client.post.call_count == 3
        # Backoff: attempt 1 delay=2, attempt 2 delay=4
        assert mock_sleep.call_count == 2
        assert mock_sleep.call_args_list[0].args[0] == pytest.approx(2.0)   # 2^1
        assert mock_sleep.call_args_list[1].args[0] == pytest.approx(4.0)   # 2^2

    def test_4xx_returns_immediately_no_retry(self, bridge_ns):
        call_nanobot = bridge_ns["call_nanobot"]
        client = MagicMock()
        client.post = AsyncMock(return_value=MagicMock(
            status_code=400, text="Bad Request",
        ))

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = asyncio.run(call_nanobot(client, "hi", "test-agent-ch1"))

        assert result.startswith("[nanobot returned 400:")
        assert client.post.call_count == 1
        mock_sleep.assert_not_called()

    def test_exception_retries_then_returns_error(self, bridge_ns):
        call_nanobot = bridge_ns["call_nanobot"]
        client = MagicMock()
        client.post = AsyncMock(side_effect=TimeoutError("timed out"))

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = asyncio.run(call_nanobot(client, "hi", "test-agent-ch1"))

        assert "[nanobot error: timed out]" in result or "[nanobot error: TimeoutError]" in result
        assert client.post.call_count == 3  # MAX_RETRIES

    def test_all_retries_exhausted_returns_last_error(self, bridge_ns):
        call_nanobot = bridge_ns["call_nanobot"]
        client = MagicMock()

        # All three attempts return 503
        client.post = AsyncMock(return_value=MagicMock(
            status_code=503, text="Gateway Timeout",
        ))

        result = asyncio.run(call_nanobot(client, "hi", "test-agent-ch1"))
        assert result.startswith("[nanobot returned 503:")
        assert client.post.call_count == 3


# -- call_nanobot request shape ------------------------------------------------

class TestCallNanobotRequestShape:
    """nanobot's /v1/chat/completions accepts exactly one user message and
    rejects any model other than its configured preset, so the bridge must
    send one user message, a session_id, and no model key."""

    def _ok_client(self, content="hello"):
        client = MagicMock()
        client.post = AsyncMock(return_value=MagicMock(
            status_code=200,
            json=MagicMock(return_value={"choices": [{"message": {"content": content}}]}),
        ))
        return client

    def test_posts_to_chat_completions(self, bridge_ns):
        client = self._ok_client()
        asyncio.run(bridge_ns["call_nanobot"](client, "hi", "test-agent-ch1"))
        url = client.post.call_args.args[0]
        assert url == "http://127.0.0.1:8900/v1/chat/completions"

    def test_body_has_one_user_message_session_id_and_no_model(self, bridge_ns):
        client = self._ok_client()
        asyncio.run(bridge_ns["call_nanobot"](client, "hi there", "test-agent-ch1"))
        body = client.post.call_args.kwargs["json"]
        assert body["messages"] == [{"role": "user", "content": "hi there"}]
        assert body["session_id"] == "test-agent-ch1"
        assert "model" not in body

    def test_reply_parsed_from_choices_message_content(self, bridge_ns):
        client = self._ok_client(content="from nanobot")
        result = asyncio.run(bridge_ns["call_nanobot"](client, "hi", "test-agent-ch1"))
        assert result == "from nanobot"

    def test_handle_user_message_sends_single_combined_message(self, bridge_ns):
        """Context, latest text and attachments are folded into the one
        user message nanobot accepts, keyed by agent and channel."""
        handle = bridge_ns["handle_user_message"]
        client = MagicMock()
        client.post = AsyncMock()
        evt = {
            "id": "m1", "trace_id": "m1", "text": "latest question",
            "channel_id": "ch1", "force_respond": True,
            "context": [{"author_id": "jay", "content": "earlier line"}],
        }
        channel = {"reply_url": "http://x/reply", "auth_bearer": "tok"}
        call = AsyncMock(return_value="ok")
        with patch.dict(bridge_ns, {"call_nanobot": call}):
            asyncio.run(handle(client, evt, channel, set(), [0.0]))
        content, session_id = call.call_args.args[1], call.call_args.args[2]
        assert isinstance(content, str)
        assert "earlier line" in content
        assert "latest question" in content
        assert session_id == "test-agent-ch1"


# -- handle_user_message dedup + cooldown tests --------------------------------

class TestHandleUserMessageDedup:
    """handle_user_message(client, evt, channel, _seen, _error_until) -> bool"""

    def _make_evt(self, msg_id="msg-1", text="hello"):
        return {"id": msg_id, "trace_id": msg_id, "text": text}

    def _make_channel(self):
        return {"reply_url": "http://x/reply", "auth_bearer": "tok"}

    def _mock_client(self):
        """Create a client with post_reply mocked to succeed."""
        client = MagicMock()
        # Default successful response for post_reply
        client.post = AsyncMock(return_value=MagicMock(status_code=200))
        return client

    def test_duplicate_message_skipped(self, bridge_ns):
        handle = bridge_ns["handle_user_message"]
        client = self._mock_client()
        evt = self._make_evt()
        channel = self._make_channel()
        seen: set = set()
        error_until: list = [0.0]

        # First call, should process
        seen_before = seen.copy()
        # We need to mock call_nanobot to return a non-error
        with patch.dict(bridge_ns, {"call_nanobot": AsyncMock(return_value="ok")}):
            result1 = asyncio.run(handle(client, evt, channel, seen, error_until))
        assert result1 is True
        assert "msg-1" in seen

        # Second call with same msg_id, should be skipped
        with patch.dict(bridge_ns, {"call_nanobot": AsyncMock(return_value="ok")}):
            result2 = asyncio.run(handle(client, evt, channel, seen, error_until))
        assert result2 is False  # dedup suppressed

    def test_error_cooldown_blocks_subsequent_messages(self, bridge_ns):
        handle = bridge_ns["handle_user_message"]
        client = self._mock_client()
        channel = self._make_channel()
        seen: set = set()
        error_until: list = [0.0]

        # First message returns a nanobot error -> starts cooldown
        with patch.dict(bridge_ns, {"call_nanobot": AsyncMock(
            return_value="[nanobot error: ReadTimeout]"
        )}):
            result1 = asyncio.run(handle(client, self._make_evt("m1"), channel, seen, error_until))
        assert result1 is True
        assert error_until[0] > 0  # cooldown timestamp set

        # Second message arrives during cooldown -> suppressed
        with patch.dict(bridge_ns, {"call_nanobot": AsyncMock(return_value="ok")}):
            result2 = asyncio.run(handle(client, self._make_evt("m2"), channel, seen, error_until))
        assert result2 is False  # cooldown suppressed

    def test_no_cooldown_for_non_error_reply(self, bridge_ns):
        handle = bridge_ns["handle_user_message"]
        client = self._mock_client()
        channel = self._make_channel()
        seen: set = set()
        error_until: list = [0.0]

        with patch.dict(bridge_ns, {"call_nanobot": AsyncMock(return_value="Normal reply")}):
            result1 = asyncio.run(handle(client, self._make_evt("m1"), channel, seen, error_until))
        assert result1 is True
        assert error_until[0] == 0.0  # no cooldown triggered

        # Next message should process normally
        with patch.dict(bridge_ns, {"call_nanobot": AsyncMock(return_value="Another reply")}):
            result2 = asyncio.run(handle(client, self._make_evt("m2"), channel, seen, error_until))
        assert result2 is True


# -- seen timing tests (RED-FIRST: these should FAIL on unmodified code) ---------

class TestSeenTiming:
    """Tests verifying _seen is updated ONLY after successful reply POST."""

    def _make_evt(self, msg_id="msg-1", text="hello", force=False):
        return {"id": msg_id, "trace_id": msg_id, "text": text, "force_respond": force}

    def _make_channel(self):
        return {"reply_url": "http://x/reply", "auth_bearer": "tok"}

    def test_cooldown_suppressed_message_not_in_seen(self, bridge_ns):
        """During error cooldown, handle_user_message returns False and id NOT in seen."""
        handle = bridge_ns["handle_user_message"]
        client = MagicMock()
        client.post = AsyncMock()
        channel = self._make_channel()
        seen: set = set()
        error_until: list = [float('inf')]  # cooldown active

        with patch.dict(bridge_ns, {"call_nanobot": AsyncMock(return_value="ok")}):
            result = asyncio.run(handle(client, self._make_evt("m-cooldown"), channel, seen, error_until))

        assert result is False
        assert "m-cooldown" not in seen, "Cooldown-suppressed message must NOT be added to _seen"

    def test_reply_post_failure_message_not_in_seen(self, bridge_ns):
        """When reply POST fails, the id is NOT added to _seen."""
        handle = bridge_ns["handle_user_message"]
        client = MagicMock()
        # Make post_reply fail by having the client.post raise an exception
        client.post = AsyncMock(side_effect=Exception("network error"))
        channel = self._make_channel()
        seen: set = set()
        error_until: list = [0.0]

        with patch.dict(bridge_ns, {"call_nanobot": AsyncMock(return_value="ok")}):
            result = asyncio.run(handle(client, self._make_evt("m-post-fail"), channel, seen, error_until))

        # The function returns False (reply POST failed)
        # but the message id should NOT be in seen since POST failed
        assert "m-post-fail" not in seen, "Message with failed reply POST must NOT be added to _seen"

    def test_successful_reply_adds_to_seen(self, bridge_ns):
        """On successful reply POST, the id IS added to _seen."""
        handle = bridge_ns["handle_user_message"]
        client = MagicMock()
        client.post = AsyncMock(return_value=MagicMock(status_code=200))
        channel = self._make_channel()
        seen: set = set()
        error_until: list = [0.0]

        with patch.dict(bridge_ns, {"call_nanobot": AsyncMock(return_value="ok")}):
            result = asyncio.run(handle(client, self._make_evt("m-success"), channel, seen, error_until))

        assert result is True
        assert "m-success" in seen, "Successfully posted message must be added to _seen"


# -- bounded _seen test --------------------------------------------------------

class TestSeenBounded:
    def test_seen_clears_at_1000_to_prevent_unbounded_growth(self, bridge_ns):
        """Verify the _MAX_SEEN guard exists in the source."""
        assert "_MAX_SEEN" in BRIDGE_PY, "Missing _MAX_SEEN constant"
        assert "len(_seen) >= _MAX_SEEN" in BRIDGE_PY, "Missing bound check"
        assert "_seen.clear()" in BRIDGE_PY, "Missing clear on bound"



# -- bounded concurrency test -------------------------------------------------

class TestBoundedConcurrency:
    def _make_channel(self):
        return {
            "events_url": "http://127.0.0.1:8900/sse",
            "reply_url": "http://127.0.0.1:8900/reply",
            "auth_bearer": "token",
        }

    def _make_evt(self, msg_id):
        return {"id": msg_id, "trace_id": msg_id, "text": "hi", "force_respond": True}

    def test_user_message_tasks_bounded(self, bridge_ns):
        """Peak concurrent handle_user_message executions <= _MAX_INFLIGHT (4)."""
        assert "_run_handle_user_message_limited" in bridge_ns
        run_limited = bridge_ns["_run_handle_user_message_limited"]
        client = MagicMock()
        client.post = AsyncMock(return_value=MagicMock(status_code=200))

        peak = {"n": 0}
        current = {"n": 0}

        async def slow_call_nanobot(*args, **kwargs):
            current["n"] += 1
            if current["n"] > peak["n"]:
                peak["n"] = current["n"]
            await asyncio.sleep(0.05)
            current["n"] -= 1
            return "ok"

        channel = self._make_channel()
        events = 10

        async def runner():
            tasks = []
            for i in range(events):
                seen: set = set()
                error_until: list = [0.0]
                tasks.append(asyncio.create_task(
                    run_limited(client, self._make_evt(f"m-{i}"), channel, seen, error_until)
                ))
            results = await asyncio.gather(*tasks, return_exceptions=True)
            return results

        with patch.dict(bridge_ns, {"call_nanobot": slow_call_nanobot}):
            results = asyncio.run(runner())

        assert all(r is True for r in results), results
        assert peak["n"] <= 4, f"peak concurrent calls {peak['n']} > 4"
