#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fake of the controller's desktop sign-in socket (tinyagentos/desktop_sso.py) for the native tests.

  fake_sso.py <socket path> <fake controller url> [unlinked]

Answers {"op":"session"} with a real session of the fake controller (it logs in over HTTP), or
{"error":"unlinked"}; {"op":"release"} with {"ok":true} and logs out that session. Prints READY."""
import asyncio, json, os, sys, urllib.request

sock, base = sys.argv[1], sys.argv[2]
unlinked = len(sys.argv) > 3 and sys.argv[3] == "unlinked"
UA = "taOS-desktop-session/1"
issued = []


def login():
    sess = csrf = None
    req = urllib.request.Request(base + "/auth/login", data=json.dumps({"username": "alex", "password": "hunter2-secret-pw"}).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req) as r:
        for h in r.headers.get_all("Set-Cookie"):
            if h.startswith("taos_session="):
                sess = h.split(";")[0].split("=", 1)[1]
            if h.startswith("csrf_token="):
                csrf = h.split(";")[0].split("=", 1)[1]
    return sess, csrf


async def handle(reader, writer):
    req = json.loads((await reader.readline()) or b"{}")
    if req.get("op") == "session":
        out = {"error": "unlinked"}
        if not unlinked:
            sess, csrf = login()  # the fake controller hands out its CSRF token at login; the real one sets it on the first GET
            out = {"session": sess, "csrf": csrf, "user_agent": UA, "expires_in": 3600, "username": "alex"}
        if "session" in out:
            issued.append(out["session"])
    else:
        out = {"ok": True}
    writer.write(json.dumps(out).encode() + b"\n")
    await writer.drain()
    writer.close()


async def main():
    if os.path.exists(sock):
        os.unlink(sock)
    await asyncio.start_unix_server(handle, path=sock)
    print("READY", flush=True)
    await asyncio.Event().wait()

asyncio.run(main())
