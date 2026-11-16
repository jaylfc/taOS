"""taos-kiosk-keyboard builds a themed private config and starts squeekboard.

Also checks the surrounding wiring: both chromium launchers carry the IME
flags, and the installer only pulls in squeekboard under --keyboard.

Run: python3 -m pytest -q tests/session/test_taos_kiosk_keyboard.py
"""
import os
import pathlib
import re
import subprocess
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
BIN = ROOT / "session" / "bin"
WRAPPER = BIN / "taos-kiosk-keyboard"
DEFAULT_PKG = ROOT / "session" / "themes" / "taos-default"
IME_FLAGS = ("--enable-wayland-ime", "--wayland-text-input-version=3")


@pytest.fixture()
def env(tmp_path):
    sq = tmp_path / "sqbin"
    sq.mkdir()
    out = tmp_path / "sq.env"
    # Stub squeekboard: record the config dir it was started with, and the
    # gtk.css it would read, then exit.
    (sq / "squeekboard").write_text(
        '#!/bin/sh\n{ echo "CFG=$XDG_CONFIG_HOME"; '
        'cat "$XDG_CONFIG_HOME/gtk-3.0/gtk.css"; } > "%s"\n' % out)
    os.chmod(sq / "squeekboard", 0o755)
    pkgs = tmp_path / "pkgs"
    pkgs.mkdir()
    subprocess.run(["cp", "-R", str(DEFAULT_PKG), str(pkgs)], check=True)
    run = tmp_path / "run"
    run.mkdir()
    home = tmp_path / "home"
    (home / ".config").mkdir(parents=True)
    e = dict(os.environ,
             HOME=str(home),
             XDG_RUNTIME_DIR=str(run),
             TAOS_THEME_PATH=str(pkgs),
             TAOS_SESSION_STATE_DIR=str(tmp_path / "state"),
             TAOS_KEYBOARD_BIN_DIRS=str(sq))
    e.pop("XDG_CONFIG_HOME", None)
    e.pop("WAYLAND_DISPLAY", None)
    e.pop("SWAYSOCK", None)
    e["_extra"] = ""
    return e, tmp_path, out


def start(e, *args):
    e = {k: v for k, v in e.items() if k != "_extra"}
    return subprocess.run([str(WRAPPER), *args], env=e, capture_output=True, text=True, timeout=20)


def set_state(tmp, text):
    (tmp / "state").mkdir(exist_ok=True)
    (tmp / "state" / "theme").write_text(text)


@pytest.mark.parametrize("variant", ["dark", "light"])
def test_gtk_css_is_variant_then_metrics_and_env_is_set(env, variant):
    e, tmp, out = env
    set_state(tmp, "taos-default %s\n" % variant)
    r = start(e)
    assert r.returncode == 0, r.stderr
    cfg = e["XDG_RUNTIME_DIR"] + "/taos-keyboard"
    lines = out.read_text().splitlines()
    assert lines[0] == "CFG=" + cfg
    want = ((DEFAULT_PKG / "keyboard" / (variant + ".css")).read_text()
            + (DEFAULT_PKG / "keyboard" / "metrics.css").read_text())
    assert "\n".join(lines[1:]) + "\n" == want
    link = pathlib.Path(cfg) / "dconf"
    assert os.readlink(link) == e["HOME"] + "/.config/dconf"


def test_no_squeekboard_exits_1_with_a_message(env):
    e, tmp, out = env
    empty = tmp / "empty"
    empty.mkdir()
    e["TAOS_KEYBOARD_BIN_DIRS"] = str(empty)
    r = start(e)
    assert r.returncode == 1
    assert "no squeekboard" in r.stderr
    assert not out.exists()
    # Control: the same environment with the stub directory starts fine.
    e["TAOS_KEYBOARD_BIN_DIRS"] = str(tmp / "sqbin")
    assert start(e).returncode == 0 and out.exists()


def test_missing_theme_in_state_falls_back_loudly(env):
    e, tmp, out = env
    set_state(tmp, "no-such-theme light\n")
    r = start(e)
    assert r.returncode == 0
    assert "no-such-theme" in r.stderr and "falling back" in r.stderr
    css = "\n".join(out.read_text().splitlines()[1:]) + "\n"
    assert css.startswith((DEFAULT_PKG / "keyboard" / "dark.css").read_text())
    # Control: a state naming a real theme warns about nothing.
    out.unlink()
    set_state(tmp, "taos-default light\n")
    r = start(e)
    assert r.stderr == "" and out.exists()


def test_restart_replaces_the_previous_instance_by_pid(env):
    e, tmp, out = env
    # A long-lived squeekboard stub so there is something to replace.
    sq = tmp / "sqbin" / "squeekboard"
    # A shell loop, not `exec sleep`, so the process keeps the name squeekboard.
    sq.write_text('#!/bin/sh\necho "$$" >> "%s"\nwhile :; do sleep 1; done\n' % (tmp / "pids"))
    pids = tmp / "pids"
    first = subprocess.Popen([str(WRAPPER)], env={k: v for k, v in e.items() if k != "_extra"})
    try:
        for _ in range(50):
            if pids.exists() and pids.read_text().strip():
                break
            time.sleep(0.1)
        old = int(pids.read_text().split()[0])
        assert start(e, "--restart").returncode == 0
        for _ in range(100):
            if len(pids.read_text().split()) >= 2:
                break
            time.sleep(0.1)
        new = int(pids.read_text().split()[1])
        assert new != old
        first.wait(timeout=10)
        assert first.returncode == -15, "the old instance was not terminated"
    finally:
        pidfile = pathlib.Path(e["XDG_RUNTIME_DIR"]) / "taos-keyboard.pid"
        if pidfile.exists():
            try:
                os.kill(int(pidfile.read_text()), 15)
            except (ProcessLookupError, ValueError):
                pass
        first.kill()


def test_bad_argument_is_a_usage_error(env):
    e, tmp, out = env
    assert start(e, "--bogus").returncode == 2
    assert start(e, "--restart").returncode == 0  # control


@pytest.mark.parametrize("name", ["taos-kiosk-launch", "taos-app-launch"])
def test_chromium_launchers_carry_the_ime_flags(name):
    text = (BIN / name).read_text()
    for flag in IME_FLAGS:
        assert flag in text, "%s lacks %s" % (name, flag)
    # Control: the check can tell a flag that is not there.
    assert "--no-such-ime-flag" not in text


def test_installer_has_no_hardcoded_uid_and_installs_squeekboard_only_with_the_flag():
    text = (ROOT / "session" / "install-session.sh").read_text()
    assert "/run/user/999" not in text
    lines = text.splitlines()
    idx = [i for i, l in enumerate(lines) if re.match(r"\s*pkg_install squeekboard\b", l)]
    assert idx, "installer never installs squeekboard"
    for i in idx:
        assert lines[i].startswith(" "), "squeekboard install is not inside a block"
        j = i
        while lines[j].startswith(" ") or not lines[j].strip():
            j -= 1
        assert lines[j] == 'if [ "$KEYBOARD" = 1 ]; then'
    # Control: the unguarded install of swaybg is still at top level.
    assert "pkg_install swaybg" in lines
    # The mentions of squeekboard outside the guarded block are only comments
    # and the usage text, never a second install.
    assert len(re.findall(r"pkg_install\s+squeekboard", text)) == 1


def test_restart_asks_sway_to_start_it_when_a_compositor_is_there(env):
    """Measured on the handset: started as a child of the caller (su from root),
    the keyboard was in the caller's login session and logind killed it when
    that session closed, so a theme change left the device with no keyboard."""
    e, tmp, out = env
    stub = tmp / "swaybin"
    stub.mkdir()
    log = tmp / "swaymsg.log"
    (stub / "swaymsg").write_text('#!/bin/sh\necho "$SWAYSOCK|$*" >> "%s"\n' % log)
    os.chmod(stub / "swaymsg", 0o755)
    e2 = dict(e, PATH="%s:%s" % (stub, e["PATH"]), SWAYSOCK=str(tmp / "sway.sock"))
    r = start(e2, "--restart")
    assert r.returncode == 0, r.stderr
    assert log.read_text() == "%s|exec -- %s\n" % (tmp / "sway.sock", WRAPPER)
    time.sleep(0.5)
    assert not out.exists(), "the wrapper started squeekboard itself instead of leaving it to sway"
    # Control: the same call with no compositor still starts it directly.
    r = start(e, "--restart")
    assert r.returncode == 0, r.stderr
    for _ in range(50):
        if out.exists():
            break
        time.sleep(0.1)
    assert out.exists()


def test_no_runtime_dir_variable_falls_back_to_the_users_own(env):
    """The controller service calls this with no XDG_RUNTIME_DIR. It must use
    /run/user/<uid>, and when that does not exist say which dir is missing
    rather than die on the unset variable. `id` is stubbed so the test never
    touches the real runtime dir of whoever runs it."""
    e, tmp, out = env
    stub = tmp / "idbin"
    stub.mkdir()
    (stub / "id").write_text('#!/bin/sh\necho 987654\n')
    os.chmod(stub / "id", 0o755)
    e2 = {k: v for k, v in e.items() if k != "XDG_RUNTIME_DIR"}
    e2["PATH"] = "%s:%s" % (stub, e["PATH"])
    r = start(e2)
    assert r.returncode == 1, r.stderr
    assert "/run/user/987654" in r.stderr and "unset" not in r.stderr, r.stderr
    assert not out.exists()
    assert start(e).returncode == 0  # control: with the variable it starts
