"""taosctl chat -- read and post chat channels and messages.

Wraps the HTTP routes in tinyagentos/routes/chat.py and chat_admin.py.

Skipped on purpose:
- /ws/chat (websocket stream) and /api/chat/messages/{id}/delta|state (agent
  streaming internals)
- /api/chat/channels/{id}/typing|thinking|wants_reply (live presence signals)
- /api/chat/channels/{id}/read-cursor/rewind, PUT /api/chat/channels/{id} and
  PUT /api/chat/channels/{id}/project, DELETE .../members/{member_id}
  (covered by `mark-read`, `update-channel` and `members remove`)
- /api/chat/v2/* (read-only bus mirror of the same data)
- chat_files.py upload routes (multipart)
"""
from __future__ import annotations

from urllib.parse import quote

NOUN = "chat"


def _q(value: str) -> str:
    return quote(value, safe="")


def register(subparsers) -> None:
    p = subparsers.add_parser(NOUN, help="Read and post chat channels and messages")
    verbs = p.add_subparsers(dest="verb", required=True, metavar="<verb>")

    # ---- channels -----------------------------------------------------------
    cp = verbs.add_parser("channels", help="List chat channels")
    cp.add_argument("--member", default=None, help="Only channels with this member")
    cp.add_argument("--project", dest="project_id", default=None,
                    help="Only channels bound to this project id")
    arch = cp.add_mutually_exclusive_group()
    arch.add_argument("--archived", dest="archived", action="store_const", const=True,
                      default=None, help="Only archived channels")
    arch.add_argument("--active", dest="archived", action="store_const", const=False,
                      help="Only non-archived channels")
    cp.set_defaults(func=_channels)

    gp = verbs.add_parser("channel", help="Get one channel by id")
    gp.add_argument("channel_id", help="Channel id")
    gp.set_defaults(func=_channel)

    ccp = verbs.add_parser("create-channel", help="Create a channel (admin)")
    ccp.add_argument("name", help="Channel name")
    ccp.add_argument("--type", default=None, help="Channel type (server default: topic)")
    ccp.add_argument("--description", default=None, help="Channel description")
    ccp.add_argument("--topic", default=None, help="Channel topic")
    ccp.add_argument("--project", dest="project_id", default=None, help="Project id")
    ccp.add_argument("--member", dest="members", action="append", default=None,
                     help="Member slug (repeatable)")
    ccp.set_defaults(func=_create_channel)

    ucp = verbs.add_parser("update-channel", help="Update channel settings (admin)")
    ucp.add_argument("channel_id", help="Channel id")
    ucp.add_argument("--name", default=None, help="New channel name")
    ucp.add_argument("--topic", default=None, help="New channel topic")
    ucp.add_argument("--response-mode", dest="response_mode", default=None,
                     help="Agent response mode")
    ucp.add_argument("--max-hops", dest="max_hops", type=int, default=None,
                     help="Max agent-to-agent hops")
    ucp.add_argument("--cooldown-seconds", dest="cooldown_seconds", type=int, default=None,
                     help="Agent reply cooldown in seconds")
    ucp.set_defaults(func=_update_channel)

    dcp = verbs.add_parser("delete-channel", help="Delete a channel (admin)")
    dcp.add_argument("channel_id", help="Channel id")
    dcp.set_defaults(func=_delete_channel)

    mbp = verbs.add_parser("members", help="Add or remove a channel member (admin)")
    mbp.add_argument("channel_id", help="Channel id")
    mbp.add_argument("action", choices=["add", "remove"], help="add or remove")
    mbp.add_argument("slug", help="Member slug, e.g. an agent name or 'user'")
    mbp.set_defaults(func=_members)

    mup = verbs.add_parser("mute", help="Add or remove an agent from a channel's muted list (admin)")
    mup.add_argument("channel_id", help="Channel id")
    mup.add_argument("action", choices=["add", "remove"], help="add or remove")
    mup.add_argument("slug", help="Agent slug")
    mup.set_defaults(func=_mute)

    # ---- reading ------------------------------------------------------------
    mp = verbs.add_parser("messages", help="List messages in a channel")
    mp.add_argument("channel_id", help="Channel id")
    mp.add_argument("--limit", type=int, default=None, help="Max messages (server default 50)")
    mp.add_argument("--before", type=float, default=None,
                    help="Only messages before this timestamp")
    mp.set_defaults(func=_messages)

    gmp = verbs.add_parser("message", help="Get one message by id")
    gmp.add_argument("message_id", help="Message id")
    gmp.set_defaults(func=_message)

    tsp = verbs.add_parser("threads", help="List threads in a channel")
    tsp.add_argument("channel_id", help="Channel id")
    tsp.set_defaults(func=_threads)

    tp = verbs.add_parser("thread", help="List replies in one thread")
    tp.add_argument("channel_id", help="Channel id")
    tp.add_argument("parent_id", help="Parent message id")
    tp.add_argument("--limit", type=int, default=None, help="Max replies (server default 20)")
    tp.set_defaults(func=_thread)

    pp = verbs.add_parser("pins", help="List pinned messages in a channel")
    pp.add_argument("channel_id", help="Channel id")
    pp.set_defaults(func=_pins)

    sp = verbs.add_parser("search", help="Search messages")
    sp.add_argument("query", help="Search text (2+ chars)")
    sp.add_argument("--channel", dest="channel_id", default=None, help="Limit to one channel")
    sp.add_argument("--limit", type=int, default=None, help="Max results (server default 20)")
    sp.set_defaults(func=_search)

    up = verbs.add_parser("unread", help="Unread counts per channel")
    up.set_defaults(func=_unread)

    mrp = verbs.add_parser("mark-read", help="Mark a channel read")
    mrp.add_argument("channel_id", help="Channel id")
    mrp.add_argument("--message-id", dest="message_id", default=None,
                     help="Mark read up to this message")
    mrp.set_defaults(func=_mark_read)

    # ---- writing ------------------------------------------------------------
    snp = verbs.add_parser("send", help="Send a message to a channel")
    snp.add_argument("channel_id", help="Channel id")
    snp.add_argument("content", help="Message text")
    snp.add_argument("--author", dest="author_id", default="user",
                     help="Author id (default: user; ignored for device bearers)")
    snp.add_argument("--author-type", dest="author_type", default=None,
                     help="Author type, e.g. user or agent")
    snp.add_argument("--thread", dest="thread_id", default=None,
                     help="Reply inside this thread (parent message id)")
    snp.set_defaults(func=_send)

    ep = verbs.add_parser("edit", help="Edit a message's content")
    ep.add_argument("message_id", help="Message id")
    ep.add_argument("content", help="New message text")
    ep.set_defaults(func=_edit)

    dp = verbs.add_parser("delete", help="Delete a message")
    dp.add_argument("message_id", help="Message id")
    dp.set_defaults(func=_delete)

    pinp = verbs.add_parser("pin", help="Pin a message")
    pinp.add_argument("message_id", help="Message id")
    pinp.set_defaults(func=_pin)

    unpinp = verbs.add_parser("unpin", help="Unpin a message")
    unpinp.add_argument("message_id", help="Message id")
    unpinp.set_defaults(func=_unpin)

    rp = verbs.add_parser("react", help="Add a reaction to a message")
    rp.add_argument("message_id", help="Message id")
    rp.add_argument("emoji", help="Emoji")
    rp.add_argument("--author", dest="author_id", default="user",
                    help="Reacting author id (default: user)")
    rp.add_argument("--author-type", dest="author_type", default=None,
                    help="Reacting author type (server default: user)")
    rp.set_defaults(func=_react)

    urp = verbs.add_parser("unreact", help="Remove a reaction from a message")
    urp.add_argument("message_id", help="Message id")
    urp.add_argument("emoji", help="Emoji")
    urp.add_argument("--author", dest="author_id", default="user",
                     help="Reacting author id (default: user)")
    urp.set_defaults(func=_unreact)


def _drop_none(body: dict) -> dict:
    return {k: v for k, v in body.items() if v is not None}


# ---- channels ----------------------------------------------------------------

def _channels(args, client):
    archived = None if args.archived is None else ("true" if args.archived else "false")
    return client.get("/api/chat/channels", params={
        "member": args.member, "archived": archived, "project_id": args.project_id,
    })


def _channel(args, client):
    return client.get(f"/api/chat/channels/{_q(args.channel_id)}")


def _create_channel(args, client):
    body = _drop_none({
        "name": args.name, "type": args.type, "description": args.description,
        "topic": args.topic, "project_id": args.project_id, "members": args.members,
    })
    return client.post("/api/chat/channels", body=body)


def _update_channel(args, client):
    body = _drop_none({
        "name": args.name, "topic": args.topic, "response_mode": args.response_mode,
        "max_hops": args.max_hops, "cooldown_seconds": args.cooldown_seconds,
    })
    return client.patch(f"/api/chat/channels/{_q(args.channel_id)}", body=body)


def _delete_channel(args, client):
    return client.delete(f"/api/chat/channels/{_q(args.channel_id)}")


def _members(args, client):
    return client.post(f"/api/chat/channels/{_q(args.channel_id)}/members",
                       body={"action": args.action, "slug": args.slug})


def _mute(args, client):
    return client.post(f"/api/chat/channels/{_q(args.channel_id)}/muted",
                       body={"action": args.action, "slug": args.slug})


# ---- reading -----------------------------------------------------------------

def _messages(args, client):
    return client.get(f"/api/chat/channels/{_q(args.channel_id)}/messages",
                      params={"limit": args.limit, "before": args.before})


def _message(args, client):
    return client.get(f"/api/chat/messages/{_q(args.message_id)}")


def _threads(args, client):
    return client.get(f"/api/chat/channels/{_q(args.channel_id)}/threads")


def _thread(args, client):
    return client.get(
        f"/api/chat/channels/{_q(args.channel_id)}/threads/{_q(args.parent_id)}/messages",
        params={"limit": args.limit},
    )


def _pins(args, client):
    return client.get(f"/api/chat/channels/{_q(args.channel_id)}/pins")


def _search(args, client):
    return client.get("/api/chat/search", params={
        "q": args.query, "channel_id": args.channel_id, "limit": args.limit,
    })


def _unread(args, client):
    return client.get("/api/chat/unread")


def _mark_read(args, client):
    body = _drop_none({"message_id": args.message_id})
    return client.post(f"/api/chat/channels/{_q(args.channel_id)}/mark-read", body=body)


# ---- writing -----------------------------------------------------------------

def _send(args, client):
    body = _drop_none({
        "channel_id": args.channel_id, "content": args.content,
        "author_id": args.author_id, "author_type": args.author_type,
        "thread_id": args.thread_id,
    })
    return client.post("/api/chat/messages", body=body)


def _edit(args, client):
    return client.patch(f"/api/chat/messages/{_q(args.message_id)}",
                        body={"content": args.content})


def _delete(args, client):
    return client.delete(f"/api/chat/messages/{_q(args.message_id)}")


def _pin(args, client):
    return client.post(f"/api/chat/messages/{_q(args.message_id)}/pin")


def _unpin(args, client):
    return client.delete(f"/api/chat/messages/{_q(args.message_id)}/pin")


def _react(args, client):
    body = _drop_none({"emoji": args.emoji, "author_id": args.author_id,
                       "author_type": args.author_type})
    return client.post(f"/api/chat/messages/{_q(args.message_id)}/reactions", body=body)


def _unreact(args, client):
    return client.delete(
        f"/api/chat/messages/{_q(args.message_id)}/reactions/{_q(args.emoji)}",
        params={"author_id": args.author_id},
    )
