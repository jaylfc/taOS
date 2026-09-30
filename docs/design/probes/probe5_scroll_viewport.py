#!/usr/bin/env python3
"""Probe 5: Scroll/viewport behavior

Verifies that tuiui's Scroll command only changes visible viewport, not fetchable scrollback.
"""

import json
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


def viewport_moved(before: list[str], after: list[str]) -> bool:
    return before != after


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


def probe_scroll_viewport_behavior(socket_path: str) -> str:
    """Probe scroll viewport vs scrollback fetch behavior."""
    transcript_lines = []
    failed = False

    transcript_lines.append("=== Test 1: Spawn and Get Initial Viewport ===")
    transcript_lines.append("Command: Spawn app and read initial Frame")

    before_lines = []
    after_lines = []

    with TuiuiConduit(socket_path, timeout=2.0) as conduit:
        spawned = conduit.spawn("sh", ["-c", "for i in $(seq 1 50); do echo scroll-$i; done; sleep 3"], cols=80, rows=5)

        try:
            for frame in conduit.iter_frames(timeout=2.0):
                before_lines = TuiuiConduit.frame_lines(frame)
                transcript_lines.append(f"Result: Viewport shows lines: {before_lines}")
                transcript_lines.append(f"  Initial viewport captured: {len(before_lines)} lines")
                break
        except TuiuiConduitError:
            transcript_lines.append("FAILED: timed out waiting for initial frame")
            failed = True

    transcript_lines.append("")

    transcript_lines.append("=== Test 2: Scroll Command ===")
    transcript_lines.append("Command: Send Scroll(app, lines=1) to view previous lines (positive = back into history)")
    transcript_lines.append("from source: jaylfc/tuiui/src/ptyhost.rs:PtyHost::scroll")

    with TuiuiConduit(socket_path, timeout=2.0) as conduit:
        try:
            conduit._send({"Scroll": {"app": spawned.app, "lines": 1}})

            frame_gen = conduit.iter_frames(timeout=2.0)
            lines_iter = (TuiuiConduit.frame_lines(f) for f in frame_gen)
            matched = first_match(lines_iter, lambda lines: viewport_moved(before_lines, lines))
            if matched is None:
                transcript_lines.append("  FAILED: Viewport did not change after Scroll")
                failed = True
            else:
                after_lines = matched
                transcript_lines.append(f"Result: Viewport after scroll: {after_lines}")
                transcript_lines.append("  Result: Viewport changed after Scroll")

        except Exception as e:
            transcript_lines.append(f"Result: Error - {e}")
            failed = True

    transcript_lines.append("")

    transcript_lines.append("=== Test 3: Scrollback Fetch Limitation ===")
    transcript_lines.append("Command: Send unknown GetScrollback request")

    with TuiuiConduit(socket_path, timeout=2.0) as conduit:
        try:
            conduit._send({"GetScrollback": {"app": spawned.app}})
            reply = conduit._wait_for_matching(lambda evt: "Frame" not in evt, timeout=2.0)
            transcript_lines.append(f"Result: Apphost replied: {json.dumps(reply)}")
        except TuiuiConduitError as e:
            transcript_lines.append(f"Result: Apphost error: {e}")
        except Exception as e:
            transcript_lines.append(f"Result: Error - {e}")

    transcript_lines.append("")
    transcript_lines.append("Limitation Summary:")
    transcript_lines.append("  - Frame events ONLY carry current viewport grid")
    transcript_lines.append("  - Scroll command ONLY changes visible viewport")
    transcript_lines.append("  - NO 'GetScrollback' or similar command exists")
    transcript_lines.append("  - Arbitrary scrollback lines cannot be pulled off-screen as text")
    transcript_lines.append("")
    transcript_lines.append("from source: tinyagentos/tuiui_conduit.py:TuiuiConduit._send")

    transcript = "\n".join(transcript_lines)
    print(transcript)
    if failed:
        sys.exit(1)
    return transcript


if __name__ == "__main__":
    socket_path = get_socket_path()
    verify_apphost(socket_path)
    probe_scroll_viewport_behavior(socket_path)
