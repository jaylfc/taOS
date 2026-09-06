"""Module entry: ``python -m tinyagentos``.

Honours ``TAOS_HOST`` / ``TAOS_PORT`` env vars (used by the Mac launcher
to bind to a private 127.0.0.1 port) and falls back to ``data/config.yaml``
when they are unset (preserves the existing console-script behaviour).
"""
from __future__ import annotations

import os
from pathlib import Path

from tinyagentos.app import PROJECT_DIR, create_app, load_config

# Bound uvicorn's graceful-shutdown wait for open connections on SIGTERM.
# Long-lived SSE streams + cluster heartbeats would otherwise keep uvicorn
# waiting indefinitely, hanging the full 45s systemd stop timeout each restart.
GRACEFUL_SHUTDOWN_SECS = 5


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
        # timeout_graceful_shutdown — without it uvicorn waits indefinitely for
        # long-lived connections (SSE streams, cluster heartbeats) to close on
        # SIGTERM, so a restart hung the full 45s systemd stop timeout. Bound it
        # so the lifespan shutdown actually runs and the process exits fast.
        uvicorn.run(
            app, host=host, port=port, backlog=128, timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECS
        )
        return

    # Advertise the proxy port to the frontend (see proxy_config route) so it
    # builds the cross-origin redeem URL from the current access host.
    if hasattr(app, "state"):
        app.state.browser_proxy_port = proxy_port

    _serve_dual_port(app, host=host, port=port, proxy_port=proxy_port)


def _serve_dual_port(app, *, host: str, port: int, proxy_port: int) -> None:
    """Run the main app and the browser-proxy origin concurrently.

    ``uvicorn.run`` is blocking and we need two servers, so we drive two
    ``uvicorn.Server`` instances under one event loop.

    The proxy-origin app shares the main app's ``app.state`` object (see
    ``create_browser_proxy_app``), which the main app's lifespan populates
    on startup. Both servers start together; the proxy origin only receives
    traffic after the shell has loaded and redeemed a ticket (post-startup),
    so the shared state is always ready by the time it is read.

    If the main server's serve() returns without having reached the
    'started' state (lifespan raised during startup), we exit non-zero so
    systemd's Restart= fires instead of leaving a half-alive process.
    """
    import asyncio
    import logging

    import uvicorn

    from tinyagentos.browser_proxy_origin import create_browser_proxy_app

    _log = logging.getLogger(__name__)

    proxy_app = create_browser_proxy_app(app.state)

    # timeout_graceful_shutdown: bound the wait for open connections on SIGTERM
    # (see the single-port path above) so neither server hangs the 45s stop.
    main_config = uvicorn.Config(
        app, host=host, port=port, backlog=128, timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECS
    )
    proxy_config = uvicorn.Config(
        proxy_app, host=host, port=proxy_port, backlog=128, timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECS
    )
    # Two uvicorn servers under one loop each install their OWN SIGTERM handler,
    # and the second registration silently overrides the first -- so on SIGTERM
    # only the proxy server got should_exit, the main server never did, and the
    # FIRST_COMPLETED+cancel path then force-cancelled the main server mid-serve,
    # which hung the full 45s systemd stop. Neuter uvicorn's per-server signal
    # capture and drive shutdown ourselves with one unified handler below.
    import contextlib

    class _NoSignalServer(uvicorn.Server):
        @contextlib.contextmanager
        def capture_signals(self):
            # Shutdown is driven by the unified handler in _serve_until_first_exit.
            yield

    main_server = _NoSignalServer(main_config)
    proxy_server = _NoSignalServer(proxy_config)

    started = asyncio.run(_serve_until_first_exit(main_server, proxy_server))
    if not started:
        _log.error(
            "Main server failed to start on %s:%d -- check lifespan errors above",
            host,
            port,
        )
        raise SystemExit(3)


async def _serve_until_first_exit(main_server, proxy_server) -> bool:
    """Drive both servers; on shutdown signal exit BOTH gracefully.

    One unified SIGTERM/SIGINT handler flips should_exit on both servers so they
    each shut down gracefully (bounded by timeout_graceful_shutdown). When one
    serve() returns first (e.g. a startup failure), the survivor is asked to exit
    gracefully and awaited with a bound, falling back to cancel only if it does
    not stop in time -- never an unconditional force-cancel mid-serve.

    Returns True when the main server reached the started state before
    either serve() returned, False otherwise (startup failure).
    """
    import asyncio
    import contextlib
    import signal

    loop = asyncio.get_running_loop()

    def _request_shutdown() -> None:
        main_server.should_exit = True
        proxy_server.should_exit = True

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _request_shutdown)
        except (NotImplementedError, RuntimeError):
            # Windows / non-main-thread: fall back to default behaviour.
            pass

    main_task = asyncio.create_task(main_server.serve())
    proxy_task = asyncio.create_task(proxy_server.serve())

    done, pending = await asyncio.wait(
        {main_task, proxy_task},
        return_when=asyncio.FIRST_COMPLETED,
    )

    # Ask the survivor to stop gracefully, then await it with a bound so a stuck
    # graceful shutdown cannot hang the process; only cancel as a last resort.
    for task, server in ((main_task, main_server), (proxy_task, proxy_server)):
        if task in pending:
            server.should_exit = True
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=GRACEFUL_SHUTDOWN_SECS + 3)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    for task in done:
        if task.exception() is not None:
            raise task.exception()

    return bool(getattr(main_server, "started", False))


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
