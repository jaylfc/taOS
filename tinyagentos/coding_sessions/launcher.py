"""Launch and control coding-CLI sessions in tmux.

Slice 2a: the ``host-folder`` target. A detached tmux session on the controller
host, scoped to the session's workdir, running the chosen CLI. Output is read on
demand with ``tmux capture-pane`` and appended to the append-only transcript.
The worker-LXC target, repo cloning, and continuous streaming are later slices.

The tmux calls go through an injectable ``run`` callable so the launcher logic is
unit tested without a real tmux, and live-verified against real tmux separately.
"""

from __future__ import annotations

import os
import subprocess
from typing import Callable

# A runner takes an argv list and returns (returncode, stdout, stderr).
Runner = Callable[[list[str]], "tuple[int, str, str]"]

# CLIs we know how to launch. opencode is wired first (the App Builder already
# drives it); claude and codex use the same tmux path once their binaries exist.
CLI_COMMANDS = {
    "opencode": "opencode",
    "claude": "claude",
    "codex": "codex",
}


def _subprocess_runner(argv: list[str]) -> tuple[int, str, str]:
    # errors="replace": capture-pane of a TUI app can emit bytes that are not
    # valid in the locale encoding; the default errors="strict" would raise
    # UnicodeDecodeError and crash capture(), so decode leniently instead.
    proc = subprocess.run(
        argv, capture_output=True, text=True, timeout=30,
        encoding="utf-8", errors="replace",
    )
    return proc.returncode, proc.stdout, proc.stderr


def tmux_name(session_id: str) -> str:
    """tmux session name for a coding session. Prefixed so it never collides
    with the owl-dispatch tmux sessions; the cs- id has no tmux-illegal chars."""
    return f"taos-cs-{session_id}"


class CodingSessionLauncher:
    """Start, stop, and read a coding CLI running in a detached tmux session."""

    def __init__(self, run: Runner | None = None) -> None:
        self._run = run or _subprocess_runner

    def is_running(self, session_id: str) -> bool:
        rc, _, _ = self._run(["tmux", "has-session", "-t", tmux_name(session_id)])
        return rc == 0

    def start_host_folder(self, session_id: str, workdir: str, cli: str) -> str:
        """Start a detached tmux session running ``cli`` in ``workdir``.

        Returns the tmux session name. Raises ValueError for an unknown CLI and
        RuntimeError if tmux refuses to start (e.g. the workdir does not exist).
        """
        if cli not in CLI_COMMANDS:
            raise ValueError(f"unknown cli {cli!r}")
        # tmux new-session returns 0 even for a missing workdir or a CLI binary
        # that is not found (the window just exits), so validate the workdir here
        # rather than trusting the return code.
        if not os.path.isdir(workdir):
            raise FileNotFoundError(f"workdir does not exist: {workdir}")
        name = tmux_name(session_id)
        rc, out, err = self._run(
            ["tmux", "new-session", "-d", "-s", name, "-c", workdir, CLI_COMMANDS[cli]]
        )
        if rc != 0:
            raise RuntimeError(f"tmux start failed: {(err or out).strip()}")
        return name

    def stop(self, session_id: str) -> None:
        """Kill the session's tmux. Idempotent: a missing session is not an error."""
        self._run(["tmux", "kill-session", "-t", tmux_name(session_id)])

    def capture(self, session_id: str) -> str:
        """Return the current terminal contents, or '' if the session is gone."""
        rc, out, _ = self._run(
            ["tmux", "capture-pane", "-p", "-t", tmux_name(session_id)]
        )
        return out if rc == 0 else ""

    def send_input(self, session_id: str, text: str) -> None:
        """Type ``text`` into the session and press Enter (steering uses this)."""
        self._run(["tmux", "send-keys", "-t", tmux_name(session_id), text, "Enter"])
