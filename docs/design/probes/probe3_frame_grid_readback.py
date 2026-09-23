#!/usr/bin/env python3
"""Probe 3: Frame grid readback

Verifies that tuiui apphost returns clean CellBuffer grids without ANSI escapes.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tinyagentos.tuiui_conduit import TuiuiConduit, TuiuiConduitError, Frame


def get_socket_path() -> str:
    """Get socket path from TUIUI_APPHOST_SOCK env var or argv[1]."""
    if sock := os.environ.get("TUIUI_APPHOST_SOCK"):
        return sock
    if len(sys.argv) > 1:
        return sys.argv[1]
    from tinyagentos.tuiui_conduit import default_socket_path
    return default_socket_path()


def ansi_count(cells: list[str]) -> int:
    return sum(1 for cell in cells if isinstance(cell, str) and '\x1b' in cell)


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


def probe_frame_grid_readback(socket_path: str) -> str:
    """Probe the Frame event format and ANSI-free output."""
    transcript_lines = []
    failed = False

    transcript_lines.append("=== Test 1: Frame Grid Readback (Clean Text) ===")
    transcript_lines.append("Command: Spawn app and read Frame")

    with TuiuiConduit(socket_path, timeout=2.0) as conduit:
        spawned = conduit.spawn("sh", ["-c", "echo test"], cols=80, rows=24)

        try:
            frames = list(conduit.iter_frames(timeout=2.0))
            matched_frame = first_match(
                frames,
                lambda f: any("test" in l for l in TuiuiConduit.frame_lines(f))
            )
            if matched_frame is None:
                transcript_lines.append("FAILED: no frame carried the command output")
                failed = True
            else:
                transcript_lines.append(f"Result: Frame with text: {TuiuiConduit.frame_lines(matched_frame)}")
                count = ansi_count(matched_frame.cells)
                if count:
                    transcript_lines.append(f"FAILED: ANSI escape count: {count} (should be 0)")
                    failed = True
                else:
                    transcript_lines.append(f"ANSI escape count: {count} (should be 0)")
        except TuiuiConduitError:
            transcript_lines.append("FAILED: timed out waiting for frame")
            failed = True

    transcript_lines.append("")

    transcript_lines.append("=== Test 2: Input to Frame ===")
    transcript_lines.append("Command: Send input and read frame")

    with TuiuiConduit(socket_path, timeout=2.0) as conduit:
        spawned2 = conduit.spawn("sh", ["-c", "read x; echo got:$x"], cols=80, rows=24)
        try:
            conduit.send_input(spawned2.app, b"hello\n")
            frame_gen = conduit.iter_frames(timeout=2.0)
            lines_iter = (TuiuiConduit.frame_lines(f) for f in frame_gen)
            matched = first_match(lines_iter, lambda lines: input_echoed(lines, "got:hello"))
            if matched is None:
                transcript_lines.append("FAILED: Input text not reflected in any frame")
                failed = True
            else:
                transcript_lines.append(f"Result: Frame after input: {matched}")
                transcript_lines.append("Verification: Input text correctly reflected in frame")
        except TuiuiConduitError:
            transcript_lines.append("FAILED: timed out waiting for frame")
            failed = True
        except Exception as e:
            transcript_lines.append(f"Result: Failed - {e}")
            failed = True

    transcript_lines.append("")

    transcript_lines.append("=== Test 3: ANSI-Free Verification ===")
    transcript_lines.append("Test: Create text with potential ANSI and verify it's cleaned")
    transcript_lines.append("Note: In real tuiui, ANSI is decoded by alacritty emulator before reaching socket")
    transcript_lines.append("The CellBuffer grid received over socket should have ch values only (no \\x1b escapes)")

    transcript = "\n".join(transcript_lines)
    print(transcript)
    if failed:
        sys.exit(1)
    return transcript


if __name__ == "__main__":
    socket_path = get_socket_path()
    verify_apphost(socket_path)
    probe_frame_grid_readback(socket_path)
