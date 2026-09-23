#!/usr/bin/env python3
"""Probe 4: Detach/reattach AppId stability

Verifies that AppId remains stable across detach/reattach and resets on daemon restart.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tinyagentos.tuiui_conduit import TuiuiConduit, TuiuiConduitError, RosterEntry


def get_socket_path() -> str:
    """Get socket path from TUIUI_APPHOST_SOCK env var or argv[1]."""
    if sock := os.environ.get("TUIUI_APPHOST_SOCK"):
        return sock
    if len(sys.argv) > 1:
        return sys.argv[1]
    from tinyagentos.tuiui_conduit import default_socket_path
    return default_socket_path()


def appid_stable(before: int, after: int | None) -> bool:
    return before == after


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


def probe_detach_reattach_appid(socket_path: str) -> str:
    """Probe AppId stability across detach/reattach and reset on restart."""
    transcript_lines = []
    failed = False
    spawned1 = None

    try:
        transcript_lines.append("=== Test 1: Detach/Reattach AppId Stability ===")
        transcript_lines.append("Step 1: Spawn an app")

        with TuiuiConduit(socket_path, timeout=2.0) as conduit:
            spawned1 = conduit.spawn("sh", ["-c", "sleep 30"], cols=80, rows=24)
            transcript_lines.append(f"  Spawned app {spawned1.app} with pid {spawned1.pid}")

            apps1 = conduit.list_apps()
            transcript_lines.append(f"  Current apps: {[app.app for app in apps1]}")

            conduit.set_meta(spawned1.app, [{"title": "agent-shell", "app_key": "test"}])
            transcript_lines.append(f"  Set meta on app {spawned1.app}")

            transcript_lines.append("  Simulating detach (closing connection)")

        transcript_lines.append("Step 2: Reconnect (reattach)")
        with TuiuiConduit(socket_path, timeout=2.0) as conduit:
            app_after = conduit.rebind_by_meta("agent-shell", app_key="test")

            if app_after:
                transcript_lines.append(f"  Found app via meta: app {app_after.app}")
                transcript_lines.append(f"  Meta title: {app_after.meta[0]['title']}")
                if not appid_stable(spawned1.app, app_after.app):
                    transcript_lines.append(f"  FAILED: AppId changed (before {spawned1.app}, after {app_after.app})")
                    failed = True
                else:
                    transcript_lines.append("  SUCCESS: AppId recovered via meta after reconnect")
            else:
                transcript_lines.append("  FAILED: Could not find app after reconnect")
                failed = True
    finally:
        if spawned1 is not None:
            transcript_lines.append(f"  Killing long-lived app {spawned1.app}")
            with TuiuiConduit(socket_path, timeout=2.0) as conduit:
                conduit.kill(spawned1.app)

    transcript_lines.append("")

    transcript_lines.append("=== Test 2: AppId Reset on Daemon Restart ===")
    transcript_lines.append("not run: needs a daemon restart")
    transcript_lines.append("from source: tinyagentos/tuiui_conduit.py:TuiuiConduit.rebind_by_meta")
    transcript_lines.append("")

    transcript = "\n".join(transcript_lines)
    print(transcript)
    if failed:
        sys.exit(1)
    return transcript


if __name__ == "__main__":
    socket_path = get_socket_path()
    verify_apphost(socket_path)
    probe_detach_reattach_appid(socket_path)
