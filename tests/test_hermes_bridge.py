"""Tests for the Hermes bridge embedded in install_hermes.sh.

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

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "tinyagentos" / "scripts" / "install_hermes.sh"


def extract_bridge_python() -> str:
    """Extract the embedded Python from the shell heredoc."""
    text = SCRIPT_PATH.read_text()
    m = re.search(r"<<'BRIDGE_EOF'\n(.*?)BRIDGE_EOF", text, re.DOTALL)
    if not m:
        raise RuntimeError("Could not find BRIDGE_EOF heredoc in install_hermes.sh")
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


# -- call_hermes retry tests ---------------------------------------------------

class TestCallHermesRetry:
    """call_hermes(client, messages) → str"""

    def test_200_returns_content(self, bridge_ns):
        call_hermes = bridge_ns["call_hermes"]
        client = MagicMock()
        client.post = AsyncMock(return_value=MagicMock(
            status_code=200,
            json=MagicMock(return_value={"choices": [{"message": {"content": "hello"}}]}),
        ))

        result = asyncio.run(call_hermes(client, [{"role": "user", "content": "hi"}]))
        assert result == "hello"
        assert client.post.call_count == 1

    def test_5xx_retries_with_backoff(self, bridge_ns):
        call_hermes = bridge_ns["call_hermes"]
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
            result = asyncio.run(call_hermes(client, [{"role": "user", "content": "hi"}]))

        assert result == "recovered"
        assert client.post.call_count == 3
        # Backoff: attempt 1 delay=2, attempt 2 delay=4
        assert mock_sleep.call_count == 2
        assert mock_sleep.call_args_list[0].args[0] == pytest.approx(2.0)   # 2^1
        assert mock_sleep.call_args_list[1].args[0] == pytest.approx(4.0)   # 2^2

    def test_4xx_returns_immediately_no_retry(self, bridge_ns):
        call_hermes = bridge_ns["call_hermes"]
        client = MagicMock()
        client.post = AsyncMock(return_value=MagicMock(
            status_code=400, text="Bad Request",
        ))

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = asyncio.run(call_hermes(client, [{"role": "user", "content": "hi"}]))

        assert result.startswith("[hermes returned 400:")
        assert client.post.call_count == 1
        mock_sleep.assert_not_called()

    def test_exception_retries_then_returns_error(self, bridge_ns):
        call_hermes = bridge_ns["call_hermes"]
        client = MagicMock()
        client.post = AsyncMock(side_effect=TimeoutError("timed out"))

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = asyncio.run(call_hermes(client, [{"role": "user", "content": "hi"}]))

        assert "[hermes error: timed out]" in result or "[hermes error: TimeoutError]" in result
        assert client.post.call_count == 3  # MAX_RETRIES

    def test_all_retries_exhausted_returns_last_error(self, bridge_ns):
        call_hermes = bridge_ns["call_hermes"]
        client = MagicMock()

        # All three attempts return 503
        client.post = AsyncMock(return_value=MagicMock(
            status_code=503, text="Gateway Timeout",
        ))

        result = asyncio.run(call_hermes(client, [{"role": "user", "content": "hi"}]))
        assert result.startswith("[hermes returned 503:")
        assert client.post.call_count == 3


# -- handle_user_message dedup + cooldown tests --------------------------------

class TestHandleUserMessageDedup:
    """handle_user_message(client, evt, channel, _seen, _error_until) → bool"""

    def _make_evt(self, msg_id="msg-1", text="hello"):
        return {"id": msg_id, "trace_id": msg_id, "text": text}

    def _make_channel(self):
        return {"reply_url": "http://x/reply", "auth_bearer": "tok"}

    def test_duplicate_message_skipped(self, bridge_ns):
        handle = bridge_ns["handle_user_message"]
        client = MagicMock()
        client.post = AsyncMock()
        evt = self._make_evt()
        channel = self._make_channel()
        seen: set = set()
        error_until: list = [0.0]

        # First call — should process
        seen_before = seen.copy()
        # We need to mock call_hermes to return a non-error
        with patch.dict(bridge_ns, {"call_hermes": AsyncMock(return_value="ok")}):
            result1 = asyncio.run(handle(client, evt, channel, seen, error_until))
        assert result1 is True
        assert "msg-1" in seen

        # Second call with same msg_id — should be skipped
        with patch.dict(bridge_ns, {"call_hermes": AsyncMock(return_value="ok")}):
            result2 = asyncio.run(handle(client, evt, channel, seen, error_until))
        assert result2 is False  # dedup suppressed

    def test_error_cooldown_blocks_subsequent_messages(self, bridge_ns):
        handle = bridge_ns["handle_user_message"]
        client = MagicMock()
        client.post = AsyncMock()
        channel = self._make_channel()
        seen: set = set()
        error_until: list = [0.0]

        # First message returns a hermes error → starts cooldown
        with patch.dict(bridge_ns, {"call_hermes": AsyncMock(
            return_value="[hermes error: ReadTimeout]"
        )}):
            result1 = asyncio.run(handle(client, self._make_evt("m1"), channel, seen, error_until))
        assert result1 is True
        assert error_until[0] > 0  # cooldown timestamp set

        # Second message arrives during cooldown → suppressed
        with patch.dict(bridge_ns, {"call_hermes": AsyncMock(return_value="ok")}):
            result2 = asyncio.run(handle(client, self._make_evt("m2"), channel, seen, error_until))
        assert result2 is False  # cooldown suppressed

    def test_no_cooldown_for_non_error_reply(self, bridge_ns):
        handle = bridge_ns["handle_user_message"]
        client = MagicMock()
        client.post = AsyncMock()
        channel = self._make_channel()
        seen: set = set()
        error_until: list = [0.0]

        with patch.dict(bridge_ns, {"call_hermes": AsyncMock(return_value="Normal reply")}):
            result1 = asyncio.run(handle(client, self._make_evt("m1"), channel, seen, error_until))
        assert result1 is True
        assert error_until[0] == 0.0  # no cooldown triggered

        # Next message should process normally
        with patch.dict(bridge_ns, {"call_hermes": AsyncMock(return_value="Another reply")}):
            result2 = asyncio.run(handle(client, self._make_evt("m2"), channel, seen, error_until))
        assert result2 is True


# -- bounded _seen test --------------------------------------------------------

class TestSeenBounded:
    def test_seen_clears_at_1000_to_prevent_unbounded_growth(self, bridge_ns):
        """Verify the _MAX_SEEN guard exists in the source."""
        assert "_MAX_SEEN" in BRIDGE_PY, "Missing _MAX_SEEN constant"
        assert "len(_seen) >= _MAX_SEEN" in BRIDGE_PY, "Missing bound check"
        assert "_seen.clear()" in BRIDGE_PY, "Missing clear on bound"
