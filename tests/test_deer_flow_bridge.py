"""Tests for the DeerFlow bridge embedded in install_deer-flow.sh.

The bridge Python code lives inside a shell heredoc. These tests extract it,
verify it compiles, and exercise the runs-API call, reply extraction, retry
and dedup/cooldown logic with mocked httpx clients.
"""

import asyncio
import py_compile
import re
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from tinyagentos import deployer
from tinyagentos.frameworks import FRAMEWORKS


# -- extraction helpers --------------------------------------------------------

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "tinyagentos" / "scripts" / "install_deer-flow.sh"


def extract_bridge_python() -> str:
    """Extract the embedded Python from the shell heredoc."""
    text = SCRIPT_PATH.read_text()
    m = re.search(r"<<'BRIDGE_EOF'\n(.*?)BRIDGE_EOF", text, re.DOTALL)
    if not m:
        raise RuntimeError("Could not find BRIDGE_EOF heredoc in install_deer-flow.sh")
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
    m = re.search(r'\nif __name__\s*==\s*["\']__main__["\']', code)
    if m:
        code = code[: m.start()]
    import os as _os
    _os.environ.setdefault("TAOS_BRIDGE_URL", "http://127.0.0.1:8989")
    _os.environ.setdefault("TAOS_AGENT_NAME", "test-agent")
    _os.environ.setdefault("TAOS_LOCAL_TOKEN", "test-token")
    ns: dict = {}
    exec(code, ns)
    return ns


@pytest.fixture(scope="module")
def bridge_ns():
    """Module-scoped fixture: loads the bridge functions once."""
    return _load_bridge_functions()


def _ok(body):
    return MagicMock(status_code=200, json=MagicMock(return_value=body))


# -- call_deer_flow ------------------------------------------------------------

class TestCallDeerFlow:
    """call_deer_flow(client, text) -> str"""

    def test_posts_runs_wait_with_user_message(self, bridge_ns):
        call = bridge_ns["call_deer_flow"]
        client = MagicMock()
        client.post = AsyncMock(return_value=_ok(
            {"messages": [{"role": "assistant", "content": "hello"}]}))

        result = asyncio.run(call(client, "hi there"))

        assert result == "hello"
        assert client.post.call_count == 1
        url = client.post.call_args.args[0]
        assert url.endswith("/api/runs/wait")
        payload = client.post.call_args.kwargs["json"]
        assert payload["input"]["messages"] == [{"role": "user", "content": "hi there"}]
        assert payload["context"] == {"model_name": "taos-default"}

    @pytest.mark.parametrize("key", ["output", "values"])
    def test_messages_nested_under_output_or_values(self, bridge_ns, key):
        call = bridge_ns["call_deer_flow"]
        client = MagicMock()
        client.post = AsyncMock(return_value=_ok(
            {key: {"messages": [{"type": "ai", "content": "nested"}]}}))
        assert asyncio.run(call(client, "hi")) == "nested"

    def test_last_ai_message_wins_over_trailing_tool_message(self, bridge_ns):
        call = bridge_ns["call_deer_flow"]
        client = MagicMock()
        client.post = AsyncMock(return_value=_ok({"messages": [
            {"type": "human", "content": "hi"},
            {"type": "ai", "content": [{"type": "text", "text": "part one "},
                                       {"type": "text", "text": "part two"}]},
            {"type": "tool", "content": "tool output"},
        ]}))
        assert asyncio.run(call(client, "hi")) == "part one part two"

    def test_non_200_returns_error_without_retry(self, bridge_ns):
        """A 5xx may arrive after the run did work; re-firing would re-run it."""
        call = bridge_ns["call_deer_flow"]
        client = MagicMock()
        client.post = AsyncMock(return_value=MagicMock(status_code=503, text="busy"))

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = asyncio.run(call(client, "hi"))

        assert result.startswith("[deer-flow returned 503:")
        assert client.post.call_count == 1
        mock_sleep.assert_not_called()

    def test_connect_error_retries_then_succeeds(self, bridge_ns):
        call = bridge_ns["call_deer_flow"]
        client = MagicMock()
        client.post = AsyncMock(side_effect=[
            httpx.ConnectError("refused"),
            _ok({"messages": [{"role": "assistant", "content": "up now"}]}),
        ])

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = asyncio.run(call(client, "hi"))

        assert result == "up now"
        assert client.post.call_count == 2
        assert mock_sleep.call_count == 1

    def test_read_timeout_is_not_retried(self, bridge_ns):
        """A read timeout means the run is in progress: never re-fire it."""
        call = bridge_ns["call_deer_flow"]
        client = MagicMock()
        client.post = AsyncMock(side_effect=httpx.ReadTimeout("slow"))

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = asyncio.run(call(client, "hi"))

        assert result.startswith("[deer-flow error:")
        assert client.post.call_count == 1
        mock_sleep.assert_not_called()

    @pytest.mark.parametrize("messages", [
        [],
        [{"type": "human", "content": "hi"},
         {"type": "ai", "content": "", "tool_calls": [{"name": "search"}]},
         {"type": "tool", "content": "raw tool output"}],
    ])
    def test_no_assistant_text_is_a_bridge_error(self, bridge_ns, messages):
        """A tool result or serialized tool call must never be posted as the reply."""
        call = bridge_ns["call_deer_flow"]
        client = MagicMock()
        client.post = AsyncMock(return_value=_ok({"messages": messages}))
        result = asyncio.run(call(client, "hi"))
        assert result == "[deer-flow error: no assistant message in response]"

    def test_connect_errors_exhaust_retries(self, bridge_ns):
        call = bridge_ns["call_deer_flow"]
        client = MagicMock()
        client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = asyncio.run(call(client, "hi"))

        assert result == "[deer-flow error: refused]"
        assert client.post.call_count == bridge_ns["MAX_RETRIES"]


# -- handle_user_message dedup + cooldown --------------------------------------

class TestHandleUserMessage:
    """handle_user_message(client, evt, channel, _seen, _error_until) -> bool"""

    def _evt(self, msg_id="msg-1", text="hello", **extra):
        return {"id": msg_id, "trace_id": msg_id, "text": text, **extra}

    def _channel(self):
        return {"reply_url": "http://x/reply", "auth_bearer": "tok"}

    def test_reply_posted_to_reply_url(self, bridge_ns):
        handle = bridge_ns["handle_user_message"]
        client = MagicMock()
        client.post = AsyncMock()
        with patch.dict(bridge_ns, {"call_deer_flow": AsyncMock(return_value="answer")}):
            assert asyncio.run(handle(client, self._evt(), self._channel(), set(), [0.0])) is True
        reply_calls = [c for c in client.post.call_args_list if c.args[0] == "http://x/reply"]
        assert len(reply_calls) == 1
        body = reply_calls[0].kwargs["json"]
        assert body["kind"] == "final"
        assert body["id"] == "msg-1"
        assert body["content"] == "answer"

    def test_prompt_carries_context_and_text(self, bridge_ns):
        handle = bridge_ns["handle_user_message"]
        client = MagicMock()
        client.post = AsyncMock()
        fake = AsyncMock(return_value="ok")
        evt = self._evt(text="what now?", force_respond=True,
                        context=[{"author_id": "jay", "content": "earlier line"}])
        with patch.dict(bridge_ns, {"call_deer_flow": fake}):
            asyncio.run(handle(client, evt, self._channel(), set(), [0.0]))
        prompt = fake.call_args.args[1]
        assert "jay: earlier line" in prompt
        assert "what now?" in prompt
        assert "test-agent" in prompt

    def test_context_without_author_id_uses_role(self, bridge_ns):
        render = bridge_ns["_render_context"]
        out = render([{"role": "system", "content": "agent manual"},
                      {"author_id": "jay", "role": "user", "content": "hi"},
                      {"content": "anon"}])
        assert out.splitlines() == ["system: agent manual", "jay: hi", "?: anon"]

    def test_no_response_suppressed_when_not_forced(self, bridge_ns):
        handle = bridge_ns["handle_user_message"]
        client = MagicMock()
        client.post = AsyncMock()
        with patch.dict(bridge_ns, {"call_deer_flow": AsyncMock(return_value="NO_RESPONSE")}):
            assert asyncio.run(handle(client, self._evt(), self._channel(), set(), [0.0])) is False
        assert not [c for c in client.post.call_args_list if c.args[0] == "http://x/reply"]

    def test_duplicate_message_skipped(self, bridge_ns):
        handle = bridge_ns["handle_user_message"]
        client = MagicMock()
        client.post = AsyncMock()
        seen: set = set()
        with patch.dict(bridge_ns, {"call_deer_flow": AsyncMock(return_value="ok")}):
            assert asyncio.run(handle(client, self._evt(), self._channel(), seen, [0.0])) is True
            assert asyncio.run(handle(client, self._evt(), self._channel(), seen, [0.0])) is False

    def test_error_cooldown_blocks_subsequent_messages(self, bridge_ns):
        handle = bridge_ns["handle_user_message"]
        client = MagicMock()
        client.post = AsyncMock()
        seen: set = set()
        error_until: list = [0.0]
        with patch.dict(bridge_ns, {"call_deer_flow": AsyncMock(
            return_value="[deer-flow error: refused]")}):
            assert asyncio.run(handle(client, self._evt("m1"), self._channel(), seen, error_until)) is True
        assert error_until[0] > 0
        with patch.dict(bridge_ns, {"call_deer_flow": AsyncMock(return_value="ok")}):
            assert asyncio.run(handle(client, self._evt("m2"), self._channel(), seen, error_until)) is False


# -- install script + registry wiring ------------------------------------------

class TestInstallWiring:
    def test_deployer_finds_in_repo_install_script(self):
        """deployer.py prefers scripts/install_<framework>.sh over the catalog
        install.sh; without it no bridge is written and chat never arrives."""
        script = Path(deployer.__file__).parent / "scripts" / "install_deer-flow.sh"
        assert script.exists()

    def test_bridge_service_started_after_httpx_install(self):
        lines = SCRIPT_PATH.read_text().splitlines()
        pip_lines = [i for i, l in enumerate(lines) if re.search(r"^pip3 install .*httpx\b", l)]
        start_lines = [i for i, l in enumerate(lines)
                       if l == "systemctl restart taos-deer-flow-bridge.service"]
        assert pip_lines and start_lines
        assert min(pip_lines) < min(start_lines)

    def test_rerun_restarts_both_services(self):
        """start / enable --now are no-ops on active units, so a re-run would
        leave the old bridge and config running."""
        lines = SCRIPT_PATH.read_text().splitlines()
        assert "systemctl restart deer-flow.service" in lines
        assert "systemctl restart taos-deer-flow-bridge.service" in lines
        assert "systemctl enable taos-deer-flow-bridge.service" in lines
        assert not any(l.startswith("systemctl start ") for l in lines)

    def test_local_token_not_in_unit_file(self):
        """Unit files are world-readable; the token goes in a mode-600 env file."""
        text = SCRIPT_PATH.read_text()
        unit = re.search(r"taos-deer-flow-bridge.service <<UNIT\n(.*?)\nUNIT", text, re.DOTALL)
        assert unit
        assert "TAOS_LOCAL_TOKEN" not in unit.group(1)
        assert "EnvironmentFile=/opt/taos/deer-flow-bridge.env" in unit.group(1)
        assert "chmod 600 /opt/taos/deer-flow-bridge.env" in text

    def test_deer_flow_service_still_provisioned(self):
        text = SCRIPT_PATH.read_text()
        assert "/etc/systemd/system/deer-flow.service" in text
        assert "DEER_FLOW_AUTH_DISABLED=1" in text
        assert "--host 127.0.0.1 --port 8001" in text

    def test_frameworks_entry(self):
        entry = FRAMEWORKS["deer-flow"]
        assert entry["id"] == "deer-flow"
        assert entry["name"]
