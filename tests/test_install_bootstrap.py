"""The README one-liner must work on images that ship no bash.

Reported by an end-user tester on postmarketOS (Alpine base), 2026-09-15:
`curl -fsSL .../install-server.sh | sudo bash` dies at
`sudo: 'bash': command not found`. The interpreter is chosen by the PIPE, so
the script's own `#!/usr/bin/env bash` shebang never gets a say and its Alpine
apk branch -- which would have installed the dependencies -- is unreachable by
the documented command.

Also: logind KillUserProcesses=yes + Linger=no kills the installer silently
mid-pip when the ssh session drops. The installer must detect this hazard,
warn before the long pip step, offer TAOS_SYSTEMD_RUN=1, and write an
explicit INSTALL_RESULT marker as the last line of the log.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "install-server.sh"
README = REPO / "README.md"


def _script() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _preamble() -> str:
    """Everything before `set -euo pipefail`, which is the first bash-only line."""
    text = _script()
    idx = text.index("\nset -euo pipefail")
    return text[:idx]


def _extract_fn(text: str, name: str) -> str:
    """Extract a bash function by name from script text, respecting nested braces."""
    start_marker = f"{name}() {{"
    start = text.index(start_marker)
    body_start = start + len(start_marker)
    depth = 1
    i = body_start
    while i < len(text) and depth > 0:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
        i += 1
    return text[start:i]


def _make_fake_bin(tmpdir: Path, **overrides: str) -> Path:
    """Create a temp bin dir with fake commands that mimic the measured hazard shape."""
    bindir = tmpdir / "bin"
    bindir.mkdir()

    def _write(name: str, content: str) -> None:
        p = bindir / name
        p.write_text(content, encoding="utf-8")
        p.chmod(0o755)

    real_bash = shutil.which("bash")
    if real_bash:
        try:
            (bindir / "bash").symlink_to(real_bash)
        except FileExistsError:
            pass

    real_cat = shutil.which("cat") or "/bin/cat"
    _write("cat", f"""#!/bin/sh
for arg in "$@"; do
    if [ "$arg" = "/proc/1/comm" ]; then
        echo "{overrides.get('systemd_pid1', 'systemd')}"
        exit 0
    fi
done
exec "{real_cat}" "$@"
""")

    _write("awk", """#!/bin/sh
exec /usr/bin/awk "$@"
""")

    _write("grep", """#!/bin/sh
exec /usr/bin/grep "$@"
""")

    _write("id", f"""#!/bin/sh
while [ $# -gt 0 ]; do
    case "$1" in
        -u) echo "{overrides.get('uid', '0')}"; exit 0 ;;
        -un) echo "{overrides.get('username', 'testuser')}"; exit 0 ;;
    esac
    shift
done
exec /usr/bin/id "$@"
""")

    _write("busctl", f"""#!/bin/sh
if [ "$1" = "get-property" ] && [ "$2" = "org.freedesktop.login1" ] && [ "$3" = "/org/freedesktop/login1" ] && [ "$4" = "org.freedesktop.login1.Manager" ] && [ "$5" = "KillUserProcesses" ]; then
    echo "{overrides.get('killuserprocesses', 'b true')}"
    exit 0
fi
exec /usr/bin/busctl "$@"
""")

    _write("loginctl", f"""#!/bin/sh
if [ "$1" = "show-user" ] && [ "$2" = "{overrides.get('username', 'testuser')}" ] && [ "$3" = "-p" ] && [ "$4" = "Linger" ]; then
    echo "Linger={overrides.get('linger', 'no')}"
    exit 0
elif [ "$1" = "list-sessions" ] && [ "$2" = "--no-legend" ]; then
    if [ "{overrides.get('has_session', 'yes')}" = "yes" ]; then
        echo "c143 {overrides.get('uid', '1000')} {overrides.get('username', 'testuser')} seat0 tty1"
    fi
    exit 0
fi
exec /usr/bin/loginctl "$@"
""")

    return bindir


def _run_fn(fn_text: str, bindir: Path, extra: str = "") -> subprocess.CompletedProcess:
    """Run a bash function in a subprocess with a controlled PATH."""
    script = f"""#!/bin/bash
set -euo pipefail
log()  {{ printf '\\033[1;34m[server-install]\\033[0m %s\\n' "$*"; }}
warn() {{ printf '\\033[1;33m[server-install]\\033[0m %s\\n' "$*" >&2; }}
have_root_or_sudo() {{
    if [[ "$(id -u)" = "0" ]]; then return 0; fi
    if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then return 0; fi
    return 1
}}
{fn_text}
{extra}
"""
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as fh:
        fh.write(script)
        path = fh.name
    try:
        env = {**os.environ, "PATH": str(bindir), "HOME": "/tmp"}
        return subprocess.run(["bash", path], capture_output=True, text=True, env=env, timeout=30)
    finally:
        os.unlink(path)


def test_readme_oneliner_does_not_require_bash() -> None:
    """The advertised command must not be `| sudo bash` -- that is the bug."""
    bad = [
        line.strip()
        for line in README.read_text(encoding="utf-8").splitlines()
        if "install-server.sh" in line and "sudo bash" in line
    ]
    assert not bad, f"README still pipes install-server.sh into `sudo bash`: {bad}"


def test_apk_branch_installs_bash() -> None:
    """Alpine/postmarketOS ships no bash, so the apk list must add it."""
    apk_lines = [ln for ln in _script().splitlines() if "apk add" in ln and "python3" in ln]
    assert apk_lines, "no apk dependency line found in install-server.sh"
    assert any(
        " bash" in ln or "bash " in ln for ln in apk_lines
    ), f"apk dependency line does not install bash: {apk_lines}"


def test_preamble_parses_under_posix_sh() -> None:
    """Everything up to the re-exec must run under `sh`, not just under bash."""
    sh = shutil.which("sh")
    assert sh, "no /bin/sh on this host"
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as fh:
        fh.write(_preamble())
        path = fh.name
    try:
        proc = subprocess.run([sh, "-n", path], capture_output=True, text=True)
        assert proc.returncode == 0, f"preamble is not POSIX sh:\n{proc.stderr}"
    finally:
        os.unlink(path)


def test_preamble_installs_bash_and_reexecs_when_bash_is_absent() -> None:
    """Behavioural: with no bash on PATH, the preamble must apk-add one and re-exec.

    A string check alone would pass on a preamble that names bash but never
    runs it, so this drives the real code path with a stubbed PATH.
    """
    sh = shutil.which("sh")
    assert sh, "no /bin/sh on this host"
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        stub = tmpdir / "bin"
        stub.mkdir()
        marker = tmpdir / "apk-called"
        reexec = tmpdir / "reexec-argv"

        # apk: records the call and materialises a `bash` that records its argv.
        (stub / "apk").write_text(
            "#!/bin/sh\n"
            f"echo \"$@\" > {marker}\n"
            f"cat > {stub}/bash <<'EOS'\n"
            "#!/bin/sh\n"
            f"echo \"$@\" > {reexec}\n"
            "EOS\n"
            f"chmod +x {stub}/bash\n",
            encoding="utf-8",
        )
        (stub / "sudo").write_text('#!/bin/sh\nexec "$@"\n', encoding="utf-8")
        for name in ("apk", "sudo"):
            (stub / name).chmod(0o755)
        # Real tools the preamble legitimately needs, minus any bash.
        for name in ("cat", "echo", "mktemp", "rm", "curl", "chmod", "sed", "command"):
            src = shutil.which(name)
            if src:
                try:
                    (stub / name).symlink_to(src)
                except FileExistsError:
                    pass

        script = tmpdir / "install-server.sh"
        script.write_text(_preamble() + "\nexit 0\n", encoding="utf-8")

        env = dict(os.environ, PATH=str(stub))
        proc = subprocess.run(
            [sh, str(script)], capture_output=True, text=True, env=env, timeout=60
        )
        assert marker.exists(), (
            "preamble did not install bash when none was on PATH; "
            f"rc={proc.returncode} stderr={proc.stderr[:600]}"
        )
        assert reexec.exists(), (
            "preamble installed bash but never re-exec'd the script under it; "
            f"rc={proc.returncode} stderr={proc.stderr[:600]}"
        )


def test_detector_true_for_hazard_shape() -> None:
    """The detector must return true for the measured hazard shape."""
    text = _script()
    fn = _extract_fn(text, "_detect_logind_hazard")
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        bindir = _make_fake_bin(
            tmpdir,
            systemd_pid1="systemd",
            killuserprocesses="b true",
            linger="no",
            has_session="yes",
            uid="1000",
            username="testuser",
        )
        proc = _run_fn(fn, bindir, extra="_detect_logind_hazard\nexit $?")
        assert proc.returncode == 0, (
            f"detector should return true for hazard shape; "
            f"rc={proc.returncode} stderr={proc.stderr[:600]}"
        )


def test_detector_false_when_linger_yes() -> None:
    """FALSE when Linger=yes -- cannot warn on every host."""
    text = _script()
    fn = _extract_fn(text, "_detect_logind_hazard")
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        bindir = _make_fake_bin(
            tmpdir,
            systemd_pid1="systemd",
            killuserprocesses="b true",
            linger="yes",
            has_session="yes",
            uid="1000",
            username="testuser",
        )
        proc = _run_fn(fn, bindir, extra="_detect_logind_hazard\nexit $?")
        assert proc.returncode == 1, (
            f"detector should return false when Linger=yes; "
            f"rc={proc.returncode} stderr={proc.stderr[:600]}"
        )


def test_detector_false_when_killuserprocesses_no() -> None:
    """FALSE when KillUserProcesses=no."""
    text = _script()
    fn = _extract_fn(text, "_detect_logind_hazard")
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        bindir = _make_fake_bin(
            tmpdir,
            systemd_pid1="systemd",
            killuserprocesses="b false",
            linger="no",
            has_session="yes",
            uid="1000",
            username="testuser",
        )
        proc = _run_fn(fn, bindir, extra="_detect_logind_hazard\nexit $?")
        assert proc.returncode == 1, (
            f"detector should return false when KillUserProcesses=no; "
            f"rc={proc.returncode} stderr={proc.stderr[:600]}"
        )


def test_detector_false_when_not_systemd() -> None:
    """FALSE when PID 1 is not systemd."""
    text = _script()
    fn = _extract_fn(text, "_detect_logind_hazard")
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        bindir = _make_fake_bin(
            tmpdir,
            systemd_pid1="init",
            killuserprocesses="b true",
            linger="no",
            has_session="yes",
            uid="1000",
            username="testuser",
        )
        proc = _run_fn(fn, bindir, extra="_detect_logind_hazard\nexit $?")
        assert proc.returncode == 1, (
            f"detector should return false when PID 1 is not systemd; "
            f"rc={proc.returncode} stderr={proc.stderr[:600]}"
        )


def test_detector_false_when_not_attached_to_session() -> None:
    """FALSE when no login session is attached."""
    text = _script()
    fn = _extract_fn(text, "_detect_logind_hazard")
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        bindir = _make_fake_bin(
            tmpdir,
            systemd_pid1="systemd",
            killuserprocesses="b true",
            linger="no",
            has_session="no",
            uid="1000",
            username="testuser",
        )
        proc = _run_fn(fn, bindir, extra="_detect_logind_hazard\nexit $?")
        assert proc.returncode == 1, (
            f"detector should return false when not attached to a session; "
            f"rc={proc.returncode} stderr={proc.stderr[:600]}"
        )


def test_systemd_run_reexec_invokes_systemd_run() -> None:
    """Assert on the systemd-run path: nohup/setsid are not the fix."""
    text = _script()
    detect_fn = _extract_fn(text, "_detect_logind_hazard")
    reexec_fn = _extract_fn(text, "_maybe_reexec_under_systemd_run")
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        bindir = _make_fake_bin(
            tmpdir,
            systemd_pid1="systemd",
            killuserprocesses="b true",
            linger="no",
            has_session="yes",
            uid="0",
            username="root",
        )
        sd_marker = tmpdir / "sd-called"
        (bindir / "systemd-run").write_text(
            f"#!/bin/sh\necho \"$@\" > {sd_marker}\nexit 0\n",
            encoding="utf-8",
        )
        (bindir / "systemd-run").chmod(0o755)

        script = f"""#!/bin/bash
set -euo pipefail
log()  {{ printf '\\033[1;34m[server-install]\\033[0m %s\\n' "$*"; }}
warn() {{ printf '\\033[1;33m[server-install]\\033[0m %s\\n' "$*" >&2; }}
have_root_or_sudo() {{
    if [[ "$(id -u)" = "0" ]]; then return 0; fi
    if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then return 0; fi
    return 1
}}
{detect_fn}
{reexec_fn}
_maybe_reexec_under_systemd_run "$@"
echo "exit_code=$?"
"""
        script_path = tmpdir / "test.sh"
        script_path.write_text(script, encoding="utf-8")
        env = {**os.environ, "PATH": str(bindir), "HOME": "/tmp", "TAOS_SYSTEMD_RUN": "1"}
        proc = subprocess.run(
            ["bash", str(script_path)], capture_output=True, text=True, env=env, timeout=30
        )
        assert sd_marker.exists(), (
            f"systemd-run was not invoked; rc={proc.returncode} stderr={proc.stderr[:600]}"
        )
        args = sd_marker.read_text()
        assert "--unit=taos-install" in args, f"missing --unit=taos-install in: {args}"
        assert "--collect" in args, f"missing --collect in: {args}"
        # nohup/setsid must NOT appear in the launch line
        assert "nohup" not in args, f"nohup must not be used as the fix: {args}"
        assert "setsid" not in args, f"setsid must not be used as the fix: {args}"


def test_no_nohup_or_setsid_in_script() -> None:
    """The script must not use nohup or setsid as the cgroup-escape fix."""
    text = _script()
    # Allow nohup/setsid in comments or unrelated contexts, but not in the
    # hazard-detection or re-exec logic.
    hazard_section = _extract_fn(text, "_detect_logind_hazard") + _extract_fn(
        text, "_maybe_reexec_under_systemd_run"
    )
    assert "nohup" not in hazard_section, (
        "nohup must not appear in the hazard detection or systemd-run re-exec path"
    )
    assert "setsid" not in hazard_section, (
        "setsid must not appear in the hazard detection or systemd-run re-exec path"
    )


def test_terminal_marker_success() -> None:
    """The log must end with an explicit SUCCESS marker."""
    text = _script()
    mark_fn = _extract_fn(text, "taos_mark_result")
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        bindir = _make_fake_bin(tmpdir)
        script = f"""{mark_fn}
taos_mark_result SUCCESS
exit 0
"""
        script_path = tmpdir / "test.sh"
        script_path.write_text(script, encoding="utf-8")
        env = {**os.environ, "PATH": str(bindir), "HOME": "/tmp"}
        proc = subprocess.run(
            ["bash", str(script_path)], capture_output=True, text=True, env=env, timeout=30
        )
        assert proc.returncode == 0
        lines = proc.stdout.strip().splitlines()
        assert lines, "no output produced"
        assert lines[-1] == "[taos-install] INSTALL_RESULT: SUCCESS", (
            f"last line should be SUCCESS marker; got: {lines[-1]!r}"
        )


def test_terminal_marker_failure_on_abort() -> None:
    """On a simulated mid-step abort, the log must end with FAILURE."""
    text = _script()
    mark_fn = _extract_fn(text, "taos_mark_result")
    trap_fn = _extract_fn(text, "_taos_install_exit_trap")
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        bindir = _make_fake_bin(tmpdir)
        script = f"""#!/bin/bash
set -euo pipefail
_taos_install_result=""
{mark_fn}
{trap_fn}
trap '_taos_install_exit_trap' EXIT
false
"""
        script_path = tmpdir / "test.sh"
        script_path.write_text(script, encoding="utf-8")
        env = {**os.environ, "PATH": str(bindir), "HOME": "/tmp"}
        proc = subprocess.run(
            ["bash", str(script_path)], capture_output=True, text=True, env=env, timeout=30
        )
        assert proc.returncode != 0
        lines = proc.stderr.strip().splitlines()
        assert lines, "no stderr produced on abort"
        assert lines[-1] == "[taos-install] INSTALL_RESULT: FAILURE", (
            f"last line on abort should be FAILURE marker; got: {lines[-1]!r}"
        )

