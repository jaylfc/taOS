"""taos-app-launch runs swaymsg as taos without handing paths to a shell parse.

The socket path and the swaymsg command used to be pasted into su's -c string,
so a `;` in either ran as a second command under the taos user.

Run: python3 -m pytest -q tests/session/test_taos_app_launch.py
"""
import os
import pathlib
import re
import subprocess

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "session" / "bin" / "taos-app-launch"


def _function():
    text = SCRIPT.read_text()
    m = re.search(r"^swaymsg_as_taos\(\) \{\n.*?^\}\n", text, re.S | re.M)
    assert m, "swaymsg_as_taos() not found in taos-app-launch"
    return m.group(0)


def test_a_semicolon_in_the_socket_path_is_not_run(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "swaymsg.log"
    pwned = tmp_path / "pwned"
    # busybox su: su [-s SH] USER -c CMD [ARG0 ARGS...]; drop the user and -s.
    (bindir / "su").write_text('#!/bin/sh\nshift 3\nexec /bin/sh "$@"\n')
    (bindir / "swaymsg").write_text(
        '#!/bin/sh\nprintf "%%s|%%s|%%s\\n" "$XDG_RUNTIME_DIR" "$SWAYSOCK" "$*" >> %s\n' % log)
    for f in ("su", "swaymsg"):
        os.chmod(bindir / f, 0o755)
    sock = "/run/x/sway-ipc.1.sock;touch %s" % pwned
    script = "%s\nRUNDIR=/run/user/1\nSWAYSOCK='%s'\nswaymsg_as_taos 'workspace app-files'\n" % (
        _function(), sock)
    env = dict(os.environ, PATH="%s:%s" % (bindir, os.environ["PATH"]))
    subprocess.run(["/bin/sh", "-c", script], env=env, check=True, timeout=10)
    assert not pwned.exists(), "the socket path was parsed as shell"
    assert log.read_text().splitlines() == ["/run/user/1|%s|workspace app-files" % sock]
