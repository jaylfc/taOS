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


@pytest_asyncio.fixture
async def client():
    bridge = create_bridge_app(app_id="blender", mcp_server=None)
    transport = ASGITransport(app=bridge)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
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
