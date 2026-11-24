#!/usr/bin/env python3
"""The foundation's fake controller extended with the chat routes and /ws/chat.

Shapes come from tinyagentos/routes/chat.py: channels, messages (ascending, `before`),
unread, mark-read, typing, POST /api/chat/messages (broadcast to joined sockets), agents,
and the websocket (cookie auth, join/leave, frames fan out to joined sockets only).
Control: POST /__fake/chat {"seed": true} | {"frame": {...}, "channel_id": "x"} | {"drop": true}
         | {"unread": {"id": n}} | {"fail_send": "text"} ; GET /__fake/chat -> posts and joins.
"""
import base64
import hashlib
import json
import os
import struct
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "testing"))
import fake_controller as fc  # noqa: E402

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
lk = threading.RLock()
chat = {}
socks = []  # {"wfile":..., "joined": set(), "alive": True}
T0 = time.time() - 3 * 3600


def seed():
    t = time.time()
    long_text = "\n".join("line %d of a long status report that goes on" % i for i in range(40))
    chat.clear()
    chat.update(
        channels=[
            dict(id="ch-general", name="general", type="topic", description="Everything", topic="Release week",
                 members=["user", "alpha", "beta"], created_at=T0, last_message_at=t - 60,
                 last_preview="Shipping tonight", last_preview_author="alpha", settings={}),
            dict(id="ch-build", name="build-room", type="group", members=["user", "alpha"], created_at=T0,
                 last_message_at=t - 4000, last_preview="green", last_preview_author="alpha", settings={}),
            dict(id="ch-alpha", name="alpha", type="dm", members=["user", "alpha"], created_at=T0,
                 last_message_at=t - 30, last_preview="Here is the plan", last_preview_author="alpha",
                 settings={"taostalk_agent": "alpha"}),
            dict(id="ch-beta", name="beta", type="dm", members=["user", "beta"], created_at=T0,
                 last_message_at=t - 9000, last_preview="Paused until Monday", last_preview_author="beta", settings={}),
            dict(id="ch-jo", name="jo", type="dm", members=["user"], created_at=T0,
                 last_message_at=t - 20000, last_preview="see you", last_preview_author="jo", settings={}),
            dict(id="ch-a2a", name="a2a-alpha-beta", type="dm", members=["alpha", "beta"], created_at=T0,
                 last_message_at=t - 500, last_preview="handoff", last_preview_author="alpha", settings={"kind": "a2a"}),
        ],
        unread={"ch-general": 2, "ch-alpha": 1, "ch-build": 0},
        agents=[
            dict(name="alpha", display_name="Alpha", emoji="", framework="openclaw", status="running"),
            dict(name="beta", display_name="Beta", framework="hermes", status="paused"),
        ],
        archived=[],
        messages={},
        posts=[],
        fail_send=None,
        seq=100,
    )

    def m(i, ch, author, atype, content, dt, **kw):
        d = dict(id="m%03d" % i, channel_id=ch, author_id=author, author_type=atype, content=content,
                 content_type="text", created_at=t - dt, state="complete")
        d.update(kw)
        return d

    chat["messages"]["ch-general"] = [
        m(1, "ch-general", "usr_1", "user", "Morning. Where are we on the **release**?", 7300),
        m(2, "ch-general", "alpha", "agent", "Tests are green. Remaining:\n\n- changelog fragments\n- the `uv.lock` bump\n- tag the beta", 7250),
        m(3, "ch-general", "alpha", "agent", "I will take the tag once @beta confirms. See [the board](https://example.org/board).", 7240),
        m(4, "ch-general", "beta", "agent", "Confirmed.\n\n```bash\ngit tag -a v1.0.0b9 -m beta\ngit push origin v1.0.0b9\n```", 120),
        m(5, "ch-general", "usr_1", "user", "Great, go ahead.", 70),
        m(6, "ch-general", "alpha", "agent", "Shipping tonight", 60),
    ]
    chat["messages"]["ch-alpha"] = [
        m(10, "ch-alpha", "usr_1", "user", "Can you draft the plan for the Messages app?", 300),
        m(11, "ch-alpha", "alpha", "agent", "", 295, content_blocks=[
            dict(kind="thinking", text="Need the parity table first, then the client."),
            dict(kind="tool_call", call_id="c1", name="read_file", input_preview="PARITY-messages.md", status="done", result_preview="63 lines"),
            dict(kind="text", text="Here is the plan\n\n1. Parity table\n2. Client and fixtures\n3. The app"),
        ]),
        m(12, "ch-alpha", "alpha", "agent", long_text, 40),
        m(13, "ch-alpha", "alpha", "agent", "| step | owner |\n|---|---|\n| client | alpha |\n| app | beta |", 30),
    ]
    chat["messages"]["ch-build"] = [m(20, "ch-build", "alpha", "agent", "green", 4000)]
    chat["messages"]["ch-beta"] = [m(30, "ch-beta", "beta", "agent", "Paused until Monday", 9000)]
    chat["messages"]["ch-jo"] = [m(40, "ch-jo", "jo", "user", "see you", 20000)]
    chat["messages"]["ch-a2a"] = [m(50, "ch-a2a", "alpha", "agent", "handoff", 500)]
    # a long history for pagination
    chat["messages"]["ch-build"] = [
        m(200 + i, "ch-build", "alpha" if i % 2 else "usr_1", "agent" if i % 2 else "user", "history %d" % i, 9000 + (130 - i) * 60)
        for i in range(130)
    ] + chat["messages"]["ch-build"]


def frame_bytes(text, opcode=1):
    b = text.encode()
    if len(b) < 126:
        h = struct.pack("!BB", 0x80 | opcode, len(b))
    elif len(b) < 65536:
        h = struct.pack("!BBH", 0x80 | opcode, 126, len(b))
    else:
        h = struct.pack("!BBQ", 0x80 | opcode, 127, len(b))
    return h + b


def broadcast(channel_id, frame):
    data = frame_bytes(json.dumps(frame))
    with lk:
        for s in list(socks):
            if channel_id is None or channel_id in s["joined"]:
                try:
                    s["wfile"].write(data)
                    s["wfile"].flush()
                except Exception:
                    s["alive"] = False


def read_exact(rfile, n):
    buf = b""
    while len(buf) < n:
        c = rfile.read(n - len(buf))
        if not c:
            raise EOFError
        buf += c
    return buf


def read_frame(rfile):
    b0, b1 = read_exact(rfile, 2)
    op = b0 & 0x0F
    ln = b1 & 0x7F
    if ln == 126:
        ln = struct.unpack("!H", read_exact(rfile, 2))[0]
    elif ln == 127:
        ln = struct.unpack("!Q", read_exact(rfile, 8))[0]
    mask = read_exact(rfile, 4) if b1 & 0x80 else b"\0\0\0\0"
    data = bytearray(read_exact(rfile, ln))
    for i in range(len(data)):
        data[i] ^= mask[i % 4]
    return op, bytes(data)


class H(fc.H):
    protocol_version = "HTTP/1.1"

    def r_GET(self, path, q):
        if path == "/ws/chat":
            return self.ws()
        if path == "/api/chat/channels":
            return self.send_json({"channels": chat["channels"]})
        if path == "/api/chat/unread":
            return self.send_json({"unread": {k: v for k, v in chat["unread"].items() if v}})
        if path.startswith("/api/chat/channels/") and path.endswith("/messages"):
            cid = path.split("/")[4]
            msgs = chat["messages"].get(cid)
            if msgs is None:
                return self.send_json({"error": "Channel not found"}, 404)
            limit = int(q.get("limit", "50"))
            if "before" in q:
                msgs = [x for x in msgs if x["created_at"] < float(q["before"])]
            return self.send_json({"messages": msgs[-limit:]})
        if path == "/api/agents":
            return self.send_json(chat["agents"])
        if path == "/api/agents/archived":
            return self.send_json(chat["archived"])
        return fc.H.r_GET(self, path, q)

    def r_POST(self, path, q):
        if path == "/api/chat/messages":
            body = self.body()
            if chat["fail_send"] and body.get("content") == chat["fail_send"]:
                return self.send_json({"error": "address a member first"}, 400)
            if body.get("content", "").startswith("/help"):
                with lk:
                    chat["seq"] += 1
                    sysm = dict(id="s%d" % chat["seq"], channel_id=body["channel_id"], author_id="system",
                                author_type="system", content="Commands: /help", content_type="text",
                                created_at=time.time(), state="complete")
                    chat["messages"].setdefault(body["channel_id"], []).append(sysm)
                broadcast(body["channel_id"], dict(type="message", **sysm))
                return self.send_json({"ok": True, "handled": "help", "system_message": sysm})
            with lk:
                chat["seq"] += 1
                msg = dict(id="n%d" % chat["seq"], channel_id=body["channel_id"], author_id="usr_1", author_type="user",
                           content=body.get("content", ""), content_type="text", created_at=time.time(), state="complete")
                chat["messages"].setdefault(body["channel_id"], []).append(msg)
                chat["posts"].append(dict(body))
            broadcast(body["channel_id"], dict(type="message", **msg))
            return self.send_json(msg)
        if path.startswith("/api/chat/channels/") and path.endswith("/mark-read"):
            cid = path.split("/")[4]
            with lk:
                chat["unread"].pop(cid, None)
                chat["posts"].append(dict(path=path))
            return self.send_json({"status": "marked"})
        if path.startswith("/api/chat/channels/") and path.endswith("/typing"):
            with lk:
                chat["posts"].append(dict(path=path, body=self.body()))
            return self.send_json({"ok": True})
        return fc.H.r_POST(self, path, q)

    def ws(self):
        ck = self.cookies().get("taos_session")
        if not ck:
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        key = self.headers.get("Sec-WebSocket-Key", "")
        acc = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", acc)
        self.end_headers()
        self.wfile.flush()
        self.close_connection = True
        s = dict(wfile=self.wfile, joined=set(), alive=True)
        with lk:
            socks.append(s)
        try:
            while s["alive"]:
                op, data = read_frame(self.rfile)
                if op == 8:
                    break
                if op == 9:
                    self.wfile.write(frame_bytes(data.decode("latin1"), 10))
                    self.wfile.flush()
                    continue
                if op == 1:
                    d = json.loads(data)
                    with lk:
                        if d.get("type") == "join":
                            s["joined"].add(d["channel_id"])
                            chat["posts"].append(dict(ws="join", channel_id=d["channel_id"]))
                        elif d.get("type") == "leave":
                            s["joined"].discard(d["channel_id"])
                            chat["posts"].append(dict(ws="leave", channel_id=d["channel_id"]))
        except Exception:
            pass
        finally:
            with lk:
                if s in socks:
                    socks.remove(s)

    def control(self, verb, path):
        if path == "/__fake/chat":
            if verb == "GET":
                with lk:
                    return self.send_json(dict(posts=chat["posts"], joined=[sorted(s["joined"]) for s in socks]))
            b = self.body()
            if b.get("seed"):
                with lk:
                    seed()
            if "frame" in b:
                broadcast(b.get("channel_id"), b["frame"])
            if b.get("drop"):
                with lk:
                    for s in list(socks):
                        s["alive"] = False
                        try:
                            s["wfile"].write(frame_bytes("", 8))
                            s["wfile"].flush()
                        except Exception:
                            pass
                    socks.clear()
            if "unread" in b:
                with lk:
                    chat["unread"] = b["unread"]
            if "fail_send" in b:
                chat["fail_send"] = b["fail_send"]
            if "channels" in b:
                with lk:
                    chat["channels"] = b["channels"]
            if "agents" in b:
                with lk:
                    chat["agents"] = b["agents"]
            if b.get("empty"):
                with lk:
                    chat["channels"] = []
                    chat["unread"] = {}
            return self.send_json({"ok": True})
        return fc.H.control(self, verb, path)


def main():
    import argparse
    from http.server import ThreadingHTTPServer
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    a = ap.parse_args()
    fc.reset()
    seed()
    ThreadingHTTPServer.daemon_threads = True
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), H)
    print("LISTENING %d" % srv.server_address[1], flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
