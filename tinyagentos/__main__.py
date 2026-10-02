"""Module entry: ``python -m tinyagentos``.

Honours ``TAOS_HOST`` / ``TAOS_PORT`` env vars (used by the Mac launcher
to bind to a private 127.0.0.1 port) and falls back to ``data/config.yaml``
when they are unset (preserves the existing console-script behaviour).
"""
from __future__ import annotations

import os
from pathlib import Path

from tinyagentos.app import PROJECT_DIR, create_app, load_config
from tinyagentos.device_scopes import device_tls_port
from tinyagentos.logging_config import configure_logging

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

    import logging
    _log = logging.getLogger(__name__)

    tls_port = device_tls_port()
    if tls_port in {port, proxy_port}:
        _log.error(
            "TLS device listener port %d collides with main port %d or proxy port %d; TLS listener disabled",
            tls_port, port, proxy_port,
        )
        tls_port = 0

    # LLM gateway agent listener (loopback only): where each agent's
    # 127.0.0.1:4000 proxy device points. The startup reconcile moves any
    # device still on an old LiteLLM port here.
    gateway_port = _gateway_listener_port(config, taken={port, proxy_port, tls_port})
    if hasattr(app, "state"):
        import secrets

        app.state.llm_gateway_agent_port = gateway_port
        # Per-start nonce the listener stamps on every response; the startup
        # reconcile moves agents only onto a port that answers with it.
        app.state.llm_gateway_listener_identity = secrets.token_urlsafe(24)

    if not proxy_port or proxy_port == port:
        # Single-port fallback: the browser proxy stays on the main origin
        # (as it has historically). No separate-origin / SW isolation, but
        # the app boots and works. Advertise port 0 to the frontend so it
        # builds same-origin proxy URLs (the old behaviour).
        # Guard: real Starlette apps have app.state; bare mocks (tests) may not.
        if hasattr(app, "state"):
            app.state.browser_proxy_port = 0
        import uvicorn

        configure_logging()

        # backlog=128 -- see issue #323. Keeps the kernel accept queue from
        # silently growing into the thousands if the event loop ever wedges.
        # timeout_graceful_shutdown -- without it uvicorn waits indefinitely for
        # long-lived connections (SSE streams, cluster heartbeats) to close on
        # SIGTERM, so a restart hung the full 45s systemd stop timeout. Bound it
        # so the lifespan shutdown actually runs and the process exits fast.
        if not _gateway_listener_wanted(gateway_port) and not tls_port:
            uvicorn.run(
                app, host=host, port=port, backlog=128, timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECS
            )
            return
        _serve_dual_port(app, host=host, port=port, proxy_port=0,
                         gateway_port=gateway_port if _gateway_listener_wanted(gateway_port) else 0,
                         tls_port=tls_port)
        return

    # Advertise the proxy port to the frontend (see proxy_config route) so it
    # builds the cross-origin redeem URL from the current access host.
    if hasattr(app, "state"):
        app.state.browser_proxy_port = proxy_port

    _serve_dual_port(app, host=host, port=port, proxy_port=proxy_port,
                     gateway_port=gateway_port if _gateway_listener_wanted(gateway_port) else 0,
                     tls_port=tls_port)


def _gateway_listener_port(config, *, taken: set) -> int:
    """The agent listener port, or 0 when unset / clashing with our own ports."""
    import logging

    from tinyagentos import llm_gateway

    try:
        gw = llm_gateway.agent_port(config)
    except (TypeError, ValueError):
        logging.getLogger(__name__).error("llm gateway: invalid agent listener port; listener disabled")
        return 0
    if gw and gw in taken:
        logging.getLogger(__name__).error(
            "llm gateway: agent listener port %d clashes with a controller port; listener disabled", gw,
        )
        return 0
    return gw


def _gateway_listener_wanted(gateway_port: int) -> bool:
    """True unless the listener port is 0 (disabled or clashing).

    The gateway itself is always on since LiteLLM removal 2b-2a."""
    return bool(gateway_port)


def _serve_dual_port(app, *, host: str, port: int, proxy_port: int, gateway_port: int = 0, tls_port: int = 0) -> None:
    """Run the main app and the browser-proxy origin concurrently.

    ``uvicorn.run`` is blocking and we need two or three servers, so we drive
    two or three ``uvicorn.Server`` instances under one event loop.

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

    # timeout_graceful_shutdown: bound the wait for open connections on SIGTERM
    # (see the single-port path above) so neither server hangs the 45s stop.
    main_config = uvicorn.Config(
        app, host=host, port=port, backlog=128, timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECS
    )
    proxy_config = None
    if proxy_port:
        proxy_app = create_browser_proxy_app(app.state)
        proxy_config = uvicorn.Config(
            proxy_app, host=host, port=proxy_port, backlog=128, timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECS
        )
    gateway_config = None
    if gateway_port:
        # Loopback ONLY, whatever the main bind is: incus proxy devices reach
        # it from the host side, and nothing off-host may.
        from tinyagentos.llm_gateway.listener import create_agent_listener_app

        gateway_config = uvicorn.Config(
            create_agent_listener_app(
                app, identity=getattr(app.state, "llm_gateway_listener_identity", None),
            ),
            host="127.0.0.1", port=gateway_port, backlog=128, lifespan="off",
            timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECS,
        )
    tls_config = None
    if tls_port:
        _tls_cert = getattr(app.state, "device_tls_cert_path", None)
        _tls_key = getattr(app.state, "device_tls_key_path", None)
        if _tls_cert and _tls_key:
            tls_config = uvicorn.Config(
                app, host=host, port=tls_port, backlog=128,
                ssl_certfile=_tls_cert, ssl_keyfile=_tls_key,
                lifespan="off",
                timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECS,
            )
        else:
            _log.error(
                "TLS device listener enabled on port %d but cert/key not found; TLS listener disabled",
                tls_port,
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
    proxy_server = _NoSignalServer(proxy_config) if proxy_config is not None else None
    tls_server = _NoSignalServer(tls_config) if tls_config is not None else None
    sidecars = (_NoSignalServer(gateway_config),) if gateway_config is not None else ()

    started = asyncio.run(_serve_until_first_exit(main_server, proxy_server, sidecars=sidecars, tls_server=tls_server))
    if not started:
        _log.error(
            "Main server failed to start on %s:%d -- check lifespan errors above",
            host,
            port,
        )
        raise SystemExit(3)


async def _serve_sidecar(server, *, name: str) -> None:
    """Serve a non-essential server (LLM gateway agent listener, TLS device listener).

    Its failure, including uvicorn's ``sys.exit(1)`` on a port already in use,
    is logged and swallowed here, inside the coroutine, so it can never take
    the controller down.
    """
    import logging

    try:
        await server.serve()
    except (Exception, SystemExit) as exc:  # noqa: BLE001 - never fatal
        logging.getLogger(__name__).error(
            "%s listener stopped (%s); %s",
            name,
            type(exc).__name__,
            "agents have no LLM path until it is back" if name == "llm gateway agent" else "embedded devices will be unable to connect",
        )


async def _serve_until_first_exit(main_server, proxy_server=None, *, sidecars=(), tls_server=None) -> bool:
    """Drive both servers; on shutdown signal exit BOTH gracefully.

    ``proxy_server`` may be None (single-port mode). ``sidecars`` are served
    alongside but are never fatal (see ``_serve_sidecar``): they stop when the
    main server stops, and their own exit does not stop anything.

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

    essential = [main_server] + ([proxy_server] if proxy_server is not None else [])
    # tls_server is non-essential (like sidecars): its failure is logged but
    # does not take the controller down. It is still included in the shutdown
    # fan-out so it stops gracefully when the main server stops.
    all_servers = (*essential, *([tls_server] if tls_server is not None else []), *sidecars)

    def _request_shutdown() -> None:
        for server in all_servers:
            server.should_exit = True

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _request_shutdown)
        except (NotImplementedError, RuntimeError):
            # Windows / non-main-thread: fall back to default behaviour.
            pass

    tasks = [(asyncio.create_task(s.serve()), s) for s in essential]
    tls_task = None
    if tls_server is not None:
        tls_task = (asyncio.create_task(_serve_sidecar(tls_server, name="TLS device")), tls_server)
    side = [(asyncio.create_task(_serve_sidecar(s, name="llm gateway agent")), s) for s in sidecars]

    done, pending = await asyncio.wait(
        {t for t, _ in tasks},
        return_when=asyncio.FIRST_COMPLETED,
    )

    # Ask the survivors (sidecars and tls_server included) to stop gracefully,
    # then await each with a bound so a stuck graceful shutdown cannot hang
    # the process; only cancel as a last resort.
    all_server_tasks = list(tasks)
    if tls_task is not None:
        all_server_tasks.append(tls_task)
    all_server_tasks.extend(side)

    for task, server in all_server_tasks:
        if not task.done():
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
