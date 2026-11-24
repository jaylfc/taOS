#!/usr/bin/env python3
# Drives MY headless session only (XDG_RUNTIME_DIR under the work dir): launches an app, real pointer events via vptool,
# grim shots. usage: matrix.py <name> <appid-regex> <launch command...>
import json, os, re, subprocess, sys, time
W = os.environ["W"]
RT = W + "/xdg-run"
env = dict(os.environ, XDG_RUNTIME_DIR=RT, WAYLAND_DISPLAY="wayland-1", HOME=W + "/home", XDG_CONFIG_HOME=W + "/home/.config",
           PATH=W + "/shim:/opt/taos/compositor/bin:/usr/bin:/bin", DBUS_SESSION_BUS_ADDRESS="unix:path=/nonexistent", GSETTINGS_BACKEND="memory")
env["SWAYSOCK"] = [RT + "/" + f for f in os.listdir(RT) if f.startswith("sway-ipc")][0]
SHOTS = W + "/shots"
def run(*a, **k): return subprocess.run(a, env=env, capture_output=True, text=True, **k).stdout
def sm(*a): return run("/opt/taos/compositor/bin/swaymsg", *a)
def tree():
    t = json.loads(run("/opt/taos/compositor/bin/swaymsg", "-t", "get_tree", "-r")); out = []
    def w(n, ws=""):
        if n["type"] == "workspace": ws = n["name"]
        if "shell" in n: out.append(dict(n, ws=ws))
        for k in n.get("nodes", []) + n.get("floating_nodes", []): w(k, ws)
    w(t); return out
def wins(): return {n["id"]: n for n in tree() if n["ws"] != "__i3_scratch"}
def app(n): return n.get("app_id") or (n.get("window_properties") or {}).get("class") or ""
def shot(name):
    p = "%s/%s.png" % (SHOTS, name); run("/usr/bin/grim", p); print("shot", p); return p
def strips():
    try: return {s["id"]: s for s in json.loads(run("taos-frame", "state"))["strips"]}
    except Exception: return {}
def ptr(*ops): subprocess.run(["vptool", "1280", "720"] + [str(o) for o in ops], env=env, capture_output=True)
def btn_xy(wid, which):
    s = strips().get(wid)
    if not s: return None
    order = ["minimise", "full", "close"] if s["right"] else ["close", "minimise", "full"]
    return (s["x"] + 12 + order.index(which) * 28, s["y"] + 12)
def hover_shot(wid, which, name):
    # the virtual pointer must stay alive while grim takes the picture (destroying it ends the hover)
    x, y = btn_xy(wid, which)
    p = subprocess.Popen(["vptool", "1280", "720", "move", str(x), str(y), "sleep", "3000"], env=env)
    time.sleep(1.4); shot(name); p.wait()
def click(x, y): ptr("move", x, y, "sleep", 150, "press", "sleep", 120, "release", "sleep", 250)
def away(): ptr("move", 640, 600, "sleep", 200)
def wait_new(before, rx, t=25):
    end = time.time() + t
    while time.time() < end:
        for i, n in wins().items():
            if i not in before and re.search(rx, app(n)): return i
        time.sleep(0.5)
    return None
def killall():
    for i in list(wins()): sm("[con_id=%d] kill" % i)
    time.sleep(1.5)
def main():
    name, rx, cmd = sys.argv[1], sys.argv[2], sys.argv[3:]
    killall(); time.sleep(1)
    before = set(wins())
    sm("exec", "bash -c '. %s; exec %s'" % " ".join(cmd))
    a = wait_new(before, rx)
    if a is None: print("NO WINDOW for", name, [app(n) for n in wins().values()]); return
    time.sleep(2.5)
    sm("[con_id=%d] move position 60 90" % a); time.sleep(0.6)
    b0 = set(wins()); sm("exec foot --title other"); b = wait_new(b0, r"^foot$"); time.sleep(2)
    sm("[con_id=%d] move position 640 330" % b); time.sleep(0.6)
    away(); time.sleep(0.8)
    n = wins()[a]; print(name, "app_id", app(n), "border", n["border"], "deco", n["deco_rect"]["height"], "strip", strips().get(a, {}).get("show"))
    shot(name + "-1-unfocused")
    sm("[con_id=%d] focus" % a); away(); time.sleep(0.8); shot(name + "-2-focused-rest")
    if a in strips() and strips()[a]["show"]:
        for w_ in ("minimise", "close", "full"): hover_shot(a, w_, name + "-3-hover-" + w_)
        away()
        x, y = btn_xy(a, "full"); click(x, y); time.sleep(1.8); away(); shot(name + "-4-maximised")
        x, y = btn_xy(a, "full"); click(x, y); time.sleep(1.8); away(); shot(name + "-5-restored-from-max")
        x, y = btn_xy(a, "minimise"); click(x, y); time.sleep(1.5); away()
        vis = a in wins() and wins()[a]["ws"] != "__i3_scratch"
        print("after minimise: window on the desktop:", vis); shot(name + "-6-minimised")
        run("taos-window", "restore", str(a)); time.sleep(1.5); away(); shot(name + "-7-restored")
    sm("[con_id=%d] kill" % b)
main()
