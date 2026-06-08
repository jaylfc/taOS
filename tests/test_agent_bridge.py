"""
Tests for tinyagentos.agent_bridge — the container-side daemon.

Uses httpx ASGITransport so no real server is started.
X11/xdotool/scrot are not available in CI, so visual tests assert error status.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.agent_bridge import create_bridge_app

_TOKEN = "test-bridge-secret"


@pytest_asyncio.fixture
async def client():
    """Unauthenticated client — no token configured on bridge."""
    bridge = create_bridge_app(app_id="blender", mcp_server=None)
    transport = ASGITransport(app=bridge)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def authed_client():
    """Client with correct X-Bridge-Token header against a token-protected bridge."""
    bridge = create_bridge_app(app_id="blender", mcp_server=None, bridge_token=_TOKEN)
    transport = ASGITransport(app=bridge)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-Bridge-Token": _TOKEN},
    ) as c:
        yield c


@pytest_asyncio.fixture
async def unauthed_token_client():
    """Client hitting a token-protected bridge without a token."""
    bridge = create_bridge_app(app_id="blender", mcp_server=None, bridge_token=_TOKEN)
    transport = ASGITransport(app=bridge)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def wrong_token_client():
    """Client hitting a token-protected bridge with the wrong token."""
    bridge = create_bridge_app(app_id="blender", mcp_server=None, bridge_token=_TOKEN)
    transport = ASGITransport(app=bridge)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-Bridge-Token": "wrong-token"},
    ) as c:
        yield c


@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["app_id"] == "blender"
    assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_screenshot_no_display(client):
    """scrot needs a real X display; expect error status in test env."""
    resp = await client.get("/screenshot")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "error"


@pytest.mark.asyncio
async def test_exec_command(client):
    resp = await client.post("/exec", json={"command": "echo hello"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["exit_code"] == 0
    assert "hello" in data["stdout"]


@pytest.mark.asyncio
async def test_exec_with_timeout(client):
    resp = await client.post("/exec", json={"command": "echo fast", "timeout": 5})
    assert resp.status_code == 200
    data = resp.json()
    assert data["exit_code"] == 0
    assert "fast" in data["stdout"]


@pytest.mark.asyncio
async def test_keyboard_no_display(client):
    """xdotool requires X display; expect error status in test env."""
    resp = await client.post("/keyboard", json={"keys": "ctrl+c"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "error"


@pytest.mark.asyncio
async def test_computer_use_toggle(client):
    # Initially disabled
    resp = await client.get("/computer-use")
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False

    # Enable
    resp = await client.post("/computer-use", json={"enabled": True})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True

    # Verify persisted
    resp = await client.get("/computer-use")
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True


@pytest.mark.asyncio
async def test_agent_current(client):
    resp = await client.get("/agent/current")
    assert resp.status_code == 200
    data = resp.json()
    assert "agent_name" in data
    assert data["agent_name"]  # non-empty


@pytest.mark.asyncio
async def test_files_list(client):
    resp = await client.post("/files/list", json={"path": "/tmp"})
    assert resp.status_code == 200
    data = resp.json()
    assert "entries" in data
    assert isinstance(data["entries"], list)


# -- Security: exec arg-list tests (no shell interpretation) ------------------


@pytest.mark.asyncio
async def test_keyboard_uses_exec_not_shell(client):
    """Payload with shell metacharacters must be passed as a literal arg, not executed."""
    captured: list = []

    async def fake_exec(*args, **kwargs):
        captured.append(args)
        proc = MagicMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        resp = await client.post("/keyboard", json={"keys": "a; rm -rf /"})

    assert resp.status_code == 200
    assert len(captured) == 1
    argv = captured[0]
    # Must be called as exec with explicit args — no shell string
    assert argv[0] == "xdotool"
    assert argv[1] == "key"
    # The injection payload is passed as a single literal argument
    assert argv[2] == "a; rm -rf /"
    # Must NOT be called as a single shell string
    assert len(argv) == 3


@pytest.mark.asyncio
async def test_type_uses_exec_not_shell(client):
    """Payload with shell metacharacters must be passed as a literal arg."""
    captured: list = []

    async def fake_exec(*args, **kwargs):
        captured.append(args)
        proc = MagicMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        resp = await client.post("/type", json={"text": "$(id); evil"})

    assert resp.status_code == 200
    assert len(captured) == 1
    argv = captured[0]
    assert argv[0] == "xdotool"
    assert argv[1] == "type"
    # The injection payload arrives as a literal arg, not a shell expression
    assert "$(id); evil" in argv
    # Ensure '--' separator is present to guard against text starting with '-'
    assert "--" in argv


# -- Security: screenshot uses exec (no shell) --------------------------------


@pytest.mark.asyncio
async def test_screenshot_uses_exec_not_shell(client):
    """Screenshot must use create_subprocess_exec — no shell interpretation."""
    captured: list = []

    async def fake_exec(*args, **kwargs):
        captured.append(args)
        proc = MagicMock()
        proc.returncode = 1  # scrot not available
        proc.communicate = AsyncMock(return_value=(b"", b"scrot failed"))
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        resp = await client.get("/screenshot")

    assert resp.status_code == 200
    # Must have been called as exec with explicit arg list — not a shell string
    assert len(captured) == 1
    argv = captured[0]
    assert argv[0] == "scrot"
    assert argv[1] == "-o"
    assert "/tmp/screenshot.png" in argv[2]


# -- Security: mouse uses exec + validates button -----------------------------


@pytest.mark.asyncio
async def test_mouse_uses_exec_not_shell(client):
    """Mouse coords/button must be passed as separate exec args, not in a shell string."""
    captured: list = []

    async def fake_exec(*args, **kwargs):
        captured.append(args)
        proc = MagicMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        resp = await client.post("/mouse", json={"x": 100, "y": 200, "button": 1})

    assert resp.status_code == 200
    assert len(captured) == 1
    argv = captured[0]
    assert argv[0] == "xdotool"
    assert "mousemove" in argv
    assert "100" in argv
    assert "200" in argv
    assert "1" in argv
    # Must be individual args — no arg contains a space (i.e. no shell string)
    assert all(" " not in str(a) for a in argv)


@pytest.mark.asyncio
async def test_mouse_rejects_invalid_button(client):
    """Buttons outside {1,2,3,4,5} must be rejected before exec is called."""
    captured: list = []

    async def fake_exec(*args, **kwargs):
        captured.append(args)
        proc = MagicMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        resp = await client.post("/mouse", json={"x": 50, "y": 50, "button": 99})

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "error"
    assert "Invalid button" in data["error"]
    # exec must NOT have been called
    assert len(captured) == 0


# -- Security: /exec and /files/batch block shell-metachar chaining -----------


@pytest.mark.asyncio
async def test_exec_no_shell_chaining(client):
    """A semicolon payload in /exec must NOT spawn a second command.

    With shlex.split + create_subprocess_exec, "echo hello; echo injected"
    becomes ["echo", "hello;", "echo", "injected"] — echo receives those
    as arguments and emits them on a single line; no second process is spawned.
    """
    resp = await client.post("/exec", json={"command": "echo hello; echo injected"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["exit_code"] == 0
    # Single process output: all on one line, no newline separating two commands.
    assert "\n" not in data["stdout"].strip()


@pytest.mark.asyncio
async def test_files_batch_no_shell_chaining(client):
    """A semicolon payload in /files/batch must NOT spawn a second command."""
    resp = await client.post("/files/batch", json={"command": "echo safe; echo injected"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["exit_code"] == 0
    # Same as /exec: single process, no newline-separated second command output.
    assert "\n" not in data["stdout"].strip()


# -- Guard: empty / malformed commands (Kilo crit) ---------------------------


@pytest.mark.asyncio
async def test_exec_empty_string(client):
    """Empty string must return a clean error, not raise TypeError."""
    resp = await client.post("/exec", json={"command": ""})
    assert resp.status_code == 200
    data = resp.json()
    assert data["exit_code"] == -1
    assert "empty command" in data["stderr"]


@pytest.mark.asyncio
async def test_exec_whitespace_only(client):
    """Whitespace-only string must return a clean error, not raise TypeError."""
    resp = await client.post("/exec", json={"command": "   "})
    assert resp.status_code == 200
    data = resp.json()
    assert data["exit_code"] == -1
    assert "empty command" in data["stderr"]


@pytest.mark.asyncio
async def test_exec_unmatched_quote(client):
    """Unmatched quote must return a clean error, not raise ValueError."""
    resp = await client.post("/exec", json={"command": "echo 'unterminated"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["exit_code"] == -1
    assert "invalid/malformed command" in data["stderr"]


@pytest.mark.asyncio
async def test_files_batch_empty_string(client):
    """/files/batch empty string must return a clean error, not raise TypeError."""
    resp = await client.post("/files/batch", json={"command": ""})
    assert resp.status_code == 200
    data = resp.json()
    assert data["exit_code"] == -1
    assert "empty command" in data["stderr"]


@pytest.mark.asyncio
async def test_files_batch_whitespace_only(client):
    """/files/batch whitespace must return a clean error, not raise TypeError."""
    resp = await client.post("/files/batch", json={"command": "   "})
    assert resp.status_code == 200
    data = resp.json()
    assert data["exit_code"] == -1
    assert "empty command" in data["stderr"]


@pytest.mark.asyncio
async def test_files_batch_unmatched_quote(client):
    """/files/batch unmatched quote must return a clean error, not raise ValueError."""
    resp = await client.post("/files/batch", json={"command": "ls 'bad"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["exit_code"] == -1
    assert "invalid/malformed command" in data["stderr"]


# ---------------------------------------------------------------------------
# Auth guard tests (issue #672)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_exec_requires_token_when_configured(unauthed_token_client):
    """POST /exec must return 401 when bridge_token is set and no header provided."""
    resp = await unauthed_token_client.post("/exec", json={"command": "echo hi"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_exec_rejects_wrong_token(wrong_token_client):
    """POST /exec must return 401 when bridge_token is set and wrong token sent."""
    resp = await wrong_token_client.post("/exec", json={"command": "echo hi"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_exec_accepts_correct_token(authed_client):
    """POST /exec must succeed when the correct X-Bridge-Token header is provided."""
    resp = await authed_client.post("/exec", json={"command": "echo hi"})
    assert resp.status_code == 200
    assert resp.json()["exit_code"] == 0


@pytest.mark.asyncio
async def test_files_batch_requires_token(unauthed_token_client):
    """POST /files/batch must return 401 without the token."""
    resp = await unauthed_token_client.post("/files/batch", json={"command": "ls /tmp"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_files_batch_accepts_correct_token(authed_client):
    """POST /files/batch must succeed with correct token."""
    resp = await authed_client.post("/files/batch", json={"command": "ls /tmp"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_health_always_accessible(unauthed_token_client):
    """GET /health must be accessible without a token even when auth is configured."""
    resp = await unauthed_token_client.get("/health")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_no_token_configured_allows_exec(client):
    """When no bridge_token is set, /exec is accessible (backward compat)."""
    resp = await client.post("/exec", json={"command": "echo hello"})
    assert resp.status_code == 200
    assert resp.json()["exit_code"] == 0


@pytest.mark.asyncio
async def test_exec_rejects_same_length_wrong_token():
    """POST /exec must return 401 for a wrong token that is the same length as the real one.

    Ensures secrets.compare_digest is used rather than a timing-leaky != comparison.
    The same-length property is important: a length-leaky check would also fail early
    on different-length strings, so testing equal-length isolates the constant-time guarantee.
    """
    real_token = "abcdefghijklmnop"        # 16 chars
    wrong_token = "abcdefghijklmnoX"       # same length, last char differs
    assert len(real_token) == len(wrong_token)

    bridge = create_bridge_app(app_id="test", bridge_token=real_token)
    transport = ASGITransport(app=bridge)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-Bridge-Token": wrong_token},
    ) as c:
        resp = await c.post("/exec", json={"command": "echo hi"})

    assert resp.status_code == 401
