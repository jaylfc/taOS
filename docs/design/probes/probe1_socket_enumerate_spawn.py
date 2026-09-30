#!/usr/bin/env python3
"""Probe 1: socket enumerate/spawn

Verifies the tuiui apphost Unix socket protocol for listing apps and spawning new ones.
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

    if not os.path.isfile(socket_path):
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


def probe_socket_enumerate_spawn(socket_path: str) -> str:
    """Probe the ListApps and Spawn functionality over the Unix socket."""
    transcript_lines = []
    failed = False

    transcript_lines.append("=== Test 1: ListApps (empty) ===")
    with TuiuiConduit(socket_path, timeout=2.0) as conduit:
        apps = conduit.list_apps()

    transcript_lines.append(f"Result: {len(apps)} apps")
    for app in apps:
        transcript_lines.append(f"  App {app.app}: cmd={app.cmd}, pid={app.pid}, alive={app.alive}")

    transcript_lines.append("")

    transcript_lines.append("=== Test 2: Spawn ===")
    with TuiuiConduit(socket_path, timeout=2.0) as conduit:
        spawned = conduit.spawn("sh", ["-c", "echo hello"], cols=80, rows=24)

    transcript_lines.append(f"Result: spawned app {spawned.app} with pid {spawned.pid}")

    transcript_lines.append("")

    transcript_lines.append("=== Test 3: ListApps (with app) ===")
    with TuiuiConduit(socket_path, timeout=2.0) as conduit:
        apps = conduit.list_apps()

    transcript_lines.append(f"Result: {len(apps)} apps")
    for app in apps:
        transcript_lines.append(f"  App {app.app}: cmd={app.cmd}, pid={app.pid}, alive={app.alive}")

    transcript_lines.append("")

    transcript_lines.append("=== Test 4: Send Input ===")
    with TuiuiConduit(socket_path, timeout=2.0) as conduit:
        spawned2 = conduit.spawn("sh", ["-c", "read x; echo got:$x"], cols=80, rows=24)
        try:
            conduit.send_input(spawned2.app, b"hello\n")
            frame_gen = conduit.iter_frames(timeout=2.0)
            lines_iter = (TuiuiConduit.frame_lines(f) for f in frame_gen)
            matched = first_match(lines_iter, lambda lines: input_echoed(lines, "got:hello"))
            if matched is None:
                transcript_lines.append("FAILED: Input not echoed in any frame")
                failed = True
            else:
                transcript_lines.append("Result: Input echoed in frame")
        except TuiuiConduitError:
            transcript_lines.append("FAILED: timed out waiting for frame")
            failed = True
        except Exception as e:
            transcript_lines.append(f"Result: Failed - {e}")
            failed = True

    transcript_lines.append("")

    transcript = "\n".join(transcript_lines)
    print(transcript)
    if failed:
        sys.exit(1)
    return transcript


if __name__ == "__main__":
    socket_path = get_socket_path()
    verify_apphost(socket_path)
    probe_socket_enumerate_spawn(socket_path)
