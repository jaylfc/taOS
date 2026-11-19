"""taos-desktop-session package: tree lint, session entry point, wallpaper helper, greeter, taos-desktop."""

from __future__ import annotations

import configparser
import os
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "distro" / "packages" / "taos-desktop-session"
SESSION = PKG / "usr/bin/taos-desktop-session"
DESKTOP = PKG / "usr/bin/taos-desktop"
LIB = PKG / "usr/lib/taos/desktop-session"
GREETER = LIB / "greeter"
AUTOSTART = LIB / "autostart"
WALL = LIB / "wallpaper"
SWAYCONF = PKG / "etc/taos/sway/config"
FXCONF = PKG / "etc/taos/sway/config-effects"
GREETD = PKG / "etc/taos/greetd/config.toml"
CSS = PKG / "etc/taos/greetd/gtkgreet.css"
DROPIN = PKG / "etc/taos/greetd/greetd.service-dropin.conf"
POSTINST = PKG / "DEBIAN/postinst"
SESSION_FILE = PKG / "usr/share/wayland-sessions/taos.desktop"
SHELLS = ["sh"] + (["/bin/dash"] if Path("/bin/dash").exists() else [])
SCRIPTS = [SESSION, DESKTOP, GREETER, AUTOSTART, WALL,
           PKG / "DEBIAN/postinst", PKG / "DEBIAN/prerm", PKG / "DEBIAN/postrm"]


def stub(bindir: Path, name: str, body: str) -> Path:
    bindir.mkdir(parents=True, exist_ok=True)
    p = bindir / name
    p.write_text("#!/bin/sh\n" + body)
    p.chmod(0o755)
    return p


@pytest.fixture
def box(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    (tmp_path / "run").mkdir()
    return {"tmp": tmp_path, "bin": tmp_path / "bin", "home": home, "run": tmp_path / "run",
            "log": tmp_path / "log"}


def env_for(box, **extra):
    e = {"PATH": f"{box['bin']}:/usr/bin:/bin", "HOME": str(box["home"]),
         "XDG_RUNTIME_DIR": str(box["run"]), "TAOS_SESSION_NO_JOURNAL": "1",
         "TAOS_DISPLAY_NODE": str(box["tmp"] / "no-such-node"),
         "TAOS_COMPOSITOR": str(box["tmp"] / "no-such-compositor")}
    e.update(extra)
    return e


def run(script, box, *args, shell="sh", **extra):
    return subprocess.run([shell, str(script), *args], env=env_for(box, **extra),
                          capture_output=True, text=True)


# ---------------------------------------------------------------- tree lint

def test_control_and_tree():
    ctl = (PKG / "DEBIAN/control").read_text()
    assert "Package: taos-desktop-session" in ctl and "Architecture: all" in ctl
    deps = [d.strip() for d in
            next(l for l in ctl.splitlines() if l.startswith("Depends:"))[8:].split(",")]
    assert deps == ["greetd", "gtkgreet", "cage", "sway", "swaybg", "foot", "xwayland",
                    "taos-wallpapers", "taos-gpu"]
    for s in SCRIPTS:
        assert os.access(s, os.X_OK), f"{s} not executable"
    for conf in (PKG / "DEBIAN/conffiles").read_text().split():
        assert (PKG / conf.lstrip("/")).is_file(), conf
    assert not [p for p in PKG.rglob("*") if p.is_file() and chr(0x2014) in p.read_text(errors="ignore")]


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_scripts_parse(script):
    for sh in SHELLS:
        r = subprocess.run([sh, "-n", str(script)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    assert script.read_text().startswith("#!/bin/sh\n")


def test_no_forbidden_calls():
    text = "".join(p.read_text() for p in SCRIPTS) + SWAYCONF.read_text()
    assert "pkill" not in text and "killall" not in text
    assert "--unsupported-gpu" not in text
    assert "exec swayidle" not in SWAYCONF.read_text() and "swayidle" not in SWAYCONF.read_text()


# ---------------------------------------------------------------- session

def sway_stub(box, rc=0):
    return stub(box["bin"], "sway",
                f'echo "$@" > {box["log"]}.args\nenv > {box["log"]}.env\nexit {rc}\n')


@pytest.mark.parametrize("shell", SHELLS)
def test_session_exports_and_runs_sway(box, shell):
    sway_stub(box)
    r = run(SESSION, box, shell=shell)
    assert r.returncode == 0, r.stderr
    assert Path(f"{box['log']}.args").read_text().split() == ["--config", "/etc/taos/sway/config"]
    env = dict(l.split("=", 1) for l in Path(f"{box['log']}.env").read_text().splitlines() if "=" in l)
    assert env["XDG_CURRENT_DESKTOP"] == "taOS"
    assert env["XDG_SESSION_TYPE"] == "wayland"
    assert env["MOZ_ENABLE_WAYLAND"] == "1"
    assert env["QT_QPA_PLATFORM"] == "wayland"
    assert env["PATH"].startswith(f"{box['home']}/.local/bin:")
    assert "WLR_DRM_DEVICES" not in env
    assert "--unsupported-gpu" not in Path(f"{box['log']}.args").read_text()
    # the user drop-in directory is created so the include glob matches
    assert list((box["home"] / ".config/taos/sway.d").glob("*.conf"))


def carried_stub(box, version_rc=0):
    d = box["tmp"] / "opt/taos/compositor/bin"
    return stub(d, "sway",
                f'[ "$1" = "--version" ] && exit {version_rc}\n'
                f'echo "$@" > {box["log"]}.fxargs\nenv > {box["log"]}.fxenv\nexit 0\n')


@pytest.mark.parametrize("shell", SHELLS)
def test_session_prefers_carried_compositor_with_effects(box, shell):
    sway_stub(box)
    fx = carried_stub(box)
    r = run(SESSION, box, shell=shell, TAOS_COMPOSITOR=str(fx))
    assert r.returncode == 0, r.stderr
    assert Path(f"{box['log']}.fxargs").read_text().split() == ["--config", "/etc/taos/sway/config-effects"]
    assert not Path(f"{box['log']}.args").exists(), "the distribution sway must not run"
    env = dict(l.split("=", 1) for l in Path(f"{box['log']}.fxenv").read_text().splitlines() if "=" in l)
    # swaymsg and friends from the carried build come first on PATH
    assert env["PATH"].startswith(str(fx.parent) + ":")


def test_session_falls_back_when_carried_compositor_is_broken(box):
    sway_stub(box)
    fx = carried_stub(box, version_rc=127)
    r = run(SESSION, box, TAOS_COMPOSITOR=str(fx))
    assert r.returncode == 0, r.stderr
    assert Path(f"{box['log']}.args").read_text().split() == ["--config", "/etc/taos/sway/config"]
    assert not Path(f"{box['log']}.fxargs").exists()


def test_session_explicit_config_wins(box):
    fx = carried_stub(box)
    r = run(SESSION, box, TAOS_COMPOSITOR=str(fx), TAOS_SWAY_CONFIG="/tmp/x.conf")
    assert r.returncode == 0, r.stderr
    assert Path(f"{box['log']}.fxargs").read_text().split() == ["--config", "/tmp/x.conf"]


def test_effects_config_layers_on_the_standard_one():
    c = FXCONF.read_text()
    assert c.splitlines()[c.splitlines().index("include /etc/taos/sway/config")] == "include /etc/taos/sway/config"
    for needle in ("blur enable", "corner_radius", "shadows enable", "animation_duration_ms",
                   'layer_effects "taos-bar"'):
        assert needle in c, needle
    # effects commands never leak into the file the distribution's sway reads
    for needle in ("blur", "corner_radius", "shadows", "animation_duration_ms", "layer_effects"):
        assert needle not in SWAYCONF.read_text(), needle
    assert "/etc/taos/sway/config-effects" in (PKG / "DEBIAN/conffiles").read_text()


def test_session_restricts_to_display_gpu(box):
    sway_stub(box)
    real = box["tmp"] / "card1"
    real.write_text("")
    link = box["tmp"] / "taos-display"
    link.symlink_to(real)
    r = run(SESSION, box, TAOS_DISPLAY_NODE=str(link))
    assert r.returncode == 0
    env = dict(l.split("=", 1) for l in Path(f"{box['log']}.env").read_text().splitlines() if "=" in l)
    assert env["WLR_DRM_DEVICES"] == str(real)


def test_session_ignores_inherited_wlr_devices_when_node_missing(box):
    sway_stub(box)
    r = run(SESSION, box, WLR_DRM_DEVICES="/dev/dri/card0:/dev/dri/card1")
    assert r.returncode == 0
    assert "WLR_DRM_DEVICES" not in Path(f"{box['log']}.env").read_text()
    assert "missing" in r.stderr


@pytest.mark.parametrize("shell", SHELLS)
def test_session_fails_loudly_on_fast_sway_exit(box, shell):
    sway_stub(box, rc=1)
    r = run(SESSION, box, shell=shell)
    assert r.returncode == 1
    assert "did not start" in r.stderr


def test_session_logs_through_systemd_cat(box):
    sway_stub(box)
    stub(box["bin"], "systemd-cat", f'echo "$@" > {box["log"]}.cat\nshift 2\nexec "$@"\n')
    e = env_for(box)
    del e["TAOS_SESSION_NO_JOURNAL"]
    r = subprocess.run(["sh", str(SESSION)], env=e, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert Path(f"{box['log']}.cat").read_text().split()[:2] == ["-t", "taos-desktop-session"]
    assert Path(f"{box['log']}.args").exists()


# ---------------------------------------------------------------- greeter

def test_greeter_uses_display_gpu_and_cage(box):
    stub(box["bin"], "cage", f'echo "$@" > {box["log"]}.args\necho "$WLR_DRM_DEVICES" > {box["log"]}.wlr\n')
    real = box["tmp"] / "card0"
    real.write_text("")
    link = box["tmp"] / "taos-display"
    link.symlink_to(real)
    assert run(GREETER, box, TAOS_DISPLAY_NODE=str(link)).returncode == 0
    assert Path(f"{box['log']}.wlr").read_text().strip() == str(real)
    args = Path(f"{box['log']}.args").read_text()
    assert args.startswith("-s -d -- gtkgreet") and "/etc/taos/greetd/gtkgreet.css" in args
    assert "-l" not in args.split()
    r = run(GREETER, box)
    assert r.returncode == 0 and Path(f"{box['log']}.wlr").read_text().strip() == ""


def test_greeter_config_and_session_files():
    cfg = tomllib.loads(GREETD.read_text())
    assert cfg["terminal"]["vt"] == 7
    assert cfg["default_session"]["command"] == "/usr/lib/taos/desktop-session/greeter"
    assert cfg["default_session"]["user"] == "_greetd"
    assert "--config /etc/taos/greetd/config.toml" in DROPIN.read_text()
    assert (PKG / "etc/greetd/environments").read_text().split() == ["taos-desktop-session"]
    cp = configparser.ConfigParser(interpolation=None)
    cp.optionxform = str
    cp.read_string(SESSION_FILE.read_text())
    de = cp["Desktop Entry"]
    assert (de["Name"], de["Exec"], de["Type"], de["DesktopNames"]) == \
        ("taOS", "taos-desktop-session", "Application", "taOS")


def test_css_has_fallback_and_parses():
    css = CSS.read_text()
    assert "background-color: #141415" in css and "/usr/share/taos/wallpapers/neural/dark-3840x2160.jpg" in css
    assert "border-radius: 16px" in css and "backdrop-filter" not in css
    assert css.count("{") == css.count("}")
    gi = pytest.importorskip("gi", reason="PyGObject not available")
    try:
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
    except (ValueError, ImportError):
        pytest.skip("GTK 3 typelib not available")
    errors = []
    prov = Gtk.CssProvider()
    prov.connect("parsing-error", lambda p, sec, err: errors.append(err.message))
    prov.load_from_path(str(CSS))
    # A missing wallpaper file is the only error allowed (the solid colour remains).
    assert [e for e in errors if "wallpapers" not in e and "Failed to import" not in e
            and "load" not in e.lower()] == [], errors


# ---------------------------------------------------------------- autostart

def test_autostart(box):
    r = run(AUTOSTART, box)
    assert r.returncode == 0 and "taOS shell not installed; bare session" in r.stdout
    stub(box["bin"], "taos-shell-session", f'echo "$@" > {box["log"]}.sh\n')
    assert run(AUTOSTART, box).returncode == 0
    assert Path(f"{box['log']}.sh").read_text().strip() == "start"


# ---------------------------------------------------------------- wallpaper

OUTPUTS = """[
  {
    "name": "DP-1",
    "make": "Dell, Inc.",
    "active": true,
    "current_mode": {
      "width": 1920,
      "height": 1080,
      "refresh": 60000
    },
    "modes": [
      {
        "width": 3840,
        "height": 2160,
        "refresh": 30000
      }
    ],
    "rect": {"x": 0, "y": 0, "width": 1920, "height": 1080}
  },
  {
    "name": "HDMI-A-1",
    "active": true,
    "current_mode": {
      "width": 1080,
      "height": 2400
    }
  },
  {
    "name": "eDP-1",
    "active": false,
    "current_mode": {
      "width": 800,
      "height": 600
    }
  }
]
"""


def wall_env(box, outputs=OUTPUTS, known=("1920x1080", "1080x2400", "3840x2160"), fail_all=False):
    walls = box["tmp"] / "walls"
    walls.mkdir(exist_ok=True)
    for k in known:
        (walls / f"dark-{k}.jpg").write_text("")
    stub(box["bin"], "taos-wallpaper",
         'echo "$@" >> ' + f'{box["log"]}.tw\n'
         + ('exit 1\n' if fail_all else
            'while [ $# -gt 0 ]; do case "$1" in --size) s=$2; shift;; esac; shift; done\n'
            f'[ -e {walls}/dark-$s.jpg ] || exit 1\necho {walls}/dark-$s.jpg\n'))
    stub(box["bin"], "swaymsg", f"cat <<'EOF'\n{outputs}EOF\n")
    stub(box["bin"], "swaybg", f'echo "$@" >> {box["log"]}.bg\n'
         "trap 'kill $!; exit 0' TERM\nsleep 30 &\nwait\n")
    return walls


def swaybg_calls(box):
    return sorted(Path(f"{box['log']}.bg").read_text().splitlines())


def stop_started(box):
    pf = box["run"] / "taos-swaybg.pids"
    for p in pf.read_text().split() if pf.exists() else []:
        try:
            os.kill(int(p), 15)
        except OSError:
            pass


@pytest.mark.parametrize("shell", SHELLS)
def test_wallpaper_per_output_sizes(box, shell):
    walls = wall_env(box)
    try:
        r = run(WALL, box, shell=shell)
        assert r.returncode == 0, r.stderr
        import time
        time.sleep(0.3)
        assert swaybg_calls(box) == sorted([
            f"-o DP-1 -m fill -i {walls}/dark-1920x1080.jpg",
            f"-o HDMI-A-1 -m fill -i {walls}/dark-1080x2400.jpg"])
        assert len((box["run"] / "taos-swaybg.pids").read_text().split()) == 2
    finally:
        stop_started(box)


def test_wallpaper_falls_back_to_largest_then_solid(box):
    walls = wall_env(box, known=("3840x2160",))
    try:
        assert run(WALL, box).returncode == 0
        import time
        time.sleep(0.3)
        assert all(c.endswith(f"-i {walls}/dark-3840x2160.jpg") for c in swaybg_calls(box))
    finally:
        stop_started(box)
    Path(f"{box['log']}.bg").unlink()
    wall_env(box, fail_all=True)
    try:
        assert run(WALL, box).returncode == 0
        import time
        time.sleep(0.3)
        assert all("-c #141415" in c for c in swaybg_calls(box)) and len(swaybg_calls(box)) == 2
    finally:
        stop_started(box)


def test_wallpaper_without_outputs_uses_wildcard(box):
    walls = wall_env(box, outputs="[]\n")
    try:
        assert run(WALL, box).returncode == 0
        import time
        time.sleep(0.3)
        assert swaybg_calls(box) == [f"-o * -m fill -i {walls}/dark-3840x2160.jpg"]
    finally:
        stop_started(box)


def test_wallpaper_replaces_only_its_own_swaybg(box):
    wall_env(box)
    other = subprocess.Popen([str(box["bin"] / "swaybg")], env=env_for(box))  # not ours
    try:
        assert run(WALL, box).returncode == 0
        first = (box["run"] / "taos-swaybg.pids").read_text().split()
        import time
        time.sleep(0.3)
        assert run(WALL, box).returncode == 0
        time.sleep(0.3)
        second = (box["run"] / "taos-swaybg.pids").read_text().split()
        assert set(first).isdisjoint(second)
        for p in first:
            assert not Path(f"/proc/{p}").exists() or Path(f"/proc/{p}/stat").read_text().split()[2] == "Z"
        assert other.poll() is None
    finally:
        stop_started(box)
        other.terminate()
        other.wait()


# ---------------------------------------------------------------- sway config

def test_sway_config_content():
    c = SWAYCONF.read_text()
    assert "include /etc/taos/sway.d/*.conf" in c
    assert "include ~/.config/taos/sway.d/*.conf" in c
    for needle in ("exec_always /usr/lib/taos/desktop-session/wallpaper",
                   "exec /usr/lib/taos/desktop-session/autostart",
                   "default_border pixel 1", "gaps inner 8", "focus_follows_mouse no",
                   "font pango:Inter 11", "output * scale 1",
                   "bindsym $mod+Return exec foot", "bindsym $mod+q kill",
                   "bindsym $mod+Shift+e exec swaynag", "bindsym $mod+f fullscreen",
                   "bindsym $mod+space floating toggle", "taos-screenshot"):
        assert needle in c, needle
    assert "set $mod Mod4" in c
    assert c.index("include /etc/taos") < c.index("exec /usr/lib/taos/desktop-session/autostart")


def test_sway_validate_if_available(tmp_path):
    sway = shutil.which("sway")
    if not sway:
        pytest.skip("sway is not installed on this host; config not validated by sway itself")
    cfg = tmp_path / "config"
    cfg.write_text(SWAYCONF.read_text().replace("/etc/taos/sway.d/", str(tmp_path / "sway.d") + "/"))
    (tmp_path / "sway.d").mkdir()
    (tmp_path / "sway.d/00.conf").write_text("# empty\n")
    kb = tmp_path / "root/etc/default"
    kb.mkdir(parents=True)
    (kb / "keyboard").write_text('XKBLAYOUT="gb"\nXKBVARIANT="extd"\nXKBOPTIONS="ctrl:nocaps"\n')
    postinst(tmp_path / "root")
    (tmp_path / "sway.d/10-keyboard.conf").write_text(
        (tmp_path / "root/etc/taos/sway.d/10-keyboard.conf").read_text())
    home = tmp_path / "h"
    (home / ".config/taos/sway.d").mkdir(parents=True)
    (home / ".config/taos/sway.d/00.conf").write_text("# empty\n")
    r = subprocess.run([sway, "--validate", "--config", str(cfg)], capture_output=True, text=True,
                       env={"PATH": os.environ["PATH"], "HOME": str(home), "XDG_RUNTIME_DIR": str(tmp_path),
                            "WLR_BACKENDS": "headless", "WLR_RENDERER": "pixman",
                            "WLR_LIBINPUT_NO_DEVICES": "1",
                            "LD_LIBRARY_PATH": os.environ.get("LD_LIBRARY_PATH", "")}, timeout=30)
    assert r.returncode == 0, r.stdout + r.stderr


# ---------------------------------------------------------------- postinst and taos-desktop

def postinst(root: Path, op="configure"):
    return subprocess.run(["sh", str(POSTINST), op], env={"PATH": os.environ["PATH"], "TAOS_ROOT": str(root)},
                          capture_output=True, text=True)


def test_postinst_keyboard_dropin(tmp_path):
    text = POSTINST.read_text()
    code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    for word in ("enable", "start", "set-default", "greetd"):
        assert word not in code, word
    root = tmp_path / "root"
    (root / "etc/default").mkdir(parents=True)
    (root / "etc/default/keyboard").write_text(
        'XKBMODEL="pc105"\nXKBLAYOUT="gb"\nXKBVARIANT="extd"\nXKBOPTIONS="ctrl:nocaps,compose:ralt"\nBACKSPACE="guess"\n')
    assert postinst(root).returncode == 0
    d = root / "etc/taos/sway.d/10-keyboard.conf"
    out = d.read_text()
    assert 'xkb_layout "gb"' in out and 'xkb_variant "extd"' in out
    assert 'xkb_options "ctrl:nocaps,compose:ralt"' in out and "input type:keyboard {" in out
    d.write_text("# mine\n")
    assert postinst(root).returncode == 0 and d.read_text() == "# mine\n"


def test_postinst_without_keyboard_file_and_hostile_values(tmp_path):
    root = tmp_path / "root"
    assert postinst(root).returncode == 0
    assert not (root / "etc/taos/sway.d/10-keyboard.conf").exists()
    (root / "etc/default").mkdir(parents=True)
    (root / "etc/default/keyboard").write_text('XKBLAYOUT="us\nexec evil"\n')
    postinst(root)
    out = (root / "etc/taos/sway.d/10-keyboard.conf").read_text()
    assert " " not in out.split('xkb_layout "')[1].split('"')[0]


def test_postrm_purge_removes_dropin_only(tmp_path):
    root = tmp_path / "root"
    sd = root / "etc/taos/sway.d"
    sd.mkdir(parents=True)
    (sd / "10-keyboard.conf").write_text("x")
    (sd / "50-other.conf").write_text("y")
    run_ = lambda op: subprocess.run(["sh", str(PKG / "DEBIAN/postrm"), op],
                                     env={"PATH": os.environ["PATH"], "TAOS_ROOT": str(root)})
    run_("remove")
    assert (sd / "10-keyboard.conf").exists()
    run_("purge")
    assert not (sd / "10-keyboard.conf").exists() and (sd / "50-other.conf").exists()


def desktop(box, root, *args, uid="1000", shell="sh"):
    return subprocess.run(
        [shell, str(DESKTOP), *args],
        env=env_for(box, TAOS_ROOT=str(root), TAOS_DESKTOP_UID=uid), capture_output=True, text=True)


def systemctl_stub(box):
    stub(box["bin"], "systemctl",
         f'echo "$@" >> {box["log"]}.sc\n'
         'case "$1" in is-enabled) echo disabled; exit 1;; get-default) echo multi-user.target;; esac\nexit 0\n')


@pytest.mark.parametrize("shell", SHELLS)
def test_taos_desktop_status_without_root(box, shell):
    systemctl_stub(box)
    root = box["tmp"] / "root"
    (root / "etc/taos/greetd").mkdir(parents=True)
    (root / "etc/taos/greetd/config.toml").write_text(GREETD.read_text())
    r = desktop(box, root, "status", shell=shell)
    assert r.returncode == 0, r.stderr
    for needle in ("greetd: disabled", "default target: multi-user.target", "not active", "vt: 7",
                   "display gpu: none", "taos-shell-session missing"):
        assert needle in r.stdout, r.stdout


def test_taos_desktop_requires_root_and_never_starts(box):
    systemctl_stub(box)
    root = box["tmp"] / "root"
    for sub in ("enable", "disable"):
        r = desktop(box, root, sub)
        assert r.returncode == 1 and "sudo taos-desktop" in r.stderr
    assert not Path(f"{box['log']}.sc").exists()
    assert "systemctl start" not in DESKTOP.read_text().replace("systemctl start greetd", "")
    assert desktop(box, root, "bogus").returncode == 2


def test_taos_desktop_enable_disable_as_root(box):
    systemctl_stub(box)
    root = box["tmp"] / "root"
    (root / "usr/sbin").mkdir(parents=True)
    (root / "usr/sbin/greetd").write_text("")
    (root / "usr/sbin/greetd").chmod(0o755)
    (root / "etc/taos/greetd").mkdir(parents=True)
    (root / "etc/taos/greetd/config.toml").write_text(GREETD.read_text())
    r = desktop(box, root, "enable", uid="0")
    assert r.returncode == 0, r.stderr
    assert "systemctl start greetd" in r.stdout
    assert (root / "etc/systemd/system/greetd.service.d/10-taos.conf").is_symlink()
    calls = Path(f"{box['log']}.sc").read_text().splitlines()
    assert calls == ["enable greetd", "set-default graphical.target"]
    r = desktop(box, root, "disable", uid="0")
    assert r.returncode == 0
    assert not (root / "etc/systemd/system/greetd.service.d/10-taos.conf").exists()
    calls = Path(f"{box['log']}.sc").read_text().splitlines()[2:]
    assert calls == ["set-default multi-user.target", "disable greetd"]


def test_enable_rolls_back_dropin_when_greetd_enable_fails(box):
    stub(box["bin"], "systemctl", 'case "$1" in enable) exit 1;; esac\nexit 0\n')
    root = box["tmp"] / "root"
    (root / "usr/sbin").mkdir(parents=True)
    (root / "usr/sbin/greetd").write_text("")
    (root / "usr/sbin/greetd").chmod(0o755)
    (root / "etc/taos/greetd").mkdir(parents=True)
    (root / "etc/taos/greetd/config.toml").write_text(GREETD.read_text())
    r = desktop(box, root, "enable", uid="0")
    assert r.returncode == 1
    assert not (root / "etc/systemd/system/greetd.service.d/10-taos.conf").exists()


# ---------------------------------------------------------------- build and setup

def test_package_builds(tmp_path):
    if not shutil.which("dpkg-deb"):
        pytest.skip("dpkg-deb not available")
    deb = tmp_path / "t.deb"
    subprocess.run(["dpkg-deb", "--root-owner-group", "--build", str(PKG), str(deb)], check=True,
                   capture_output=True)
    listing = subprocess.run(["dpkg-deb", "-c", str(deb)], capture_output=True, text=True, check=True).stdout
    for path in ("./usr/bin/taos-desktop-session", "./usr/bin/taos-desktop",
                 "./usr/lib/taos/desktop-session/greeter", "./usr/lib/taos/desktop-session/autostart",
                 "./usr/lib/taos/desktop-session/wallpaper", "./etc/taos/sway/config",
                 "./etc/taos/greetd/config.toml", "./etc/taos/greetd/gtkgreet.css",
                 "./usr/share/wayland-sessions/taos.desktop"):
        assert path in listing, path


def test_setup_sh_step8_never_enables():
    setup = (REPO / "distro/setup.sh").read_text()
    assert "taos-desktop-session_*_all.deb" in setup
    code = [l for l in setup.splitlines() if not l.lstrip().startswith(("#", "echo"))]
    assert not any("taos-desktop enable" in l for l in code)
    assert "optional last step" in setup
