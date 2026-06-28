"""taosctl notifications -- list, read, and manage notification preferences."""
from __future__ import annotations

from urllib.parse import quote

NOUN = "notifications"


def register(subparsers) -> None:
    p = subparsers.add_parser(NOUN, help="List, read, and manage notifications")
    verbs = p.add_subparsers(dest="verb", required=True, metavar="<verb>")

    lp = verbs.add_parser("list", help="List notifications")
    lp.add_argument("--unread-only", action="store_true", default=False,
                    help="Show only unread notifications")
    lp.set_defaults(func=_list)

    rp = verbs.add_parser("read", help="Mark a notification as read")
    rp.add_argument("notif_id", help="Notification id")
    rp.set_defaults(func=_read)

    rap = verbs.add_parser("read-all", help="Mark all notifications as read")
    rap.set_defaults(func=_read_all)

    marp = verbs.add_parser("mark-all-read", help="Mark all as read (counted)")
    marp.set_defaults(func=_mark_all_read)

    cp = verbs.add_parser("count", help="Get the unread notification count")
    cp.set_defaults(func=_count)

    pp = verbs.add_parser("prefs", help="Get notification preferences")
    pp.set_defaults(func=_prefs)

    sp = verbs.add_parser("set-pref", help="Set a notification preference")
    sp.add_argument("event_type", help="Event type to configure")
    sp.add_argument("--muted", action="store_true", default=False,
                    help="Mute this event type")
    sp.set_defaults(func=_set_pref)


def _list(args, client):
    params = {"unread_only": args.unread_only} if args.unread_only else None
    return client.get("/api/notifications", params=params)


def _read(args, client):
    return client.post(f"/api/notifications/{quote(str(args.notif_id), safe='')}/read")


def _read_all(args, client):
    return client.post("/api/notifications/read-all")


def _mark_all_read(args, client):
    return client.post("/api/notifications/mark-all-read")


def _count(args, client):
    return client.get("/api/notifications/count")


def _prefs(args, client):
    return client.get("/api/notifications/prefs")


def _set_pref(args, client):
    return client.request(
        "PUT",
        f"/api/notifications/prefs/{quote(args.event_type, safe='')}",
        body={"muted": args.muted},
    )
