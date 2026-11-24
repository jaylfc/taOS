#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Helper for e2e-notifyd.sh, against native/testing/fake_controller.py.

  seed <port> <runtime dir>   sign in to the fake and write <runtime dir>/taos/session (0600), the file the native apps share
  stats <port>                print the fake's /__fake/stats as JSON
  config <port> <json>        POST /__fake/config
  rows <port> <title>         print how many OS rows carry exactly this title
"""
import json
import os
import sys
import urllib.request

UA = "taOS-native/e2e"


def req(port, path, body=None, headers=None):
    r = urllib.request.Request("http://127.0.0.1:%s%s" % (port, path),
                               data=None if body is None else json.dumps(body).encode(),
                               headers=headers or {}, method="POST" if body is not None else "GET")
    return urllib.request.urlopen(r, timeout=10)


def main():
    cmd, port = sys.argv[1], sys.argv[2]
    if cmd == "seed":
        resp = req(port, "/auth/login", {"username": "alex", "password": "hunter2-secret-pw", "remember": True},
                   {"User-Agent": UA, "Content-Type": "application/json"})
        cookies = {}
        for h in resp.headers.get_all("Set-Cookie"):
            k, _, v = h.split(";")[0].partition("=")
            cookies[k] = v
        d = os.path.join(sys.argv[3], "taos")
        os.makedirs(d, mode=0o700, exist_ok=True)
        path = os.path.join(d, "session")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"v": 1, "base_url": "http://127.0.0.1:%s" % port, "user_agent": UA,
                       "session": cookies["taos_session"], "csrf": cookies.get("csrf_token")}, f)
    elif cmd == "stats":
        print(req(port, "/__fake/stats").read().decode())
    elif cmd == "config":
        req(port, "/__fake/config", json.loads(sys.argv[3])).read()
    elif cmd == "rows":
        st = json.load(req(port, "/__fake/stats"))
        print(sum(1 for r in st["os_rows"] if r["title"] == sys.argv[3]))
    else:
        sys.exit(2)


main()
