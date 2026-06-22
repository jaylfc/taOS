"""taosctl mail -- inspect and manage mail accounts.

Reference noun: mirrors the agents pattern (NOUN, register(), small handlers
that call the client and return data for the framework to render).
"""
from __future__ import annotations

import getpass
import os
from urllib.parse import quote

NOUN = "mail"


def register(subparsers) -> None:
    p = subparsers.add_parser(NOUN, help="Inspect and manage mail accounts")
    verbs = p.add_subparsers(dest="verb", required=True, metavar="<verb>")

    lp = verbs.add_parser("list", help="List mail accounts")
    lp.set_defaults(func=_list)

    cp = verbs.add_parser("create", help="Add a mail account")
    cp.add_argument("--email", required=True, help="Email address")
    cp.add_argument("--imap-host", required=True, help="IMAP server hostname")
    cp.add_argument("--imap-port", type=int, default=993, help="IMAP port")
    cp.add_argument("--smtp-host", required=True, help="SMTP server hostname")
    cp.add_argument("--smtp-port", type=int, default=587, help="SMTP port")
    cp.add_argument("--username", required=True, help="Account username")
    cp.add_argument("--password", default=None,
                    help="Account password. Prefer the TAOS_MAIL_PASSWORD env var or the "
                         "interactive prompt; a password on the command line is visible to "
                         "other processes.")
    cp.add_argument("--display-name", default="", help="Display name")
    cp.set_defaults(func=_create)

    dp = verbs.add_parser("delete", help="Delete a mail account")
    dp.add_argument("account_id", help="Account id")
    dp.set_defaults(func=_delete)

    fp = verbs.add_parser("folders", help="List IMAP folders for an account")
    fp.add_argument("account_id", help="Account id")
    fp.set_defaults(func=_folders)

    mp = verbs.add_parser("messages", help="List messages in an account folder")
    mp.add_argument("account_id", help="Account id")
    mp.add_argument("--folder", default="INBOX", help="Folder name")
    mp.add_argument("--limit", type=int, default=50, help="Max messages")
    mp.set_defaults(func=_messages)

    gp = verbs.add_parser("get", help="Get a single message by uid")
    gp.add_argument("account_id", help="Account id")
    gp.add_argument("uid", help="Message uid")
    gp.add_argument("--folder", default="INBOX", help="Folder name")
    gp.set_defaults(func=_get)

    sp = verbs.add_parser("send", help="Send an email")
    sp.add_argument("account_id", help="Account id")
    sp.add_argument("--to", required=True, help="Recipient address")
    sp.add_argument("--subject", default="", help="Subject line")
    sp.add_argument("--body", default="", help="Message body")
    sp.add_argument("--cc", default="", help="CC address")
    sp.set_defaults(func=_send)

    # TODO: agent send-as profile switcher (Phase 2, needs consent layer)


def _list(args, client):
    return client.get("/api/mail/accounts")


def _resolve_password(args):
    """Resolve the account password without requiring it on the command line
    (which would be visible in the process list). Precedence: explicit --password,
    then the TAOS_MAIL_PASSWORD env var, then an interactive prompt."""
    if args.password is not None:
        return args.password
    env = os.environ.get("TAOS_MAIL_PASSWORD")
    if env:
        return env
    return getpass.getpass("Account password: ")


def _create(args, client):
    return client.post("/api/mail/accounts", json={
        "display_name": args.display_name,
        "email_address": args.email,
        "imap_host": args.imap_host,
        "imap_port": args.imap_port,
        "smtp_host": args.smtp_host,
        "smtp_port": args.smtp_port,
        "username": args.username,
        "password": _resolve_password(args),
    })


def _delete(args, client):
    return client.delete(f"/api/mail/accounts/{quote(args.account_id, safe='')}")


def _folders(args, client):
    return client.get(f"/api/mail/accounts/{quote(args.account_id, safe='')}/folders")


def _messages(args, client):
    return client.get(
        f"/api/mail/accounts/{quote(args.account_id, safe='')}/messages",
        params={"folder": args.folder, "limit": args.limit},
    )


def _get(args, client):
    return client.get(
        f"/api/mail/accounts/{quote(args.account_id, safe='')}/messages/{quote(args.uid, safe='')}",
        params={"folder": args.folder},
    )


def _send(args, client):
    return client.post(
        f"/api/mail/accounts/{quote(args.account_id, safe='')}/send",
        json={"to": args.to, "subject": args.subject, "body": args.body, "cc": args.cc},
    )
