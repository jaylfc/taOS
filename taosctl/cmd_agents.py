"""taosctl agents — manage agents.

Subcommands map 1:1 to /api/agents endpoints. Output is human-readable by
default; pass --json for piping into jq.
"""
from __future__ import annotations

import json
import uuid

import click

from taosctl import http_client


def _print(obj, json_out: bool) -> None:
    if json_out:
        click.echo(json.dumps(obj, indent=2))
        return
    if isinstance(obj, list):
        for item in obj:
            click.echo(_format_summary(item))
    else:
        click.echo(_format_summary(obj))


def _format_summary(agent: dict) -> str:
    has_tok = "[token]" if agent.get("has_token") else "       "
    name = agent.get("name", "?")
    host = agent.get("host", "?")
    qmd = agent.get("qmd_index", "?")
    return f"{has_tok} {name:30s}  {host:20s}  {qmd}"


@click.group(help="Manage taOS agents.")
def agents_group() -> None:
    """taosctl agents."""


@agents_group.command("list", help="List all agents your user can see.")
@click.option("--json", "json_out", is_flag=True, help="Output as JSON.")
def list_cmd(json_out: bool) -> None:
    agents = http_client.get("/api/agents")
    _print(agents, json_out)


@agents_group.command("get", help="Show a single agent (name, host, has_token, ...).")
@click.argument("name")
@click.option("--json", "json_out", is_flag=True)
def get_cmd(name: str, json_out: bool) -> None:
    agent = http_client.get(f"/api/agents/{name}")
    _print(agent, json_out)


@agents_group.command("create", help="Register a new agent. Idempotent via auto-generated Idempotency-Key.")
@click.option("--name", required=True)
@click.option("--host", required=True)
@click.option("--qmd-index", "qmd_index", required=True)
@click.option("--color", default="#888888")
@click.option("--json", "json_out", is_flag=True)
def create_cmd(name: str, host: str, qmd_index: str, color: str, json_out: bool) -> None:
    headers = {"Idempotency-Key": str(uuid.uuid4())}
    body = {"name": name, "host": host, "qmd_index": qmd_index, "color": color}
    agent = http_client.post("/api/agents", json=body, headers=headers)
    _print(agent, json_out)


@agents_group.command("update", help="Update an agent's mutable fields.")
@click.argument("name")
@click.option("--host")
@click.option("--qmd-index", "qmd_index")
@click.option("--color")
@click.option("--emoji")
@click.option("--json", "json_out", is_flag=True)
def update_cmd(name: str, host, qmd_index, color, emoji, json_out: bool) -> None:
    body = {k: v for k, v in {
        "host": host, "qmd_index": qmd_index, "color": color, "emoji": emoji,
    }.items() if v is not None}
    result = http_client.put(f"/api/agents/{name}", json=body)
    _print(result, json_out)


@agents_group.command("start", help="Start a stopped agent's container.")
@click.argument("name")
def start_cmd(name: str) -> None:
    http_client.post(f"/api/agents/{name}/start")
    click.echo(f"started: {name}")


@agents_group.command("stop", help="Stop a running agent's container.")
@click.argument("name")
def stop_cmd(name: str) -> None:
    http_client.post(f"/api/agents/{name}/stop")
    click.echo(f"stopped: {name}")


@agents_group.command("pause", help="Pause an agent without releasing resources.")
@click.argument("name")
def pause_cmd(name: str) -> None:
    http_client.post(f"/api/agents/{name}/pause")
    click.echo(f"paused: {name}")


@agents_group.command("restart", help="Restart an agent.")
@click.argument("name")
def restart_cmd(name: str) -> None:
    http_client.post(f"/api/agents/{name}/restart")
    click.echo(f"restarted: {name}")


@agents_group.command("logs", help="Print recent log lines for an agent.")
@click.argument("name")
@click.option("--lines", default=100, type=int)
def logs_cmd(name: str, lines: int) -> None:
    data = http_client.get(f"/api/agents/{name}/logs", params={"lines": lines})
    for line in data.get("lines", []):
        click.echo(line)


@agents_group.command("delete", help="Archive an agent. Cascades to token revoke.")
@click.argument("name")
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
def delete_cmd(name: str, yes: bool) -> None:
    if not yes:
        click.confirm(f"Archive agent {name!r}? Its token is revoked automatically.", abort=True)
    http_client.delete(f"/api/agents/{name}")
    click.echo(f"archived: {name}")


@agents_group.group("token", help="Manage an agent's API token.")
def token_subgroup() -> None:
    """taosctl agents token."""


@token_subgroup.command("issue", help="Issue a new token (revokes the previous).")
@click.argument("name")
def token_issue_cmd(name: str) -> None:
    result = http_client.post(f"/api/agents/{name}/token/issue")
    click.echo(json.dumps(result, indent=2))
    click.echo("Warning: Token shown only once — save it now.", err=True)


@token_subgroup.command("revoke", help="Revoke the agent's current token.")
@click.argument("name")
def token_revoke_cmd(name: str) -> None:
    http_client.delete(f"/api/agents/{name}/token")
    click.echo(f"token revoked: {name}")
