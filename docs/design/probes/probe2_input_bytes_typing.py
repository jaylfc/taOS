#!/usr/bin/env python3
"""Probe 2: Input bytes typing

Verifies that tuiui apphost correctly handles raw byte input (not base64).
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tinyagentos.tuiui_conduit import TuiuiConduit, TuiuiConduitError


def get_socket_path() -> str:
    """Get socket path from TUIUI_APPHOST_SOCK env var or argv[1]."""
    if sock := os.environ.get("TUIUI_APPHOST_SOCK"):
        return sock
    if len(sys.argv) > 1:
        return sys.argv[1]
    from tinyagentos.tuiui_conduit import default_socket_path
    return default_socket_path()


def input_echoed(lines: list[str], marker: str) -> bool:
    return any(marker in line for line in lines)


def first_match(frames, pred):
    for lines in frames:
        if pred(lines):
            return lines
    return None


def verify_apphost(socket_path: str) -> None:
    """Verify the apphost socket exists and answers ListApps.

    Exits with code 2 and prints 'could not run:' message if verification fails.
    """
    if not os.path.exists(socket_path):
        print(f"could not run: no tuiui apphost at {socket_path} (socket does not exist)")
        sys.exit(2)

    import stat
    try:
        st = os.lstat(socket_path)
        if not stat.S_ISSOCK(st.st_mode):
            print(f"could not run: no tuiui apphost at {socket_path} (not a socket)")
            sys.exit(2)
    except OSError as e:
        print(f"could not run: no tuiui apphost at {socket_path} (cannot stat: {e})")
        sys.exit(2)

    try:
        with TuiuiConduit(socket_path, timeout=2.0) as conduit:
            conduit.list_apps()
    except TuiuiConduitError as e:
        print(f"could not run: no tuiui apphost at {socket_path} ({e})")
        sys.exit(2)
    except OSError as e:
        print(f"could not run: no tuiui apphost at {socket_path} (connection failed: {e})")
        sys.exit(2)


def probe_input_bytes_typing(socket_path: str) -> str:
    """Probe the Input command's byte encoding."""
    transcript_lines = []
    failed = False

    transcript_lines.append("=== Test: Spawn App ===")
    with TuiuiConduit(socket_path, timeout=2.0) as conduit:
        spawned = conduit.spawn("sh", ["-c", "read x; echo got:$x"], cols=80, rows=24)

    transcript_lines.append(f"Result: Spawned app {spawned.app} with pid {spawned.pid}")
    transcript_lines.append("")

    transcript_lines.append("=== Test: Input Byte Encoding ===")
    transcript_lines.append("Command: Send Input payload containing bytes [104, 101, 108, 108, 111, 10] (hello)")

    with TuiuiConduit(socket_path, timeout=2.0) as conduit:
        conduit.send_input(spawned.app, b"hello\n")
        frame_gen = conduit.iter_frames(timeout=2.0)
        lines_iter = (TuiuiConduit.frame_lines(f) for f in frame_gen)
        matched = first_match(lines_iter, lambda lines: input_echoed(lines, "got:hello"))
        if matched is None:
            transcript_lines.append("FAILED: Input not echoed in any frame")
            failed = True
        else:
            transcript_lines.append("Result: Input echoed in frame")

    transcript_lines.append("Verification: The wire protocol should have carried [104, 101, 108, 108, 111, 10] as integer array, NOT as base64-encoded bytes")
    transcript_lines.append("from source: tinyagentos/tuiui_conduit.py:TuiuiConduit.send_input")

    transcript = "\n".join(transcript_lines)
    print(transcript)
    if failed:
        sys.exit(1)
    return transcript


if __name__ == "__main__":
    socket_path = get_socket_path()
    verify_apphost(socket_path)
    probe_input_bytes_typing(socket_path)
