"""Module entry: ``python -m tinyagentos``.

Honours ``TAOS_HOST`` / ``TAOS_PORT`` env vars (used by the Mac launcher
to bind to a private 127.0.0.1 port) and falls back to ``data/config.yaml``
when they are unset (preserves the existing console-script behaviour).
"""
from __future__ import annotations

import os
from pathlib import Path

from tinyagentos.app import PROJECT_DIR, create_app, load_config


def main() -> None:
    env_host = os.environ.get("TAOS_HOST")
    env_port = os.environ.get("TAOS_PORT")
    env_data_dir = os.environ.get("TAOS_DATA_DIR")

    data_dir = Path(env_data_dir) if env_data_dir else None
    if data_dir is not None:
        _seed_data_dir(data_dir)

    config_path = (data_dir or (PROJECT_DIR / "data")) / "config.yaml"

    config = load_config(config_path)
    if env_host or env_port:
        host = env_host or "127.0.0.1"
        port = int(env_port) if env_port else 6969
    else:
        host = config.server.get("host", "0.0.0.0")
        port = config.server.get("port", 6969)

    # Browser-proxy origin port. Precedence: TAOS_BROWSER_PROXY_PORT env >
    # config server.browser_proxy_port > default 6970. Set to 0 (or empty)
    # to disable the second origin entirely and degrade to single-port:
    # the proxy then stays reachable on the main origin as before.
    env_proxy_port = os.environ.get("TAOS_BROWSER_PROXY_PORT")
    if env_proxy_port is not None:
        proxy_port = int(env_proxy_port) if env_proxy_port.strip() else 0
    else:
        proxy_port = int(config.server.get("browser_proxy_port", 6970) or 0)

    app = create_app(data_dir=data_dir)

    # Record the main origin port so the browser proxy can build a
    # frame-ancestors CSP that lets the shell (main port) embed the
    # proxy origin (proxy port). See proxy._shell_origin.
    if hasattr(app, "state"):
        app.state.main_port = port

    if not proxy_port or proxy_port == port:
        # Single-port fallback: the browser proxy stays on the main origin
        # (as it has historically). No separate-origin / SW isolation, but
        # the app boots and works. Advertise port 0 to the frontend so it
        # builds same-origin proxy URLs (the old behaviour).
        # Guard: real Starlette apps have app.state; bare mocks (tests) may not.
        if hasattr(app, "state"):
            app.state.browser_proxy_port = 0
        import uvicorn

        # backlog=128 — see issue #323. Keeps the kernel accept queue from
        # silently growing into the thousands if the event loop ever wedges.
        uvicorn.run(app, host=host, port=port, backlog=128)
        return

    # Advertise the proxy port to the frontend (see proxy_config route) so it
    # builds the cross-origin redeem URL from the current access host.
    if hasattr(app, "state"):
        app.state.browser_proxy_port = proxy_port

    _serve_dual_port(app, host=host, port=port, proxy_port=proxy_port)


def _serve_dual_port(app, *, host: str, port: int, proxy_port: int) -> None:
    """Run the main app and the browser-proxy origin concurrently.

    ``uvicorn.run`` is blocking and we need two servers, so we drive two
    ``uvicorn.Server`` instances under one event loop via ``asyncio.gather``.

    The proxy-origin app shares the main app's ``app.state`` object (see
    ``create_browser_proxy_app``), which the main app's lifespan populates
    on startup. Both servers start together; the proxy origin only receives
    traffic after the shell has loaded and redeemed a ticket (post-startup),
    so the shared state is always ready by the time it is read.
    """
    import asyncio

    import uvicorn

    from tinyagentos.browser_proxy_origin import create_browser_proxy_app

    proxy_app = create_browser_proxy_app(app.state)

    main_config = uvicorn.Config(app, host=host, port=port, backlog=128)
    proxy_config = uvicorn.Config(proxy_app, host=host, port=proxy_port, backlog=128)
    main_server = uvicorn.Server(main_config)
    proxy_server = uvicorn.Server(proxy_config)

    async def _run() -> None:
        await asyncio.gather(main_server.serve(), proxy_server.serve())

    asyncio.run(_run())


def _seed_data_dir(target: Path) -> None:
    """Copy bundled data/ skeleton into target on first run.

    Existing files are preserved; only missing ones get copied. This lets the
    embedded server boot in ~/Library/Application Support/taOS without the
    user supplying a config.yaml.
    """
    import shutil

    target.mkdir(parents=True, exist_ok=True)
    source = PROJECT_DIR / "data"
    if not source.exists():
        return
    for entry in source.rglob("*"):
        rel = entry.relative_to(source)
        dest = target / rel
        if entry.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
        elif not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(entry, dest)


if __name__ == "__main__":
    main()
