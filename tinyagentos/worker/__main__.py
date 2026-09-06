"""TinyAgentOS Worker — connects this machine to a TinyAgentOS controller."""
import argparse
import os
import sys


def _is_headless_env() -> bool:
    """Detect environments where a system tray is impossible.

    Headless when:
    - explicit --headless flag (handled by caller)
    - Linux without DISPLAY and without WAYLAND_DISPLAY (the systemd /
      docker / lxc / ssh -c case)
    - any platform when stdout isn't a TTY *and* there's no graphical
      session env hint (the curl|bash case)
    """
    if sys.platform == "linux":
        if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
            return True
    return False


def _run_headless(controller: str, name: str | None) -> None:
    import asyncio
    from tinyagentos.worker.agent import WorkerAgent
    agent = WorkerAgent(controller, name)
    try:
        asyncio.run(agent.run())
    except KeyboardInterrupt:
        agent.stop()


def main():
    parser = argparse.ArgumentParser(description="TinyAgentOS Worker")
    parser.add_argument("controller", help="Controller URL (e.g. http://192.168.1.100:6969)")
    parser.add_argument("--name", help="Worker name (default: hostname)")
    parser.add_argument("--headless", action="store_true", help="Run without system tray (server mode)")
    args = parser.parse_args()

    if args.headless or _is_headless_env():
        _run_headless(args.controller, args.name)
        return

    # Try the tray; fall back to headless if pystray (or its system deps)
    # aren't installed. The tray is a desktop convenience; the worker is
    # the load-bearing part.
    try:
        from tinyagentos.worker.tray import run_tray
    except ImportError as exc:
        print(
            f"[worker] tray dependencies unavailable ({exc}); running headless",
            file=sys.stderr,
        )
        _run_headless(args.controller, args.name)
        return

    run_tray(args.controller, args.name)


if __name__ == "__main__":
    main()
