"""taosctl auth — token management commands."""
from __future__ import annotations

import json
import os
import sys

import click
import httpx

from taosctl import config, http_client


@click.group(help="Authentication: log in, check status, identify the token bearer.")
def auth_group() -> None:
    """taosctl auth."""


@auth_group.command("login", help="Save a token to ~/.config/taos/credentials (mode 0600).")
@click.option(
    "--token",
    prompt="taOS bearer token",
    hide_input=True,
    help="The bearer token (begins with taos_agent_).",
)
def login_cmd(token: str) -> None:
    """Save the bearer token for future taosctl invocations."""
    path = config.CREDENTIALS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"token": token}), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        # Windows / unusual filesystems — env-var fallback recommended there.
        pass
    click.echo(f"Token saved to {path}.")


@auth_group.command("status", help="Check whether a token is configured and reachable.")
def status_cmd() -> None:
    token = config.resolve_token()
    if not token:
        click.echo(
            "no token configured (set TAOS_TOKEN or run `taosctl auth login`)",
            err=True,
        )
        sys.exit(2)
    try:
        info = http_client.get("/api/auth/whoami")
        click.echo(
            f"OK — user_id={info.get('user_id')} agent_id={info.get('agent_id')}"
        )
    except httpx.HTTPStatusError as e:
        click.echo(f"token rejected: {e.response.status_code}", err=True)
        sys.exit(2)
    except httpx.HTTPError as e:
        click.echo(f"cannot reach controller: {e}", err=True)
        sys.exit(2)


@auth_group.command("whoami", help="Show the bearer's user_id, agent_id, and scope.")
def whoami_cmd() -> None:
    info = http_client.get("/api/auth/whoami")
    click.echo(json.dumps(info, indent=2))
