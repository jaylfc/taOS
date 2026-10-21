"""Tests for the Langroid bridge embedded in install_langroid.sh.

The bridge Python code lives inside a shell heredoc. These tests extract it,
verify it compiles, and exercise the retry/backoff logic for fetch_boot and
post_reply with mocked httpx clients.
"""

import asyncio
import py_compile
import re
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# -- extraction helpers --------------------------------------------------------

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "tinyagentos" / "scripts" / "install_langroid.sh"


def extract_bridge_python() -> str:
    """Extract the embedded Python from the shell heredoc."""
    text = SCRIPT_PATH.read_text()
    m = re.search(r"<<'BRIDGE_EOF'\n(.*?)BRIDGE_EOF", text, re.DOTALL)
    if not m:
        raise RuntimeError("Could not find BRIDGE_EOF heredoc in install_langroid.sh")
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
    code = BRIDGE_PY
    # Strip the trailing main invocation so the bridge does not start.
    for marker in (
        r'\nif __name__\s*==\s*["\']__main__["\']',
        r'\nasyncio\.run\(main\(\)\)',
    ):
        m = re.search(marker, code)
        if m:
            code = code[: m.start()]
    import os as _os
    _os.environ.setdefault("TAOS_BRIDGE_URL", "http://127.0.0.1:8989")
    _os.environ.setdefault("TAOS_AGENT_NAME", "test-agent")
    _os.environ.setdefault("TAOS_LOCAL_TOKEN", "test-token")
    _os.environ.setdefault("TAOS_MODEL", "test-model")
    ns: dict = {}
    exec(code, ns)
    return ns


@pytest.fixture(scope="module")
def bridge_ns():
    """Module-scoped fixture: loads the bridge functions once."""
    return _load_bridge_functions()


# -- post_reply retry tests ----------------------------------------------------

class TestPostReplyRetry:
    """post_reply(c, u, t, mid, tid, txt, cid=None) -> bool"""

    def _make_client(self, statuses):
        client = MagicMock()
        client.post = AsyncMock(side_effect=[
            MagicMock(status_code=s, text="err") for s in statuses
        ])
        return client

    def test_503_503_200_succeeds(self, bridge_ns):
        """RED target: post_reply retries on 503 and succeeds once it returns 200."""
        post_reply = bridge_ns["post_reply"]
        client = self._make_client([503, 503, 200])

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = asyncio.run(post_reply(
                client, "http://x/reply", "tok", "mid-1", "trace-1", "hello"
            ))

        assert result is True
        assert client.post.call_count == 3
        assert mock_sleep.call_count == 2
        assert mock_sleep.call_args_list[0].args[0] == pytest.approx(2.0)
        assert mock_sleep.call_args_list[1].args[0] == pytest.approx(4.0)

    def test_200_succeeds_first_try(self, bridge_ns):
        post_reply = bridge_ns["post_reply"]
        client = MagicMock()
        client.post = AsyncMock(return_value=MagicMock(status_code=200))

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = asyncio.run(post_reply(
                client, "http://x/reply", "tok", "mid-1", "trace-1", "hello"
            ))

        assert result is True
        assert client.post.call_count == 1
        mock_sleep.assert_not_called()

    def test_all_503_drops_reply(self, bridge_ns):
        post_reply = bridge_ns["post_reply"]
        client = self._make_client([503, 503, 503])

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = asyncio.run(post_reply(
                client, "http://x/reply", "tok", "mid-1", "trace-1", "hello"
            ))

        assert result is False
        assert client.post.call_count == 3

    def test_exception_retries_then_drops(self, bridge_ns):
        post_reply = bridge_ns["post_reply"]
        client = MagicMock()
        client.post = AsyncMock(side_effect=ConnectionError("refused"))

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = asyncio.run(post_reply(
                client, "http://x/reply", "tok", "mid-1", "trace-1", "hello"
            ))

        assert result is False
        assert client.post.call_count == 3


# -- fetch_boot retry tests ----------------------------------------------------

class TestFetchBootRetry:
    def test_503_503_200_returns_json(self, bridge_ns):
        """fetch_boot retries on 503 and returns the bootstrap once it is 200."""
        fetch_boot = bridge_ns["fetch_boot"]
        boot = {
            "schema_version": 1,
            "agent_name": "test-agent",
            "channel": {"reply_url": "http://x", "auth_bearer": "t", "events_url": "http://x/sse"},
        }
        client = MagicMock()
        client.get = AsyncMock(side_effect=[
            MagicMock(status_code=503, text="err"),
            MagicMock(status_code=503, text="err"),
            MagicMock(status_code=200, json=MagicMock(return_value=boot)),
        ])

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = asyncio.run(fetch_boot(client))

        assert result == boot
        assert client.get.call_count == 3
        assert mock_sleep.call_count == 2
        assert mock_sleep.call_args_list[0].args[0] == pytest.approx(2.0)
        assert mock_sleep.call_args_list[1].args[0] == pytest.approx(4.0)

    def test_all_503_raises(self, bridge_ns):
        """After exhausting retries on a persistent 5xx, fetch_boot raises."""
        fetch_boot = bridge_ns["fetch_boot"]
        client = MagicMock()
        client.get = AsyncMock(side_effect=[
            MagicMock(status_code=503, text="err", json=MagicMock(return_value={}))
            for _ in range(3)
        ])

        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(RuntimeError):
                asyncio.run(fetch_boot(client))

        assert client.get.call_count == 3