from __future__ import annotations

import asyncio
import json
import os
import pty
import signal
import struct
import fcntl
import termios
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


def _ws_session_user_id(websocket: WebSocket) -> str | None:
    """Return the user_id from the session cookie, or None if invalid/missing.

    Called before websocket.accept() so an unauthenticated connection is
    rejected without spawning any process.
    """
    auth_mgr = websocket.app.state.auth
    token = websocket.cookies.get("taos_session", "")
    if not token:
        return None
    return auth_mgr.validate_session(token)


def build_command(config: dict) -> list[str]:
    """Build the PTY command from a connection config dict.

    Supported modes:
      - "local" (default): spawn the user's login shell.
      - "ssh": spawn ssh to a remote host. If a password is supplied we
        shell out via `sshpass` (note: sshpass must be installed on the
        host manually; we deliberately do not auto-install it). Key-based
        auth works with stock ssh and needs no extra packages.
    """
    mode = config.get("mode", "local")
    if mode == "ssh":
        host = str(config.get("host", "")).strip()
        port = int(config.get("port", 22) or 22)
        username = str(config.get("username", "")).strip()
        password = config.get("password", "") or ""

        if not host or not username:
            raise ValueError("SSH requires host and username")

        ssh_args = [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-p", str(port),
            f"{username}@{host}",
        ]

        if password:
            # Requires `sshpass` installed on the host (not auto-installed).
            return ["sshpass", "-p", password, *ssh_args]
        return ssh_args

    # Default: local login shell
    shell = os.environ.get("SHELL", "/bin/bash")
    return [shell, "-l"]


@router.websocket("/ws/terminal")
async def terminal_ws(ws: WebSocket):
    user_id = _ws_session_user_id(ws)
    if user_id is None:
        await ws.close(code=1008)
        return

    await ws.accept()

    # Wait briefly for the client's first message. It may be either a
    # "connect" config object (local/ssh) or raw terminal input (legacy).
    first: str | None = None
    try:
        first = await asyncio.wait_for(ws.receive_text(), timeout=5.0)
    except asyncio.TimeoutError:
        first = None
    except WebSocketDisconnect:
        return

    config: dict = {}
    initial_input: str | None = None
    if first is not None:
        try:
            parsed = json.loads(first)
            if isinstance(parsed, dict) and parsed.get("type") == "connect":
                config = parsed
            elif isinstance(parsed, dict) and parsed.get("type") == "resize":
                # Legacy clients send resize first — treat as local shell
                # and apply resize after PTY is ready.
                initial_input = first
            else:
                initial_input = first
        except (json.JSONDecodeError, ValueError):
            initial_input = first

    try:
        cmd = build_command(config)
    except ValueError as e:
        await ws.send_text(f"\r\n\x1b[31mError: {e}\x1b[0m\r\n")
        await ws.close()
        return

    # Create PTY pair
    master_fd, slave_fd = pty.openpty()

    # Fork child process
    pid = os.fork()
    if pid == 0:
        # Child: become session leader, attach to slave PTY
        os.close(master_fd)
        os.setsid()
        os.dup2(slave_fd, 0)
        os.dup2(slave_fd, 1)
        os.dup2(slave_fd, 2)
        if slave_fd > 2:
            os.close(slave_fd)
        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        env["COLORTERM"] = "truecolor"
        env["TAOS_USER_ID"] = user_id  # authenticated user for this PTY session
        try:
            os.execvpe(cmd[0], cmd, env)
        except FileNotFoundError:
            print(f"Command not found: {cmd[0]}", flush=True)
            os._exit(127)
        # Never reaches here

    # Parent: close slave, work with master
    os.close(slave_fd)

    # Non-blocking reads from master
    flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
    fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    # If the first message was a resize or raw input (legacy path),
    # process it now that the PTY exists.
    if initial_input is not None:
        handled = False
        try:
            legacy = json.loads(initial_input)
            if isinstance(legacy, dict) and legacy.get("type") == "resize":
                winsize = struct.pack(
                    "HHHH",
                    legacy.get("rows", 24),
                    legacy.get("cols", 80),
                    0,
                    0,
                )
                fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)
                handled = True
        except (json.JSONDecodeError, ValueError):
            pass
        if not handled:
            try:
                os.write(master_fd, initial_input.encode("utf-8"))
            except OSError:
                pass

    async def pty_reader():
        try:
            while True:
                await asyncio.sleep(0.02)
                try:
                    data = os.read(master_fd, 65536)
                    if data:
                        await ws.send_text(data.decode("utf-8", errors="replace"))
                except BlockingIOError:
                    pass
                except OSError:
                    break
        except asyncio.CancelledError:
            pass

    task = asyncio.create_task(pty_reader())

    try:
        while True:
            msg = await ws.receive_text()
            # Check for resize command
            try:
                cmd_msg = json.loads(msg)
                if isinstance(cmd_msg, dict) and cmd_msg.get("type") == "resize":
                    winsize = struct.pack(
                        "HHHH",
                        cmd_msg.get("rows", 24),
                        cmd_msg.get("cols", 80),
                        0,
                        0,
                    )
                    fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)
                    continue
            except (json.JSONDecodeError, ValueError):
                pass
            os.write(master_fd, msg.encode("utf-8"))
    except WebSocketDisconnect:
        pass
    finally:
        task.cancel()
        try:
            os.kill(pid, signal.SIGTERM)
            os.waitpid(pid, 0)
        except OSError:
            pass
        try:
            os.close(master_fd)
        except OSError:
            pass
