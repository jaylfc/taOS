"""Retry behaviour of every bridge's embedded post_reply.

Each bridge ships its reply POST inside a shell heredoc. This module extracts
that Python the same way tests/test_deer_flow_bridge.py loads the deer-flow
bridge and asserts the retry contract:

  * 5xx / 429 -> retry up to 3 attempts total, with a 1.0s backoff between
    attempts, until a 2xx lands.
  * other 4xx (e.g. 400) -> no retry, treated as failure.
  * httpx.TransportError -> retried; exhausting all attempts fails.

The compact variants (pocketflow, smolagents, openai-agents-sdk,
openai_agents_sdk) return None on both success and failure today, so the
contract we assert there is the POST count, not the return value.
"""

import asyncio
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
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(_extract(script))
        tmp = f.name
    try:
        py_compile.compile(tmp, doraise=True)
    finally:
        Path(tmp).unlink()


# -- retry contract ------------------------------------------------------------

class TestPostReplyRetry:
    def test_503_then_200_retries_and_succeeds(self, bridge):
        post_reply = bridge["post_reply"]
        client = MagicMock()
        client.post = AsyncMock(side_effect=[_ok(503), _ok(200)])

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = asyncio.run(post_reply(client, "http://x/reply", "tok", "m1", "t1", "hi"))

        assert client.post.call_count == 2

    def test_400_is_not_retried(self, bridge):
        post_reply = bridge["post_reply"]
        client = MagicMock()
        client.post = AsyncMock(return_value=_ok(400))

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = asyncio.run(post_reply(client, "http://x/reply", "tok", "m1", "t1", "hi"))

        assert client.post.call_count == 1
        mock_sleep.assert_not_called()

    def test_transport_error_exhausts_retries(self, bridge):
        post_reply = bridge["post_reply"]
        client = MagicMock()
        client.post = AsyncMock(side_effect=httpx.TransportError("boom"))

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = asyncio.run(post_reply(client, "http://x/reply", "tok", "m1", "t1", "hi"))

        assert client.post.call_count == 3

    def test_429_is_retried(self, bridge):
        post_reply = bridge["post_reply"]
        client = MagicMock()
        client.post = AsyncMock(side_effect=[_ok(429), _ok(200)])

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = asyncio.run(post_reply(client, "http://x/reply", "tok", "m1", "t1", "hi"))

        assert client.post.call_count == 2