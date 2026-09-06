"""taosctl settings -- inspect and manage server settings.

Covers storage, webhooks, backup schedule, container runtime, update status,
platform settings, and config. Skips endpoints that need file upload, streaming,
or complex nested bodies (test-backend, backup, restore, webhooks/test,
notification-prefs/{event_type}, update, update-check-now, rebuild-frontend,
update-channel).
"""
from __future__ import annotations

NOUN = "settings"


def register(subparsers) -> None:
    p = subparsers.add_parser(NOUN, help="Inspect and manage server settings")
    verbs = p.add_subparsers(dest="verb", required=True, metavar="<verb>")

    sp = verbs.add_parser("storage", help="Show storage usage")
    sp.set_defaults(func=_storage)

    lp = verbs.add_parser("llm-proxy", help="Show LLM proxy status")
    lp.set_defaults(func=_llm_proxy)

    bp = verbs.add_parser("backup-schedule", help="Show backup schedule")
    bp.set_defaults(func=_backup_schedule)

    sbp = verbs.add_parser("set-backup-schedule", help="Set backup schedule frequency")
    sbp.add_argument("frequency", choices=["off", "daily", "weekly"], help="Backup frequency")
    sbp.set_defaults(func=_set_backup_schedule)

    wp = verbs.add_parser("webhooks", help="List configured webhooks")
    wp.set_defaults(func=_webhooks)

    awp = verbs.add_parser("add-webhook", help="Add a webhook endpoint")
    awp.add_argument("--url", required=True, help="Webhook URL")
    awp.add_argument("--type", default="generic", help="Webhook type")
    awp.add_argument("--bot-token", default="", help="Bot token (Telegram etc.)")
    awp.add_argument("--chat-id", default="", help="Chat ID (Telegram etc.)")
    awp.set_defaults(func=_add_webhook)

    rwp = verbs.add_parser("remove-webhook", help="Remove a webhook by index")
    rwp.add_argument("index", type=int, help="Webhook index")
    rwp.set_defaults(func=_remove_webhook)

    np = verbs.add_parser("notification-prefs", help="Show notification preferences")
    np.set_defaults(func=_notification_prefs)

    cp = verbs.add_parser("container-runtime", help="Show container runtime status")
    cp.set_defaults(func=_container_runtime)

    scp = verbs.add_parser("set-container-runtime", help="Set container runtime preference")
    scp.add_argument("runtime", choices=["auto", "apple", "lxc", "docker", "podman"], help="Runtime")
    scp.set_defaults(func=_set_container_runtime)

    brp = verbs.add_parser("branches", help="List remote branches and current tracking")
    brp.set_defaults(func=_branches)

    up = verbs.add_parser("update-check", help="Check for available updates")
    up.set_defaults(func=_update_check)

    usp = verbs.add_parser("update-status", help="Show current/pending update status")
    usp.set_defaults(func=_update_status)

    pp = verbs.add_parser("set-platform", help="Update platform settings")
    pp.add_argument("--poll-interval", type=int, required=True, help="Metrics poll interval (seconds)")
    pp.add_argument("--retention-days", type=int, required=True, help="Metrics retention (days)")
    pp.add_argument("--catalog-repo", default="", help="Catalog repo URL")
    pp.set_defaults(func=_set_platform)

    gc = verbs.add_parser("config", help="Get current config as YAML")
    gc.set_defaults(func=_config)

    sc = verbs.add_parser("set-config", help="Validate and save config from YAML")
    sc.add_argument("--yaml", required=True, help="Config YAML string")
    sc.set_defaults(func=_set_config)


def _storage(args, client):
    return client.get("/api/settings/storage")


def _llm_proxy(args, client):
    return client.get("/api/settings/llm-proxy")


def _backup_schedule(args, client):
    return client.get("/api/settings/backup-schedule")


def _set_backup_schedule(args, client):
    return client.put("/api/settings/backup-schedule", body={"frequency": args.frequency})


def _webhooks(args, client):
    return client.get("/api/settings/webhooks")


def _add_webhook(args, client):
    body = {"url": args.url, "type": args.type}
    if args.bot_token:
        body["bot_token"] = args.bot_token
    if args.chat_id:
        body["chat_id"] = args.chat_id
    return client.post("/api/settings/webhooks", body=body)


def _remove_webhook(args, client):
    return client.delete(f"/api/settings/webhooks/{args.index}")


def _notification_prefs(args, client):
    return client.get("/api/settings/notification-prefs")


def _container_runtime(args, client):
    return client.get("/api/settings/container-runtime")


def _set_container_runtime(args, client):
    return client.put("/api/settings/container-runtime", body={"runtime": args.runtime})


def _branches(args, client):
    return client.get("/api/settings/branches")


def _update_check(args, client):
    return client.get("/api/settings/update-check")


def _update_status(args, client):
    return client.get("/api/settings/update-status")


def _set_platform(args, client):
    body = {"poll_interval": args.poll_interval, "retention_days": args.retention_days}
    if args.catalog_repo:
        body["catalog_repo"] = args.catalog_repo
    return client.put("/api/settings/platform", body=body)


def _config(args, client):
    return client.get("/api/config")


def _set_config(args, client):
    return client.put("/api/config", body={"yaml": args.yaml})
