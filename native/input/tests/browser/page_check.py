#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Drives the controller page in a real (headless) browser against a real engine
and a private headless sway, with phone-sized touch emulation, and checks what
the compositor delivered. Not part of CI (needs Playwright browsers); run by hand:

    python3 page_check.py <engine-binary> <probe-example-binary> <shots-dir>

Everything runs under a private runtime directory; nothing touches a real session.
"""
import json, os, shutil, socket, subprocess, sys, tempfile, time
from pathlib import Path
from playwright.sync_api import sync_playwright

engine_bin, probe_bin, shots = sys.argv[1], sys.argv[2], Path(sys.argv[3])
shots.mkdir(parents=True, exist_ok=True)
root = Path(tempfile.mkdtemp(prefix="taos-input-page-"))
rt = root / "rt"; rt.mkdir(mode=0o700)
for d in ("home", "state", "cfg", "tmp"): (root / d).mkdir()
base = {"PATH": os.environ["PATH"], "HOME": str(root / "home"), "XDG_RUNTIME_DIR": str(rt),
        "XDG_STATE_HOME": str(root / "state"), "XDG_CONFIG_HOME": str(root / "cfg"),
        "TMPDIR": str(root / "tmp"), "LANG": "C.UTF-8"}
procs = []

def spawn(cmd, env, **kw):
    p = subprocess.Popen(cmd, env=env, **kw); procs.append(p); return p

def wait(cond, secs=20):
    end = time.time() + secs
    while time.time() < end:
        v = cond()
        if v: return v
        time.sleep(0.05)
    raise SystemExit("timed out")

def ctl(req):
    s = socket.socket(socket.AF_UNIX); s.connect(str(root / "ctl.sock"))
    s.sendall((json.dumps(req) + "\n").encode()); data = s.makefile().readline(); s.close()
    return json.loads(data)

def events(log):
    try: return [json.loads(l) for l in Path(log).read_text().splitlines() if l]
    except FileNotFoundError: return []

try:
    (root / "sway.conf").write_text('xwayland disable\ndefault_border none\nfor_window [app_id="taos-probe"] fullscreen enable\n')
    spawn(["sway", "-c", str(root / "sway.conf")], {**base, "WLR_BACKENDS": "headless", "WLR_RENDERER": "pixman", "WLR_LIBINPUT_NO_DEVICES": "1"}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    disp = wait(lambda: next((p.name for p in rt.iterdir() if p.name.startswith("wayland-") and not p.name.endswith(".lock")), None))
    wait(lambda: any(p.name.startswith("sway-ipc.") for p in rt.iterdir()))
    env = {**base, "WAYLAND_DISPLAY": disp}
    eng = spawn([engine_bin, "serve", "--bind", "127.0.0.1", "--port", "17981", "--allow-net", "127.0.0.1/32",
                 "--control", str(root / "ctl.sock"), "--state-dir", str(root / "estate")], env, stderr=subprocess.DEVNULL)
    wait(lambda: (root / "ctl.sock").exists())
    wait(lambda: ctl({"op": "status"})["compositor"])
    log = root / "probe.log"
    spawn([probe_bin, str(log)], env)
    wait(lambda: any(e["e"] == "ready" for e in events(log)))
    url = "http://127.0.0.1:17981/"

    with sync_playwright() as pw:
        br = pw.chromium.launch()
        ctx = br.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=3, has_touch=True, is_mobile=True,
                             color_scheme="dark", user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148")
        page = ctx.new_page()
        # A phone reaches the engine over plain HTTP, where WebCrypto does not exist.
        page.add_init_script("Object.defineProperty(Crypto.prototype, 'subtle', {get(){return undefined}})")
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "403" not in m.text else None)  # 403 is the wrong code on purpose
        page.goto(url)
        page.screenshot(path=str(shots / "01-pair.png"))
        # wrong code first
        page.fill("#code", "000000")
        page.wait_for_selector("#pair-msg:not([hidden])")
        page.screenshot(path=str(shots / "02-pair-error.png"))
        code = ctl({"op": "pair"})["code"]
        page.fill("#code", code)
        page.wait_for_selector("#status[data-state=connected]")
        page.wait_for_timeout(2300)
        page.screenshot(path=str(shots / "03-control.png"))

        cdp = ctx.new_cdp_session(page)
        box = page.locator("#pad").bounding_box()
        cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        def touch(kind, pts): cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": [{"x": x, "y": y, "id": i} for i, (x, y) in enumerate(pts)]})

        # one finger tap -> left click
        touch("touchStart", [(cx, cy)]); page.wait_for_timeout(60); touch("touchEnd", [])
        wait(lambda: [e for e in events(log) if e["e"] == "button"][:2])
        b = [(e["b"], e["down"]) for e in events(log) if e["e"] == "button"]
        assert b[:2] == [(272, True), (272, False)], b
        # one finger move -> pointer moves
        m0 = len([e for e in events(log) if e["e"] == "motion"])
        touch("touchStart", [(cx, cy)])
        for i in range(1, 11):
            touch("touchMove", [(cx + 8 * i, cy)]); page.wait_for_timeout(16)
        page.screenshot(path=str(shots / "04-touching.png"))
        touch("touchEnd", [])
        wait(lambda: len([e for e in events(log) if e["e"] == "motion"]) > m0)
        # two finger tap -> right click
        touch("touchStart", [(cx - 30, cy), (cx + 30, cy)]); page.wait_for_timeout(60); touch("touchEnd", [])
        wait(lambda: any(e["e"] == "button" and e["b"] == 273 for e in events(log)))

        # sticky modifier + keyboard
        page.tap("[data-mod=ctrl]")
        page.screenshot(path=str(shots / "05-ctrl-armed.png"))
        page.tap("[data-mod=ctrl]")  # tap again clears
        page.tap("#kbd-btn")
        assert page.evaluate("document.activeElement.id") == "kbd"
        page.keyboard.type("héllo")
        wait(lambda: len([e for e in events(log) if e["e"] == "key" and e["down"]]) >= 5)
        keys = [e["k"] for e in events(log) if e["e"] == "key" and e["down"]]
        assert keys[-5:] == [35, 200, 38, 38, 24], keys   # h, é (pool), l, l, o
        page.keyboard.press("Backspace")
        wait(lambda: [e for e in events(log) if e["e"] == "key" and e["down"] and e["k"] == 14])
        page.screenshot(path=str(shots / "06-keyboard.png"))

        # settings sheet and landscape
        page.tap("#open-settings"); page.wait_for_timeout(300)
        page.screenshot(path=str(shots / "07-settings.png"))
        page.tap("#close-settings")
        page.set_viewport_size({"width": 844, "height": 390}); page.wait_for_timeout(300)
        page.screenshot(path=str(shots / "08-landscape.png"))

        assert not errors, errors
        br.close()
    print("PAGE CHECK OK", [s.name for s in sorted(shots.iterdir())])
finally:
    for p in procs[::-1]:
        try: p.kill(); p.wait(timeout=5)
        except Exception: pass
    shutil.rmtree(root, ignore_errors=True)
