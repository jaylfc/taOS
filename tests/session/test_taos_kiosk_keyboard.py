"""taos-kiosk-keyboard builds a themed private config and starts squeekboard.

Also checks the surrounding wiring: both chromium launchers carry the IME
flags, and the installer only pulls in squeekboard under --keyboard.

Run: python3 -m pytest -q tests/session/test_taos_kiosk_keyboard.py
"""
import os
import pathlib
import re
import shutil
import subprocess
import sys
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


def _default_bin_dirs(path):
    m = re.search(r'TAOS_KEYBOARD_BIN_DIRS:-([^}]*)\}', path.read_text())
    assert m, path
    return m.group(1)


def test_both_scripts_probe_the_same_default_squeekboard_dirs():
    a = _default_bin_dirs(WRAPPER)
    assert a == _default_bin_dirs(BIN / "taos-session")
    # Control: the extractor reads a real, non-empty list.
    assert a.split() and all(d.startswith("/") for d in a.split())


def test_installer_installs_the_wrapper_unconditionally():
    lines = (ROOT / "session" / "install-session.sh").read_text().splitlines()
    wrapper = [i for i, l in enumerate(lines)
               if re.match(r"install -m 755 .*bin/taos-kiosk-keyboard\b", l)]
    assert len(wrapper) == 1, "wrapper install is missing or indented inside a block"
    # Control: the squeekboard package install is still inside the guard.
    pkg = [i for i, l in enumerate(lines) if re.match(r"\s+pkg_install squeekboard\b", l)]
    assert len(pkg) == 1


def _spawn(e, extra=None):
    ee = {k: v for k, v in e.items() if k != "_extra"}
    return subprocess.Popen([str(WRAPPER)], env=ee)


def _alive_stubs(pids_file):
    alive = []
    for tok in pids_file.read_text().split():
        # A killed child of the test is a zombie until reaped: not alive.
        try:
            state = pathlib.Path("/proc/%s/stat" % tok).read_text().rsplit(")", 1)[1].split()[0]
        except FileNotFoundError:
            continue
        if state != "Z":
            alive.append(int(tok))
    return alive


def test_two_simultaneous_starts_leave_exactly_one_recorded_keyboard(env):
    e, tmp, out = env
    pids = tmp / "pids"
    (tmp / "sqbin" / "squeekboard").write_text(
        '#!/bin/sh\necho "$$" >> "%s"\nwhile :; do sleep 1; done\n' % pids)
    pidfile = pathlib.Path(e["XDG_RUNTIME_DIR"]) / "taos-keyboard.pid"
    trace = tmp / "trace"
    e = dict(e, TAOS_KEYBOARD_TRACE=str(trace))
    procs = [_spawn(e), _spawn(e)]
    try:
        for _ in range(100):
            if pids.exists() and len(pids.read_text().split()) >= 2:
                break
            time.sleep(0.1)
        # The second start stops the first before it execs, but allow for a slow
        # runner: poll rather than trust one fixed sleep.
        for _ in range(80):
            alive = _alive_stubs(pids)
            if len(alive) == 1:
                break
            time.sleep(0.1)

        def _describe(pid):
            try:
                comm = pathlib.Path("/proc/%d/comm" % pid).read_text().strip()
                stat = pathlib.Path("/proc/%d/stat" % pid).read_text().rsplit(")", 1)[1].split()[:3]
            except OSError as exc:
                return "%d gone (%s)" % (pid, type(exc).__name__)
            return "%d comm=%s state/ppid/pgrp=%s" % (pid, comm, stat)

        # The symlink lock is not used with flock: nothing stale is left behind.
        old_lock = pathlib.Path(e["XDG_RUNTIME_DIR"]) / "taos-keyboard.lock"
        why = "alive=%s | %s | pids file=%r | pidfile=%r | old-lock=%s" % (
            alive, "; ".join(_describe(p) for p in alive), pids.read_text(),
            pidfile.read_text() if pidfile.exists() else None,
            ("-> " + os.readlink(old_lock)) if os.path.islink(old_lock) else
            ("present" if old_lock.exists() else "absent"))
        why += "\nTRACE (pid uptime step):\n" + (trace.read_text() if trace.exists() else "(no trace file)")
        assert len(alive) == 1, why
        assert pidfile.read_text().strip() == str(alive[0]), why
        assert not os.path.lexists(old_lock)
    finally:
        for p in procs:
            p.kill()
        if pids.exists():
            for tok in pids.read_text().split():
                try:
                    os.kill(int(tok), 9)
                except ProcessLookupError:
                    pass


def test_a_keyboard_that_ignores_term_is_still_replaced(env):
    """Seen in CI: two keyboards alive after two starts. A process ignores TERM
    when its parent did (the disposition survives exec), and the stop step only
    ever sent TERM, so the old keyboard stayed up beside the new one."""
    e, tmp, out = env
    pids = tmp / "pids"
    (tmp / "sqbin" / "squeekboard").write_text(
        '#!/bin/sh\ntrap "" TERM\necho "$$" >> "%s"\nwhile :; do sleep 1; done\n' % pids)
    procs = [_spawn(e)]
    try:
        for _ in range(100):
            if pids.exists() and len(pids.read_text().split()) >= 1:
                break
            time.sleep(0.1)
        first = int(pids.read_text().split()[0])
        procs.append(_spawn(e))
        for _ in range(150):
            if len(pids.read_text().split()) >= 2:
                break
            time.sleep(0.1)
        time.sleep(0.5)
        alive = _alive_stubs(pids)
        assert first not in alive, "the TERM-ignoring keyboard is still running"
        assert len(alive) == 1, alive   # control: the new one did start
    finally:
        for p in procs:
            p.kill()
        if pids.exists():
            for tok in pids.read_text().split():
                try:
                    os.kill(int(tok), 9)
                except ProcessLookupError:
                    pass


def _holder(lock, env):
    """A single process that takes the flock on the lock file and keeps it for
    30 s. Python's fcntl.flock() is flock(2), the same lock util-linux flock
    uses, and a process that dies releases it automatically: nothing to
    reclaim."""
    holder = subprocess.Popen([sys.executable, "-c",
        "import fcntl, sys, time\n"
        "f = open(sys.argv[1], 'w')\n"
        "fcntl.flock(f.fileno(), fcntl.LOCK_EX)\n"
        "print('locked', flush=True)\n"
        "time.sleep(30)\n", str(lock)],
        env=env, stdout=subprocess.PIPE, text=True)
    line = holder.stdout.readline()
    assert line.strip() == "locked", "the holder did not take the lock: %r" % line
    return holder


def test_a_lock_held_by_a_live_process_is_never_taken_over(env):
    """A live holder of the flock is waited for, up to 30 s: the start must not
    enter the section while the holder is alive, and must not take the lock
    over on elapsed time alone."""
    e, tmp, out = env
    run = pathlib.Path(e["XDG_RUNTIME_DIR"])
    lock = run / "taos-keyboard.flock"
    e = {k: v for k, v in e.items() if k != "_extra"}
    holder = _holder(lock, e)
    try:
        p = subprocess.Popen([str(WRAPPER)], env=e, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
        try:
            time.sleep(7)   # the old symlink code took over after 5 s
            assert p.poll() is None, "the wrapper did not wait for the live holder"
            assert not out.exists(), "squeekboard was started while another start held the lock"
            assert os.path.exists(lock)
            # Control: once the holder is gone the waiting start goes through,
            # with nothing to reclaim (the kernel released the lock).
            holder.kill(); holder.wait()
            p.wait(timeout=15)
            assert p.returncode == 0 and out.exists()
        finally:
            if p.poll() is None:
                p.kill()
    finally:
        if holder.poll() is None:
            holder.kill()


def test_a_holder_killed_with_sigkill_does_not_block_the_next_start(env):
    """A holder killed with SIGKILL inside the locked section releases the
    lock through the kernel: the next start goes through at once, with no
    reclaim wait at all."""
    e, tmp, out = env
    run = pathlib.Path(e["XDG_RUNTIME_DIR"])
    lock = run / "taos-keyboard.flock"
    e = {k: v for k, v in e.items() if k != "_extra"}
    holder = _holder(lock, e)
    try:
        p = subprocess.Popen([str(WRAPPER)], env=e, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
        try:
            time.sleep(1)
            assert p.poll() is None, "the wrapper did not wait for the live holder"
            t0 = time.time()
            os.kill(holder.pid, 9)
            holder.wait()
            p.wait(timeout=15)
            took = time.time() - t0
            assert p.returncode == 0 and out.exists(), (p.returncode, p.stderr)
            assert took < 2, ("the start waited for a dead holder", took)
        finally:
            if p.poll() is None:
                p.kill()
    finally:
        if holder.poll() is None:
            holder.kill()


def test_the_started_keyboard_does_not_hold_fd_9(env):
    """The lock fd must be closed before exec, so squeekboard inherits
    nothing from it: no fd of the started keyboard may reference the lock
    file."""
    e, tmp, out = env
    pids = tmp / "pids"
    (tmp / "sqbin" / "squeekboard").write_text(
        '#!/bin/sh\necho "$$" >> "%s"\nwhile :; do sleep 1; done\n' % pids)
    proc = _spawn(e)
    try:
        for _ in range(100):
            if pids.exists() and pids.read_text().strip():
                break
            time.sleep(0.1)
        pid = int(pids.read_text().split()[-1])
        lock = pathlib.Path(e["XDG_RUNTIME_DIR"]) / "taos-keyboard.flock"
        fd_dir = pathlib.Path("/proc/%d/fd" % pid)
        targets = [os.readlink(fd_dir / name) for name in os.listdir(fd_dir)]
        assert str(lock) not in targets, "keyboard holds the lock fd: %r" % targets
    finally:
        proc.kill()
        proc.wait()
        if pids.exists():
            for tok in pids.read_text().split():
                try:
                    os.kill(int(tok), 9)
                except ProcessLookupError:
                    pass


def test_a_stale_lock_does_not_hang_the_wrapper(env):
    """A lock file left behind by a start that died inside the section is not
    stale state: there is nothing to reclaim, so a later start opens it and
    goes on at once."""
    e, tmp, out = env
    run = pathlib.Path(e["XDG_RUNTIME_DIR"])
    lock = run / "taos-keyboard.flock"
    lock.write_text("stale\n")
    t0 = time.time()
    r = start(e)
    assert r.returncode == 0, r.stderr
    assert time.time() - t0 < 6
    assert out.exists()
    # Control: with no lock file at all it starts at once.
    lock.unlink()
    t0 = time.time()
    assert start(e).returncode == 0 and out.exists()
    assert time.time() - t0 < 4


def test_a_lock_that_cannot_be_created_gives_up_instead_of_spinning(env):
    """If the lock file cannot be created at all (runtime dir read-only or
    full), there is no owner to wait for: give up at once instead of spinning
    on a lock nobody can ever take."""
    e, tmp, out = env
    run = pathlib.Path(e["XDG_RUNTIME_DIR"])
    os.chmod(run, 0o555)
    try:
        if os.access(run, os.W_OK):
            pytest.skip("this runner can write a 0555 directory")
        e2 = {k: v for k, v in e.items() if k != "_extra"}
        t0 = time.time()
        try:
            r = subprocess.run([str(WRAPPER)], env=e2, capture_output=True,
                               text=True, timeout=12)
        except subprocess.TimeoutExpired:
            pytest.fail("the wrapper never gave up: it is spinning on a lock it cannot create")
        took = time.time() - t0
        assert r.returncode == 1, (r.returncode, r.stderr)
        assert "cannot create" in r.stderr and "taos-keyboard.flock" in r.stderr, r.stderr
        assert took < 8, took     # it gave up (did not spin), and it stopped
        assert not out.exists()
    finally:
        os.chmod(run, 0o755)
    assert start(e).returncode == 0 and out.exists()  # control: a writable dir starts


def test_without_flock_uses_the_symlink_lock_fallback(env):
    e, tmp, out = env
    flock_bin = shutil.which("flock")
    assert flock_bin, "flock must exist on PATH for this test to be meaningful"
    flock_real = os.path.realpath(os.path.dirname(flock_bin))
    stub = tmp / "stub"
    stub.mkdir()
    for name in ("ln", "rm", "readlink", "sleep", "dirname", "sed", "cat", "mv", "mkdir"):
        (stub / name).write_text('#!/bin/sh\nexec /usr/bin/%s "$@"\n' % name)
        os.chmod(stub / name, 0o755)
    # Dropping the directory that holds flock also drops python3 when they
    # share it (/usr/bin on this runner), and resolve-theme reads theme.json
    # with python3. Shim it from its resolved path so the test removes flock
    # and nothing else, on any runner.
    (stub / "python3").write_text('#!/bin/sh\nexec "%s" "$@"\n' % shutil.which("python3"))
    os.chmod(stub / "python3", 0o755)
    keep = [d for d in os.environ["PATH"].split(":")
            if d and os.path.realpath(d) != flock_real]
    e2 = dict(e)
    e2["PATH"] = "%s:%s" % (stub, ":".join(keep))
    # Sanity: flock really is undiscoverable in this environment.
    check = subprocess.run(["/bin/sh", "-c", "command -v flock >/dev/null && echo found || echo missing"],
                           env=e2, capture_output=True, text=True)
    assert check.stdout.strip() == "missing", check.stdout

    pids = tmp / "pids"
    (tmp / "sqbin" / "squeekboard").write_text(
        '#!/bin/sh\necho "$$" >> "%s"\nwhile :; do sleep 1; done\n' % pids)
    pidfile = pathlib.Path(e["XDG_RUNTIME_DIR"]) / "taos-keyboard.pid"
    lock = pathlib.Path(e["XDG_RUNTIME_DIR"]) / "taos-keyboard.lock"
    ee = {k: v for k, v in e2.items() if k != "_extra"}
    procs = [subprocess.Popen([str(WRAPPER)], env=ee),
             subprocess.Popen([str(WRAPPER)], env=ee)]
    try:
        for _ in range(100):
            if pids.exists() and len(pids.read_text().split()) >= 2:
                break
            time.sleep(0.1)
        for _ in range(80):
            alive = _alive_stubs(pids)
            if len(alive) == 1:
                break
            time.sleep(0.1)
        assert len(alive) == 1, alive
        assert pidfile.read_text().strip() == str(alive[0])
        assert not os.path.lexists(lock)
    finally:
        for p in procs:
            p.kill()
        if pids.exists():
            for tok in pids.read_text().split():
                try:
                    os.kill(int(tok), 9)
                except ProcessLookupError:
                    pass
    # A dead owner's symlink lock is taken over at once by the fallback path.
    # A pid that never existed can never be a live holder: the fallback must
    # not wait for it (that was the check-then-remove race the flock path
    # removes).
    (tmp / "sqbin" / "squeekboard").write_text(
        '#!/bin/sh\necho "$$" >> "%s"\nwhile :; do sleep 1; done\n' % pids)
    (tmp / "sqbin" / "squeekboard").write_text(
        '#!/bin/sh\n{ echo "CFG=$XDG_CONFIG_HOME"; '
        'cat "$XDG_CONFIG_HOME/gtk-3.0/gtk.css"; } > "%s"\n' % out)
    os.chmod(tmp / "sqbin" / "squeekboard", 0o755)
    os.symlink("1234567", lock)
    t0 = time.time()
    r = start(e2)
    assert r.returncode == 0, r.stderr
    assert time.time() - t0 < 6
    assert out.exists() and not os.path.lexists(lock)


def _busybox_flock_stub(tmp, real_flock):
    """A PATH stub for a BusyBox-style flock: no -w (rejected with a usage
    line), everything else (the -n form the wrapper uses) is handed to the real
    flock, so -n locks a real lock file against the inherited fd 9. Only the
    option arguments that come before the fd are examined (that is the whole
    option position in `flock [-sxun] FD`), so a non-option argument can never
    be read as -w, and a value attached to the flag (-w30) is rejected too."""
    stub = tmp / "flockstub"
    stub.mkdir()
    (stub / "flock").write_text(
        '#!/bin/sh\n'
        'for _a in "$@"; do\n'
        '    case "$_a" in\n'
        '        --) break ;;\n'
        '        -[a-z]*)\n'
        '            case "$_a" in\n'
        '                -w*)\n'
        '                    echo "Usage: flock [-sxun] fd | { file [-c] cmd ... }" >&2\n'
        '                    exit 1 ;;\n'
        '            esac ;;\n'
        '        *) break ;;\n'
        '    esac\n'
        'done\n'
        'exec "%s" "$@"\n' % real_flock)
    os.chmod(stub / "flock", 0o755)
    return str(stub)


def test_the_busybox_flock_stub_rejects_w_and_accepts_n(tmp_path):
    """Control for the stub above: it rejects -w in both forms and passes the
    -n form through to a real flock, so the tests that use it cannot pass on a
    stub that simply succeeds."""
    real = _real_flock_or_skip()
    stub = _busybox_flock_stub(tmp_path, real)
    env = dict(os.environ, PATH="%s:%s" % (stub, os.environ["PATH"]))
    lock = tmp_path / "lock"
    lock.write_text("")

    def run(*args):
        # fd 9 is the lock file, as the wrapper opens it.
        return subprocess.run(["/bin/sh", "-c", 'exec 9>"%s"; flock %s 9' % (lock, " ".join(args))],
                              env=env, capture_output=True, text=True)

    for rejected in (["-w", "30"], ["-w30"]):
        r = run(*rejected)
        assert r.returncode == 1, (rejected, r.returncode, r.stderr)
        assert "Usage:" in r.stderr, (rejected, r.stderr)
    assert run("-n").returncode == 0
    # Control: a flag the stub must not reject (-s, in BusyBox usage) is passed
    # through to the real flock.
    assert run("-s").returncode == 0


def _real_flock_or_skip():
    real = shutil.which("flock")
    if not real:
        pytest.skip("no real flock on PATH to delegate -n to")
    return real


def test_a_busybox_flock_without_w_still_acquires_the_lock(env):
    """The handset runs BusyBox, whose flock has no -w. A flock that rejects -w
    but implements -n against a real lock file must not stop the wrapper: it
    takes the lock with the non-blocking retry loop and starts the keyboard."""
    e, tmp, out = env
    real = _real_flock_or_skip()
    stub = _busybox_flock_stub(tmp, real)
    e2 = dict(e, PATH="%s:%s" % (stub, e["PATH"]))
    # Control: the stub really rejects -w, which is why the old `-w 30` call
    # made the wrapper print "gave up" and exit 1 on BusyBox.
    check = subprocess.run(["/bin/sh", "-c", "flock -w 30 9 2>/dev/null; echo rc=$?"],
                           env={k: v for k, v in e2.items() if k != "_extra"},
                           capture_output=True, text=True)
    assert "rc=1" in check.stdout, check.stdout
    r = start(e2)
    assert r.returncode == 0, r.stderr
    assert out.exists(), "the keyboard never started under a flock without -w"


def test_two_wrappers_under_a_busybox_flock_start_exactly_one_keyboard(env):
    """With a BusyBox-style flock (no -w) two wrappers started together still
    serialize on the -n retry loop: the second waits for the first, then finds
    it running and replaces it, so exactly one keyboard process is left."""
    e, tmp, out = env
    real = _real_flock_or_skip()
    stub = _busybox_flock_stub(tmp, real)
    pids = tmp / "pids"
    (tmp / "sqbin" / "squeekboard").write_text(
        '#!/bin/sh\necho "$$" >> "%s"\nwhile :; do sleep 1; done\n' % pids)
    pidfile = pathlib.Path(e["XDG_RUNTIME_DIR"]) / "taos-keyboard.pid"
    ee = {k: v for k, v in e.items() if k != "_extra"}
    ee["PATH"] = "%s:%s" % (stub, e["PATH"])
    procs = [subprocess.Popen([str(WRAPPER)], env=ee),
             subprocess.Popen([str(WRAPPER)], env=ee)]
    try:
        for _ in range(100):
            if pids.exists() and len(pids.read_text().split()) >= 1:
                break
            time.sleep(0.1)
        alive = []
        for _ in range(120):
            alive = _alive_stubs(pids)
            if len(alive) == 1:
                break
            time.sleep(0.1)
        assert len(alive) == 1, alive
        assert pidfile.read_text().strip() == str(alive[0])
    finally:
        for p in procs:
            p.kill()
        if pids.exists():
            for tok in pids.read_text().split():
                try:
                    os.kill(int(tok), 9)
                except ProcessLookupError:
                    pass


def test_a_lock_held_forever_makes_the_wrapper_give_up_after_bounded_tries(env):
    """A lock held forever must not hang the wrapper: after a bounded number of
    non-blocking tries (shortened here with TAOS_KEYBOARD_LOCK_TRIES) it gives
    up with a clear message and a non-zero exit, without starting the keyboard.
    This runs on the real flock (util-linux and BusyBox share the -n form)."""
    e, tmp, out = env
    run = pathlib.Path(e["XDG_RUNTIME_DIR"])
    lock = run / "taos-keyboard.flock"
    e = {k: v for k, v in e.items() if k != "_extra"}
    holder = _holder(lock, e)
    try:
        ee = dict(e, TAOS_KEYBOARD_LOCK_TRIES="3")
        t0 = time.time()
        try:
            r = subprocess.run([str(WRAPPER)], env=ee, capture_output=True,
                               text=True, timeout=30)
        except subprocess.TimeoutExpired:
            pytest.fail("the wrapper never gave up: it hangs on a lock held forever")
        took = time.time() - t0
        assert r.returncode != 0, (r.returncode, r.stderr)
        assert "giving up" in r.stderr and str(lock) in r.stderr, r.stderr
        assert took < 15, took
        assert not out.exists()
    finally:
        holder.kill()
        holder.wait()


def test_a_requested_try_count_survives_the_whole_second_sleep_fallback(env):
    """TAOS_KEYBOARD_LOCK_TRIES is the hook that shortens the wait, and the
    whole-second sleep fallback must not undo it. When this shell's sleep rejects
    a fraction the wrapper switches its step from 0.1 s to 1 s; only the step may
    change there. A loop that also rewrote the try count would wait 10 times the
    requested bound again, so the hook would shorten nothing on exactly the
    systems that need it (BusyBox)."""
    e, tmp, out = env
    run = pathlib.Path(e["XDG_RUNTIME_DIR"])
    lock = run / "taos-keyboard.flock"
    sleepstub = tmp / "sleepstub"
    sleepstub.mkdir()
    (sleepstub / "sleep").write_text(
        '#!/bin/sh\ncase "$1" in *.*) exit 1 ;; esac\nexec "%s" "$@"\n' % shutil.which("sleep"))
    os.chmod(sleepstub / "sleep", 0o755)
    e = {k: v for k, v in e.items() if k != "_extra"}
    e["PATH"] = "%s:%s" % (sleepstub, e["PATH"])
    # Control: the stub really does reject a fractional sleep, which is what
    # puts the wrapper on its 1 s step.
    check = subprocess.run(["/bin/sh", "-c", "sleep 0.1; echo rc=$?"], env=e,
                           capture_output=True, text=True)
    assert "rc=1" in check.stdout, check.stdout
    holder = _holder(lock, e)
    try:
        ee = dict(e, TAOS_KEYBOARD_LOCK_TRIES="3")
        t0 = time.time()
        try:
            r = subprocess.run([str(WRAPPER)], env=ee, capture_output=True,
                               text=True, timeout=40)
        except subprocess.TimeoutExpired:
            pytest.fail("the wrapper did not honour TAOS_KEYBOARD_LOCK_TRIES on the 1 s step")
        took = time.time() - t0
        # First the bound: a loop that rewrote the count waits the full 30 tries
        # here and then takes the lock when the 30 s holder goes away.
        assert took < 15, ("the 1 s fallback rewrote the requested try count", took)
        assert r.returncode != 0, (r.returncode, r.stderr)
        assert "giving up" in r.stderr and str(lock) in r.stderr, r.stderr
        assert not out.exists()
    finally:
        holder.kill()
        holder.wait()
