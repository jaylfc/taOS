#!/usr/bin/env python3
"""Portable taOS agent client for the simple cron-poll collaboration model.

An invited external agent that holds its own registry token (Bearer, scoped to
a project) uses this to participate in a taOS project: check the A2A bus, find a
claimable board card, claim it, and report the result. No shared config, no
session cookie -- the token IS the identity.

Config (a credential file, chmod 600), path in $TAOS_CRED or ~/.taos-agent.cred:

    TAOS_API=http://<host>:6969
    TAOS_BUS=http://<host>:7900          # optional; A2A over the same host
    TAOS_TOKEN=<your registry JWT>       # from the invite approval
    TAOS_CANONICAL=<your-canonical-id>   # e.g. myagent-20260718-013717
    TAOS_PROJECT=prj-xxxxxx              # the project you were granted

Usage (the loop your 30-min cron runs):

    taos_agent.py check       # print any A2A mentions + the next claimable card
    taos_agent.py claim  <id> # claim a card as yourself
    taos_agent.py comment <id> "<text>"
    taos_agent.py close  <id> "<reason>"
    taos_agent.py release <id>
    taos_agent.py say "<channel>" "<message>"   # post to an A2A channel

Exit code of `check` is 0 with a card id on stdout when work is available, so a
wrapper can gate on it. Prints nothing and exits 0 when idle.
"""
import json
import os
import sys
import urllib.request
import urllib.error


_KEYS = ("TAOS_API", "TAOS_BUS", "TAOS_TOKEN", "TAOS_CANONICAL", "TAOS_PROJECT")


def _load_cfg():
    """Load config from a chmod-600 credential file AND/OR the environment.

    The environment wins, so an agent can inject TAOS_TOKEN (and friends) from a
    secret store at runtime with no plaintext file on disk. The file is optional
    when the environment supplies everything.
    """
    cfg = {}
    path = os.environ.get("TAOS_CRED") or os.path.expanduser("~/.taos-agent.cred")
    if os.path.exists(path):
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    cfg[k.strip()] = v.strip()
    for k in _KEYS:  # environment overrides / supplements the file
        if os.environ.get(k):
            cfg[k] = os.environ[k]
    for k in ("TAOS_API", "TAOS_TOKEN", "TAOS_CANONICAL", "TAOS_PROJECT"):
        if not cfg.get(k):
            sys.exit(f"missing {k} (set it in {path} or the environment)")
    return cfg


CFG = _load_cfg()
API = CFG["TAOS_API"].rstrip("/")
BUS = (CFG.get("TAOS_BUS") or "").rstrip("/")
TOKEN = CFG["TAOS_TOKEN"]
ME = CFG["TAOS_CANONICAL"]
PROJECT = CFG["TAOS_PROJECT"]


def _req(base, path, payload=None, method=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + TOKEN)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=20) as r:
        body = r.read().decode()
    return json.loads(body) if body else {}


def _claimable(t):
    """A card this agent may pick up: open, unclaimed, labelled `claimable`, and
    in the open pool (@any / unassigned) or assigned to this agent."""
    if t.get("status") not in ("open", "todo", "ready", "backlog"):
        return False
    if t.get("claimer_id"):
        return False
    if "claimable" not in [str(x).lower() for x in (t.get("labels") or [])]:
        return False
    asg = (t.get("assignee_id") or "").strip().lower()
    return asg in ("", "@any", "@all", "unassigned", "none") or asg == ME.lower()


def cmd_check():
    # A2A mentions (best effort; skip silently if the bus is not reachable).
    if BUS:
        try:
            chans = _req(BUS, "/a2a/channels").get("channels", [])
            for ch in chans:
                name = ch.get("channel")
                msgs = _req(BUS, f"/a2a/messages?thread={name}&limit=5")
                for m in (msgs if isinstance(msgs, list) else msgs.get("messages", [])):
                    if ME.lower() in (m.get("body") or "").lower():
                        print(f"[a2a:{name}] {m.get('from')}: {m.get('body', '')[:200]}")
        except Exception:
            pass
    # Next claimable board card (highest priority first).
    tasks = _req(API, f"/api/projects/{PROJECT}/tasks?status=open").get("items", [])
    cands = sorted(
        (t for t in tasks if _claimable(t)),
        key=lambda t: t.get("priority") or 0,
        reverse=True,
    )
    if not cands:
        return
    top = cands[0]
    print(f"CARD {top['id']} | {top.get('title', '')}")
    if top.get("body"):
        print(top["body"])


def cmd_claim(tid):
    print(_req(API, f"/api/projects/{PROJECT}/tasks/{tid}/claim", {"claimer_id": ME}))


def cmd_release(tid):
    print(_req(API, f"/api/projects/{PROJECT}/tasks/{tid}/release", {"releaser_id": ME}))


def cmd_comment(tid, text):
    print(_req(API, f"/api/projects/{PROJECT}/tasks/{tid}/comments", {"author_id": ME, "body": text}))


def cmd_close(tid, reason=""):
    print(_req(API, f"/api/projects/{PROJECT}/tasks/{tid}/close", {"closed_by": ME, "reason": reason}))


def cmd_say(channel, message):
    if not BUS:
        sys.exit("TAOS_BUS not set")
    print(_req(BUS, "/a2a/send", {"from": ME, "thread": channel, "body": message}))


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd, args = sys.argv[1], sys.argv[2:]
    try:
        {
            "check": lambda: cmd_check(),
            "claim": lambda: cmd_claim(args[0]),
            "release": lambda: cmd_release(args[0]),
            "comment": lambda: cmd_comment(args[0], args[1]),
            "close": lambda: cmd_close(args[0], args[1] if len(args) > 1 else ""),
            "say": lambda: cmd_say(args[0], args[1]),
        }[cmd]()
    except KeyError:
        sys.exit(f"unknown command {cmd!r}")
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {e.read().decode()[:200]}")


if __name__ == "__main__":
    main()
