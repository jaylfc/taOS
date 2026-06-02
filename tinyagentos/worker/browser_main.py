from __future__ import annotations

"""Browser-worker entrypoint.

Wires together a BrowserContainerRunner, the browser-worker FastAPI app, and
a WorkerAgent that advertises the 'browser' capability to the taOS controller.

Usage::

    python -m tinyagentos.worker.browser_main \\
        --controller http://taos.local:6969 \\
        --node-ip 10.0.0.5 \\
        [--name my-browser-node] \\
        [--http-api-port 7080] \\
        [--gpu] \\
        [--auth-token <secret>]
"""

import argparse
import asyncio
import logging

from fastapi import FastAPI

from tinyagentos.worker.agent import WorkerAgent
from tinyagentos.worker.browser_container import BrowserContainerRunner
from tinyagentos.worker.browser_server import create_browser_worker_app

logger = logging.getLogger(__name__)


def build(
    controller_url: str,
    *,
    name: str | None = None,
    node_ip: str,
    http_api_port: int = 7080,
    gpu: bool = False,
    auth_token: str | None = None,
) -> tuple[FastAPI, WorkerAgent]:
    """Construct the app + agent pair for the browser-worker.

    Returns ``(app, agent)`` without starting any server or loop.  Callers
    use this for testing; ``serve()`` drives the actual runtime.
    """
    runner = BrowserContainerRunner(node_ip=node_ip, gpu=gpu)
    app = create_browser_worker_app(runner, auth_token=auth_token)
    agent = WorkerAgent(
        controller_url,
        name=name,
        worker_port=http_api_port,
        extra_capabilities=["browser"],
        advertise_url=f"http://{node_ip}:{http_api_port}",
    )
    return app, agent


async def serve(
    controller_url: str,
    *,
    name: str | None = None,
    node_ip: str,
    http_api_port: int = 7080,
    gpu: bool = False,
    auth_token: str | None = None,
) -> None:
    """Run the browser-worker: HTTP API + WorkerAgent heartbeat loop."""
    import uvicorn

    app, agent = build(
        controller_url,
        name=name,
        node_ip=node_ip,
        http_api_port=http_api_port,
        gpu=gpu,
        auth_token=auth_token,
    )

    config = uvicorn.Config(app, host="0.0.0.0", port=http_api_port, log_level="info")
    server = uvicorn.Server(config)

    # Run the WorkerAgent registration + heartbeat loop concurrently with uvicorn.
    agent_task = asyncio.create_task(agent.run())
    try:
        await server.serve()
    finally:
        agent.stop()
        agent_task.cancel()
        try:
            await agent_task
        except asyncio.CancelledError:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="taOS browser-worker")
    parser.add_argument("controller", help="Controller base URL, e.g. http://taos.local:6969")
    parser.add_argument("--name", default=None, help="Worker name (defaults to hostname)")
    parser.add_argument("--node-ip", required=True, help="Reachable IP of this node")
    parser.add_argument("--http-api-port", type=int, default=7080, help="Port for the worker HTTP API")
    parser.add_argument("--gpu", action="store_true", help="Use the GPU-accelerated Neko image")
    parser.add_argument("--auth-token", default=None, help="Shared secret for Bearer auth (optional)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    asyncio.run(
        serve(
            args.controller,
            name=args.name,
            node_ip=args.node_ip,
            http_api_port=args.http_api_port,
            gpu=args.gpu,
            auth_token=args.auth_token,
        )
    )


if __name__ == "__main__":
    main()
