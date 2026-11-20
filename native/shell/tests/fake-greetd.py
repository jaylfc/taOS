#!/usr/bin/env python3
"""A tiny scripted greetd for the greeter's non-demo end to end test.

    fake-greetd.py <socket-path> <log-file>

Speaks the greetd IPC (4 byte native-endian length + JSON). The password is
"demo". Every request type it receives is appended to <log-file>; the
start_session command is logged as "start_session <cmd words>".
"""
import json
import os
import socket
import struct
import sys

path, log = sys.argv[1], sys.argv[2]
if os.path.exists(path):
    os.unlink(path)
srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
srv.bind(path)
srv.listen(1)


def recv_exact(c, n):
    buf = b""
    while len(buf) < n:
        chunk = c.recv(n - len(buf))
        if not chunk:
            raise EOFError
        buf += chunk
    return buf


def send(c, obj):
    data = json.dumps(obj).encode()
    c.sendall(struct.pack("=I", len(data)) + data)


def note(line):
    with open(log, "a") as f:
        f.write(line + "\n")


conn, _ = srv.accept()
authed = False
try:
    while True:
        (n,) = struct.unpack("=I", recv_exact(conn, 4))
        req = json.loads(recv_exact(conn, n))
        t = req.get("type")
        if t == "create_session":
            note("create_session")
            authed = False
            send(conn, {"type": "auth_message", "auth_message_type": "secret", "auth_message": "Password: "})
        elif t == "post_auth_message_response":
            note("post_auth_message_response")
            if req.get("response") == "demo":
                authed = True
                send(conn, {"type": "success"})
            else:
                send(conn, {"type": "error", "error_type": "auth_error", "description": "AUTH_ERR"})
        elif t == "cancel_session":
            note("cancel_session")
            authed = False
            send(conn, {"type": "success"})
        elif t == "start_session":
            note("start_session " + " ".join(req.get("cmd", [])))
            send(conn, {"type": "success"} if authed else {"type": "error", "error_type": "error", "description": "not authenticated"})
            break
except EOFError:
    pass
