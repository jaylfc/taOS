"""taosctl tasks -- inspect and manage scheduled tasks."""
from __future__ import annotations

NOUN = "tasks"


def register(subparsers) -> None:
    p = subparsers.add_parser(NOUN, help="Inspect and manage scheduled tasks")
    verbs = p.add_subparsers(dest="verb", required=True, metavar="<verb>")

    lp = verbs.add_parser("list", help="List all tasks")
    lp.add_argument("--agent", help="Filter by agent name")
    lp.set_defaults(func=_list)

    gp = verbs.add_parser("get", help="Get one task by id")
    gp.add_argument("id", help="Task id")
    gp.set_defaults(func=_get)

    cp = verbs.add_parser("create", help="Create a new task")
    cp.add_argument("--name", required=True, help="Task name")
    cp.add_argument("--schedule", required=True, help="Cron expression")
    cp.add_argument("--command", required=True, help="Command to run")
    cp.add_argument("--agent-name", help="Agent to run as")
    cp.add_argument("--description", default="", help="Task description")
    cp.set_defaults(func=_create)

    up = verbs.add_parser("update", help="Update an existing task")
    up.add_argument("id", help="Task id")
    up.add_argument("--name", help="Task name")
    up.add_argument("--schedule", help="Cron expression")
    up.add_argument("--command", help="Command to run")
    up.add_argument("--description", help="Task description")
    up.add_argument("--enabled", help="true or false")
    up.set_defaults(func=_update)

    dp = verbs.add_parser("delete", help="Delete a task")
    dp.add_argument("id", help="Task id")
    dp.set_defaults(func=_delete)

    tp = verbs.add_parser("toggle", help="Toggle a task enabled/disabled")
    tp.add_argument("id", help="Task id")
    tp.set_defaults(func=_toggle)

    # Skipped: GET /api/tasks/presets (list_presets) -- no matching verb needed yet
    # Skipped: POST /api/tasks/presets/{id}/apply (apply_preset) -- complex nested body


def _list(args, client):
    params = {}
    if args.agent:
        params["agent"] = args.agent
    return client.get("/api/tasks", params=params or None)


def _get(args, client):
    return client.get(f"/api/tasks/{args.id}")


def _create(args, client):
    body = {
        "name": args.name,
        "schedule": args.schedule,
        "command": args.command,
        "agent_name": args.agent_name,
        "description": args.description,
    }
    return client.post("/api/tasks", body=body)


def _update(args, client):
    body = {}
    if args.name is not None:
        body["name"] = args.name
    if args.schedule is not None:
        body["schedule"] = args.schedule
    if args.command is not None:
        body["command"] = args.command
    if args.description is not None:
        body["description"] = args.description
    if args.enabled is not None:
        v = args.enabled.strip().lower()
        if v in ("true", "1", "yes"):
            body["enabled"] = True
        elif v in ("false", "0", "no"):
            body["enabled"] = False
        else:
            raise SystemExit(f"--enabled expects true or false, got: {args.enabled}")
    return client.request("PUT", f"/api/tasks/{args.id}", body=body)


def _delete(args, client):
    return client.delete(f"/api/tasks/{args.id}")


def _toggle(args, client):
    return client.post(f"/api/tasks/{args.id}/toggle")
