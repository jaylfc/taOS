"""taos-session: theme lookup, whole-or-nothing apply, and the keyboard opt-in.

A theme package that lists a variant but lacks one of its files must never be
half applied: the fallback (taos-default dark) is applied whole, the state file
records that, and the exit status says so.

Run: python3 -m pytest -q tests/session/test_taos_session_theme.py
"""
import json
import os
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "session" / "bin" / "taos-session"
DEFAULT_PKG = ROOT / "session" / "themes"


def make_theme(base, tid, variants=("dark", "light"), skip=(), listed=None):
    """Write a theme package under base/tid; `skip` names files to leave out."""
    d = pathlib.Path(base) / tid
    (d / "keyboard").mkdir(parents=True)
    kb = {"metrics": "keyboard/metrics.css"}
    for v in variants:
        kb[v] = "keyboard/%s.css" % v
    meta = {"id": tid, "name": tid, "variants": list(listed or variants), "keyboard": kb}
    (d / "theme.json").write_text(json.dumps(meta))
    for name in ["metrics"] + list(variants):
        if name not in skip:
            (d / "keyboard" / (name + ".css")).write_text("/* %s %s */\n" % (tid, name))
    return d


@pytest.fixture()
def env(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    store = tmp_path / "gs-store"
    store.mkdir()
    log = tmp_path / "gs.log"
    # Stub gsettings: set writes a file per key unless GS_MEMORY is set (the
    # "memory backend" case, where the write never reaches a later reader).
    (bindir / "gsettings").write_text(
        '#!/bin/sh\n'
        'echo "$*" >> "%s"\n'
        'case "$1" in\n'
        '  set) [ -n "${GS_MEMORY:-}" ] || printf "%%s\\n" "$4" > "%s/$3" ;;\n'
        '  get) cat "%s/$3" 2>/dev/null || echo false ;;\n'
        'esac\n' % (log, store, store))
    os.chmod(bindir / "gsettings", 0o755)
    # A keyboard wrapper and a squeekboard binary that `keyboard on` can find.
    wrapper = tmp_path / "taos-kiosk-keyboard"
    wrapper.write_text("#!/bin/sh\nexit 0\n")
    os.chmod(wrapper, 0o755)
    sqdir = tmp_path / "sqbin"
    sqdir.mkdir()
    (sqdir / "squeekboard").write_text("#!/bin/sh\nexit 0\n")
    os.chmod(sqdir / "squeekboard", 0o755)
    pkgs = tmp_path / "pkgs"
    pkgs.mkdir()
    # The real default package, copied so a test can break its copy.
    subprocess.run(["cp", "-R", str(DEFAULT_PKG / "taos-default"), str(pkgs)], check=True)
    e = dict(os.environ,
             PATH="%s:%s" % (bindir, os.environ["PATH"]),
             TAOS_THEME_PATH=str(pkgs),
             TAOS_SESSION_STATE_DIR=str(tmp_path / "state"),
             TAOS_SWAY_DROPIN_DIR=str(tmp_path / "sway.d"),
             TAOS_KIOSK_KEYBOARD=str(wrapper),
             TAOS_KEYBOARD_BIN_DIRS=str(sqdir),
             XDG_RUNTIME_DIR=str(tmp_path / "run"))
    e.pop("GS_MEMORY", None)
    e.update(pkgs=pkgs, tmp=tmp_path, gslog=log)
    return e


def run(env, *args, **extra):
    e = {k: v for k, v in env.items() if isinstance(v, str)}
    e.update(extra)
    return subprocess.run([str(SCRIPT), *args], env=e, capture_output=True, text=True, timeout=20)


def state(env):
    p = env["tmp"] / "state" / "theme"
    return p.read_text() if p.exists() else None


@pytest.mark.parametrize("variant", ["dark", "light"])
def test_valid_theme_resolves_and_persists(env, variant):
    r = run(env, "theme", "taos-default", variant)
    assert r.returncode == 0, r.stderr
    assert state(env) == "taos-default %s\n" % variant
    assert "prefer-%s" % variant in env["gslog"].read_text()
    out = run(env, "resolve-theme").stdout.splitlines()
    assert out[0] == "taos-default " + variant
    assert out[1].endswith("keyboard/%s.css" % variant)
    assert out[2].endswith("keyboard/metrics.css")


def test_missing_variant_css_falls_back_whole(env):
    make_theme(env["pkgs"], "half", skip=("light",))
    r = run(env, "theme", "half", "light")
    assert r.returncode == 3
    assert "light.css" in r.stderr and "half" in r.stderr
    assert state(env) == "taos-default dark\n"
    assert "prefer-dark" in env["gslog"].read_text()
    assert "prefer-light" not in env["gslog"].read_text()


def test_missing_metrics_css_falls_back_whole(env):
    make_theme(env["pkgs"], "nometrics", skip=("metrics",))
    r = run(env, "theme", "nometrics", "dark")
    assert r.returncode == 3
    assert "metrics.css" in r.stderr
    assert state(env) == "taos-default dark\n"


def test_unlisted_variant_and_broken_json_fall_back(env):
    make_theme(env["pkgs"], "darkonly", variants=("dark",))
    r = run(env, "theme", "darkonly", "light")
    assert r.returncode == 3 and "not listed" in r.stderr
    bad = make_theme(env["pkgs"], "broken")
    (bad / "theme.json").write_text("{not json")
    r = run(env, "theme", "broken", "dark")
    assert r.returncode == 3 and "invalid" in r.stderr
    assert state(env) == "taos-default dark\n"


def test_complete_package_is_applied_not_rejected(env):
    # Control for the three refusals above: the same helper, nothing removed.
    make_theme(env["pkgs"], "whole")
    r = run(env, "theme", "whole", "light")
    assert r.returncode == 0, r.stderr
    assert state(env) == "whole light\n"


def test_invalid_id_is_rejected_and_never_looked_up(env):
    canary = env["tmp"] / "etc"
    (canary / "keyboard").mkdir(parents=True)
    for n in ("theme.json", "keyboard/metrics.css", "keyboard/dark.css"):
        (canary / n).write_text(json.dumps({"variants": ["dark"], "keyboard": {
            "metrics": "keyboard/metrics.css", "dark": "keyboard/dark.css"}}) if n == "theme.json" else "x")
    sub = env["pkgs"] / "a"
    sub.mkdir()
    for bad in ("../etc", "../../etc", "A", "-x", "a/b", ""):
        r = run(env, "theme", bad, "dark")
        assert r.returncode == 3, (bad, r.stderr)
        assert "invalid theme id" in r.stderr
        assert state(env) == "taos-default dark\n"


def test_user_theme_dir_earlier_in_path_wins(env):
    user = env["tmp"] / "user-themes"
    make_theme(user, "taos-default")
    r = run(env, "theme", "taos-default", "dark",
            TAOS_THEME_PATH="%s:%s" % (user, env["pkgs"]))
    assert r.returncode == 0
    out = run(env, "resolve-theme", TAOS_THEME_PATH="%s:%s" % (user, env["pkgs"])).stdout
    assert str(user) in out.splitlines()[1]
    # Control: reversed order picks the system copy.
    out = run(env, "resolve-theme", TAOS_THEME_PATH="%s:%s" % (env["pkgs"], user)).stdout
    assert str(env["pkgs"]) in out.splitlines()[1]


def test_broken_fallback_is_a_hard_failure(env):
    (env["pkgs"] / "taos-default" / "keyboard" / "dark.css").unlink()
    r = run(env, "theme", "nothing-here", "dark")
    assert r.returncode == 1
    assert state(env) is None


def test_usage_errors_exit_2(env):
    for args in ([], ["bogus"], ["theme"], ["theme", "a"], ["keyboard"], ["keyboard", "maybe"],
                 ["resolve-theme", "a"]):
        r = run(env, *args)
        assert r.returncode == 2, (args, r.stderr)
        assert "usage" in r.stderr
    # Control: a well-formed call is not a usage error.
    assert run(env, "theme", "taos-default", "dark").returncode == 0


def test_keyboard_on_writes_dropin_and_off_removes_it(env):
    dropin = env["tmp"] / "sway.d" / "50-keyboard.conf"
    r = run(env, "keyboard", "on")
    assert r.returncode == 0, r.stderr
    assert dropin.read_text() == "exec_always /usr/local/bin/taos-kiosk-keyboard\n"
    r = run(env, "keyboard", "off")
    assert r.returncode == 0, r.stderr
    assert not dropin.exists()
    assert "screen-keyboard-enabled true" in env["gslog"].read_text()
    assert "screen-keyboard-enabled false" in env["gslog"].read_text()


def test_keyboard_on_fails_when_the_readback_does_not_match(env):
    r = run(env, "keyboard", "on", GS_MEMORY="1")
    assert r.returncode == 1
    assert "reads back" in r.stderr
    assert not (env["tmp"] / "sway.d" / "50-keyboard.conf").exists()


def test_theme_restarts_the_keyboard_only_when_opted_in(env):
    marker = env["tmp"] / "restarted"
    kb = env["tmp"] / "fake-keyboard"
    kb.write_text('#!/bin/sh\necho "$*" > "%s"\n' % marker)
    os.chmod(kb, 0o755)
    run(env, "theme", "taos-default", "dark", TAOS_KIOSK_KEYBOARD=str(kb))
    assert not marker.exists()
    assert run(env, "keyboard", "on").returncode == 0
    run(env, "theme", "taos-default", "light", TAOS_KIOSK_KEYBOARD=str(kb))
    assert marker.read_text() == "--restart\n"


def _as_root(env):
    """Stubs that make the script take its root path: `id -u` says 0, and `su`
    parses its arguments the way busybox su does (options are permuted, so an
    argument starting with `-` anywhere before a `--` is read as su's own)."""
    bindir = env["tmp"] / "bin"
    (bindir / "id").write_text(
        '#!/bin/sh\n'
        'case "$*" in\n'
        '  "-u") echo 0 ;;\n'
        '  "-u taos") echo 4242 ;;\n'
        '  taos) echo "uid=4242(taos)" ;;\n'
        '  *) exit 1 ;;\n'
        'esac\n')
    (bindir / "su").write_text(
        '#!/bin/sh\n'
        '# getopt permutes: every argument before a `--` is scanned for options,\n'
        '# wherever it sits, which is what makes a trailing --flag fatal.\n'
        'skip=0\n'
        'for a in "$@"; do\n'
        '  if [ "$skip" = 1 ]; then skip=0; continue; fi\n'
        '  case "$a" in\n'
        '    --) break ;;\n'
        '    -s|-c) skip=1 ;;\n'
        '    -*) echo "su: unrecognized option: ${a#--}" >&2; exit 1 ;;\n'
        '  esac\n'
        'done\n'
        'cmd=""; user=""; ended=0\n'
        'while [ "$#" -gt 0 ]; do\n'
        '  if [ "$ended" = 0 ]; then\n'
        '    case "$1" in\n'
        '      --) ended=1; shift; continue ;;\n'
        '      -s) shift 2; continue ;;\n'
        '      -c) cmd=$2; shift 2; continue ;;\n'
        '      -*) echo "su: unrecognized option: ${1#--}" >&2; exit 1 ;;\n'
        '    esac\n'
        '  fi\n'
        '  if [ -z "$user" ]; then user=$1; shift; continue; fi\n'
        '  if [ -z "$cmd" ] && [ "$1" = -c ]; then cmd=$2; shift 2; break; fi\n'
        '  break\n'
        'done\n'
        '[ "$user" = taos ] || { echo "su: wrong user $user" >&2; exit 1; }\n'
        'exec /bin/sh -c "$cmd" "$@"\n')
    # Log every su call so a test can see what went through it.
    su = (bindir / "su").read_text().replace(
        "#!/bin/sh\n", '#!/bin/sh\necho "$*" >> "%s"\n' % (env["tmp"] / "su.log"), 1)
    (bindir / "su").write_text(su)
    for n in ("id", "su"):
        os.chmod(bindir / n, 0o755)
    # As root the script does not create the state dir; the session user owns it.
    (env["tmp"] / "state").mkdir(exist_ok=True)


def test_as_root_the_restart_flag_reaches_the_keyboard_not_su(env):
    """Measured on the handset: `su taos -s /bin/sh -c CMD arg0 ... --restart`
    died with "su: unrecognized option: restart" and the keyboard kept the old
    theme while the command still exited 0."""
    _as_root(env)
    marker = env["tmp"] / "restarted"
    kb = env["tmp"] / "fake-keyboard"
    kb.write_text('#!/bin/sh\necho "$*" > "%s"\n' % marker)
    os.chmod(kb, 0o755)
    assert run(env, "keyboard", "on").returncode == 0   # control: the su path works at all
    r = run(env, "theme", "taos-default", "light", TAOS_KIOSK_KEYBOARD=str(kb))
    assert "unrecognized option" not in r.stderr, r.stderr
    assert "keyboard restart failed" not in r.stderr, r.stderr
    assert marker.exists() and marker.read_text() == "--restart\n"


def test_a_failed_keyboard_restart_is_never_exit_0(env):
    """Measured on the handset: called as the session user from outside the
    session, the restart failed, the state file said light, the keyboard stayed
    dark, and the command exited 0."""
    kb = env["tmp"] / "fake-keyboard"
    kb.write_text('#!/bin/sh\nexit 1\n')
    os.chmod(kb, 0o755)
    assert run(env, "keyboard", "on").returncode == 0
    r = run(env, "theme", "taos-default", "light", TAOS_KIOSK_KEYBOARD=str(kb))
    assert r.returncode == 4, (r.returncode, r.stderr)
    assert "restart FAILED" in r.stderr
    # Control: the same call with a keyboard that restarts is exit 0.
    kb.write_text('#!/bin/sh\nexit 0\n')
    assert run(env, "theme", "taos-default", "light", TAOS_KIOSK_KEYBOARD=str(kb)).returncode == 0


def test_keyboard_on_off_says_it_needs_root_when_it_cannot_write(env):
    ro = env["tmp"] / "ro"
    ro.mkdir()
    os.chmod(ro, 0o555)
    try:
        r = run(env, "keyboard", "on", TAOS_SWAY_DROPIN_DIR=str(ro / "sway.d"))
        assert r.returncode == 1 and "must run as root" in r.stderr, r.stderr
    finally:
        os.chmod(ro, 0o755)
    assert run(env, "keyboard", "on").returncode == 0  # control: a writable dir works


def test_theme_json_keyboard_path_with_a_newline_is_rejected(env):
    """The validated paths come back line by line: a newline in one smuggled a
    second path past the ".." check."""
    d = make_theme(env["pkgs"], "smuggle")
    (env["pkgs"] / "canary.css").write_text("/* canary */\n")
    meta = json.loads((d / "theme.json").read_text())
    meta["keyboard"]["metrics"] = "keyboard/metrics.css\n../canary.css"
    (d / "theme.json").write_text(json.dumps(meta))
    r = run(env, "resolve-theme", "smuggle", "dark")
    assert r.returncode == 3, r.stderr
    assert "unsafe keyboard path" in r.stderr
    assert r.stdout.splitlines()[0] == "taos-default dark"
    assert "canary" not in r.stdout
    # Control: the same package with a clean path is applied.
    meta["keyboard"]["metrics"] = "keyboard/metrics.css"
    (d / "theme.json").write_text(json.dumps(meta))
    r = run(env, "resolve-theme", "smuggle", "dark")
    assert r.returncode == 0 and r.stdout.splitlines()[0] == "smuggle dark", r.stderr


def test_a_css_path_naming_a_directory_is_rejected(env):
    d = make_theme(env["pkgs"], "dirvariant", skip=("light",))
    (d / "keyboard" / "light.css").mkdir()
    r = run(env, "resolve-theme", "dirvariant", "light")
    assert r.returncode == 3, r.stderr
    assert "light.css" in r.stderr
    assert r.stdout.splitlines()[0] == "taos-default dark"
    # Control: the dark variant of the same package is a regular file.
    assert run(env, "resolve-theme", "dirvariant", "dark").returncode == 0


def test_as_root_the_state_file_is_written_through_su(env):
    """Root must not write into the session-user-owned state dir itself: the
    write and the rename go through su. This cannot prove file ownership (the
    stub su runs as the test user); it proves the route."""
    _as_root(env)
    sulog = env["tmp"] / "su.log"
    r = run(env, "theme", "taos-default", "light")
    assert r.returncode == 0, r.stderr
    assert state(env) == "taos-default light\n"
    hits = [l for l in sulog.read_text().splitlines()
            if str(env["tmp"] / "state") in l and "taos-default light" in l]
    assert hits, sulog.read_text()
    # Control: a non-root run writes the same file without any su call.
    sulog.unlink()
    (env["tmp"] / "bin" / "id").unlink()
    (env["tmp"] / "bin" / "su").unlink()
    r = run(env, "theme", "taos-default", "dark")
    assert r.returncode == 0 and state(env) == "taos-default dark\n"
    assert not sulog.exists()


def test_as_root_a_missing_state_dir_fails_before_the_keyboard_is_touched(env):
    _as_root(env)
    marker = env["tmp"] / "restarted"
    kb = env["tmp"] / "fake-keyboard"
    kb.write_text('#!/bin/sh\necho "$*" > "%s"\n' % marker)
    os.chmod(kb, 0o755)
    assert run(env, "keyboard", "on").returncode == 0
    (env["tmp"] / "state").rmdir()
    r = run(env, "theme", "taos-default", "light", TAOS_KIOSK_KEYBOARD=str(kb))
    assert r.returncode == 1 and "state dir" in r.stderr, r.stderr
    assert not marker.exists() and state(env) is None
    # Control: with the dir present the same call succeeds and restarts.
    (env["tmp"] / "state").mkdir()
    r = run(env, "theme", "taos-default", "light", TAOS_KIOSK_KEYBOARD=str(kb))
    assert r.returncode == 0, r.stderr
    assert marker.exists()


def test_keyboard_on_refuses_when_the_wrapper_is_missing(env):
    dropin = env["tmp"] / "sway.d" / "50-keyboard.conf"
    r = run(env, "keyboard", "on", TAOS_KIOSK_KEYBOARD=str(env["tmp"] / "absent"))
    assert r.returncode == 1 and "--keyboard" in r.stderr and "wrapper" in r.stderr, r.stderr
    assert not dropin.exists()
    assert not env["gslog"].exists()
    # Control: with the wrapper present it succeeds.
    assert run(env, "keyboard", "on").returncode == 0 and dropin.exists()


def test_keyboard_on_refuses_when_no_squeekboard_exists(env):
    dropin = env["tmp"] / "sway.d" / "50-keyboard.conf"
    empty = env["tmp"] / "empty"
    empty.mkdir()
    r = run(env, "keyboard", "on", TAOS_KEYBOARD_BIN_DIRS=str(empty))
    assert r.returncode == 1 and "squeekboard" in r.stderr and "--keyboard" in r.stderr, r.stderr
    assert not dropin.exists()
    assert not env["gslog"].exists()
    # `off` needs neither.
    assert run(env, "keyboard", "off", TAOS_KEYBOARD_BIN_DIRS=str(empty),
               TAOS_KIOSK_KEYBOARD=str(env["tmp"] / "absent")).returncode == 0
    # Control: with a squeekboard in the list it succeeds.
    assert run(env, "keyboard", "on").returncode == 0 and dropin.exists()
