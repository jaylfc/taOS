"""taos-kiosk-idle reads SCREEN_OFF_SECONDS from /etc/taos/display.conf and starts swayidle.

Valid values: 0 (never), 5..3600. Missing file -> default 30. Malformed/out-of-range ->
fall back to 30 with one stderr message. File wins when present and valid; TAOS_KIOSK_IDLE_SECS
env override only applies when file is absent or invalid. The script never sources the file.

Run: python3 -m pytest -q tests/session/test_taos_kiosk_idle.py
"""
import os
import pathlib
import pwd
import subprocess
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
BIN = ROOT / "session" / "bin"
IDLE_SCRIPT = BIN / "taos-kiosk-idle"
INSTALL_SCRIPT = ROOT / "session" / "install-session.sh"
DISPLAY_CONF_SRC = ROOT / "session" / "etc" / "display.conf"


def _make_swayidle_stub(swaybin, swayidle_log):
    """Create a swayidle stub that logs its arguments."""
    stub = swaybin / "swayidle"
    # Escape % for Python string formatting; the shell script uses printf "%s\n"
    stub.write_text(
        '#!/bin/sh\n'
        'printf "%%s\\n" "$*" > "%s"\n'
        'exit 0\n' % swayidle_log)
    os.chmod(stub, 0o755)


def _make_pkill_stub(swaybin):
    stub = swaybin / "pkill"
    stub.write_text('#!/bin/sh\nexit 0\n')
    os.chmod(stub, 0o755)


def _make_swaymsg_stub(swaybin):
    stub = swaybin / "swaymsg"
    stub.write_text('#!/bin/sh\nexit 0\n')
    os.chmod(stub, 0o755)


def _run_idle(env, display_conf_content=None, display_conf_path=None):
    """Run taos-kiosk-idle under dash with a stubbed swayidle and return (stdout, stderr, swayidle_args, rc)."""
    with tempfile.TemporaryDirectory() as td:
        td_path = pathlib.Path(td)
        swaybin = td_path / "swaybin"
        swaybin.mkdir()
        swayidle_log = td_path / "swayidle.log"

        _make_swayidle_stub(swaybin, swayidle_log)
        _make_pkill_stub(swaybin)
        _make_swaymsg_stub(swaybin)

        # Prepare display.conf if provided
        if display_conf_path is None:
            display_conf_path = td_path / "display.conf"
        if display_conf_content is not None:
            display_conf_path.write_text(display_conf_content)

        # Environment
        test_env = dict(os.environ,
                        PATH="%s:%s" % (swaybin, os.environ.get("PATH", "")),
                        TAOS_DISPLAY_CONF=str(display_conf_path),
                        XDG_RUNTIME_DIR=str(td_path / "runtime"))
        test_env.update(env)
        # Don't pop TAOS_KIOSK_IDLE_SECS if it was explicitly set in env
        if "TAOS_KIOSK_IDLE_SECS" not in env:
            test_env.pop("TAOS_KIOSK_IDLE_SECS", None)

        # Run the script with dash
        proc = subprocess.run(
            ["/bin/dash", str(IDLE_SCRIPT)],
            env={k: v for k, v in test_env.items() if v is not None},
            capture_output=True,
            text=True,
            timeout=10
        )

        swayidle_args = ""
        if swayidle_log.exists():
            swayidle_args = swayidle_log.read_text().strip()

        return proc.stdout, proc.stderr, swayidle_args, proc.returncode


# Every value in 0, 5..3600 with no leading zero must be accepted verbatim.
# The old pattern only matched 5-299 and 3000-3600, so 300/600/999/1000/1800/2999
# (a 5- or 30-minute timeout) silently fell back to 30.
ACCEPTED = [0, 5, 6, 59, 60, 299, 300, 600, 999, 1000, 1800, 2999, 3000, 3599, 3600]

# Everything else falls back to the default 30 with one message on stderr.
# Values that fail the anchored grep (99999, -5, 1.5, empty, text, a trailing
# space) are rejected before arithmetic; 4, 3601 and 0300 reach the range
# check (0300 also trips the leading-zero guard) and are rejected there.
REJECTED = ["4", "3601", "99999", "-5", "1.5", "", "text", "0300", "60 "]


class TestTaosKioskIdle:
    """Tests for taos-kiosk-idle reading SCREEN_OFF_SECONDS from display.conf."""

    @pytest.mark.parametrize("value", ACCEPTED)
    def test_accepted_range_runs_under_dash(self, value):
        """An in-range SCREEN_OFF_SECONDS sets that swayidle timeout, no warning (under dash)."""
        stdout, stderr, args, rc = _run_idle(
            {}, display_conf_content="SCREEN_OFF_SECONDS=%d\n" % value)
        assert rc == 0
        if value == 0:
            # 0 means never: no timeout, only before-sleep handler
            assert "timeout" not in args
            assert "before-sleep" in args
        else:
            assert "timeout %d" % value in args
        assert stderr == ""

    @pytest.mark.parametrize("value", REJECTED)
    def test_rejected_falls_back_with_message(self, value):
        """A rejected SCREEN_OFF_SECONDS falls back to 30 and warns (run under dash)."""
        stdout, stderr, args, rc = _run_idle(
            {}, display_conf_content="SCREEN_OFF_SECONDS=%s\n" % value)
        assert rc == 0
        assert "timeout 30" in args
        assert stderr != "", "expected a fallback message for %r" % value
        assert stderr.count("\n") == 1

    def test_key_missing_fallback(self):
        """File exists but key missing -> fallback to 30 with stderr message."""
        stdout, stderr, args, rc = _run_idle({}, display_conf_content="# comment only\nOTHER=123\n")
        assert rc == 0
        assert "timeout 30" in args
        assert ("malformed" in stderr) or ("missing" in stderr)

    def test_missing_file_uses_default(self):
        """Missing display.conf -> uses default 30 (from script default)."""
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            display_conf_path = td_path / "nonexistent.conf"
            stdout, stderr, args, rc = _run_idle({}, display_conf_content=None, display_conf_path=display_conf_path)
            assert rc == 0
            assert "timeout 30" in args
            assert stderr == ""

    def test_env_override_when_file_missing(self):
        """TAOS_KIOSK_IDLE_SECS env override works when file is missing."""
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            display_conf_path = td_path / "nonexistent.conf"
            env = {"TAOS_KIOSK_IDLE_SECS": "120"}
            stdout, stderr, args, rc = _run_idle(env, display_conf_content=None, display_conf_path=display_conf_path)
            assert rc == 0
            assert "timeout 120" in args
            assert stderr == ""

    def test_env_override_when_file_missing_zero(self):
        """TAOS_KIOSK_IDLE_SECS=0 env override works when file is missing (never blank)."""
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            display_conf_path = td_path / "nonexistent.conf"
            env = {"TAOS_KIOSK_IDLE_SECS": "0"}
            stdout, stderr, args, rc = _run_idle(env, display_conf_content=None, display_conf_path=display_conf_path)
            assert rc == 0
            assert "timeout" not in args
            assert "before-sleep" in args
            assert stderr == ""

    def test_file_wins_over_env_override(self):
        """Valid file value wins over TAOS_KIOSK_IDLE_SECS env override."""
        env = {"TAOS_KIOSK_IDLE_SECS": "120"}
        stdout, stderr, args, rc = _run_idle(env, display_conf_content="SCREEN_OFF_SECONDS=45\n")
        assert rc == 0
        assert "timeout 45" in args
        assert stderr == ""

    def test_env_override_used_when_file_invalid(self):
        """TAOS_KIOSK_IDLE_SECS env override works when file exists but is invalid."""
        env = {"TAOS_KIOSK_IDLE_SECS": "120"}
        stdout, stderr, args, rc = _run_idle(env, display_conf_content="SCREEN_OFF_SECONDS=abc\n")
        assert rc == 0
        assert "timeout 120" in args
        assert "malformed" in stderr

    def test_script_never_sources_file(self):
        """The script must never source the config file.
        Put a harmless marker command in the file and assert it never runs."""
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            marker = td_path / "marker_ran"
            swaybin = td_path / "swaybin"
            swaybin.mkdir()
            swayidle_log = td_path / "swayidle.log"
            _make_swayidle_stub(swaybin, swayidle_log)
            _make_pkill_stub(swaybin)
            _make_swaymsg_stub(swaybin)

            display_conf = td_path / "display.conf"
            display_conf.write_text(
                "SCREEN_OFF_SECONDS=60\n"
                'echo "sourced" > "%s"\n' % marker)

            test_env = dict(os.environ,
                            PATH="%s:%s" % (swaybin, os.environ.get("PATH", "")),
                            TAOS_DISPLAY_CONF=str(display_conf),
                            XDG_RUNTIME_DIR=str(td_path / "runtime"))

            proc = subprocess.run(
                ["/bin/dash", str(IDLE_SCRIPT)],
                env={k: v for k, v in test_env.items() if v is not None},
                capture_output=True,
                text=True,
                timeout=10
            )

            rc = proc.returncode
            assert rc == 0
            assert not marker.exists(), "Config file was sourced! Marker file was created."
            swayidle_args = swayidle_log.read_text().strip()
            assert "timeout 60" in swayidle_args


def _run_installer(destdir):
    """Run the real install-session.sh with TAOS_SESSION_DESTDIR pointed at a staging root."""
    test_env = dict(os.environ, TAOS_SESSION_DESTDIR=str(destdir))
    return subprocess.run(
        ["/bin/dash", str(INSTALL_SCRIPT)],
        env=test_env,
        capture_output=True,
        text=True,
        timeout=20,
    )


def _skip_if_not_root():
    """Skip the test if not running as root (installer needs root for staging hook)."""
    if os.geteuid() != 0:
        pytest.skip("install-session.sh staging hook requires root")
    try:
        pwd.getpwnam("taos")
    except KeyError:
        pytest.skip("install-session.sh staging past session setup requires the taos user")


class TestInstallSessionDisplayConf:
    """install-session.sh places the session helpers and display.conf under TAOS_SESSION_DESTDIR.

    These drive the real installer, not a re-typed snippet: if the install logic
    in install-session.sh is removed the files never land under DESTDIR here.
    """

    def test_installer_places_idle_and_display_conf(self):
        """The real installer installs taos-kiosk-idle and the display.conf sample under TAOS_SESSION_DESTDIR."""
        _skip_if_not_root()
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            proc = _run_installer(td_path)
            assert proc.returncode == 0, proc.stderr + proc.stdout

            idle = td_path / "usr" / "local" / "bin" / "taos-kiosk-idle"
            conf = td_path / "etc" / "taos" / "display.conf"
            assert idle.is_file(), "taos-kiosk-idle was not installed under TAOS_SESSION_DESTDIR"
            assert os.access(idle, os.X_OK), "installed taos-kiosk-idle is not executable"
            assert conf.is_file(), "display.conf was not installed under TAOS_SESSION_DESTDIR"
            # It is the real sample file, copied verbatim, not a stub.
            assert conf.read_bytes() == DISPLAY_CONF_SRC.read_bytes()

    def test_installer_keeps_existing_display_conf(self):
        """A pre-existing display.conf under TAOS_SESSION_DESTDIR is not overwritten."""
        _skip_if_not_root()
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            existing = td_path / "etc" / "taos" / "display.conf"
            existing.parent.mkdir(parents=True)
            existing.write_text("SCREEN_OFF_SECONDS=120\n")

            proc = _run_installer(td_path)
            assert proc.returncode == 0, proc.stderr + proc.stdout

            content = existing.read_text()
            assert "SCREEN_OFF_SECONDS=120" in content, "Existing display.conf should not be overwritten"
            assert "SCREEN_OFF_SECONDS=30" not in content


class TestScriptSyntax:
    """Syntax checks for the shell scripts."""

    def test_idle_script_syntax(self):
        """taos-kiosk-idle passes sh -n (dash syntax check)."""
        proc = subprocess.run(["/bin/dash", "-n", str(IDLE_SCRIPT)], capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr

    def test_install_script_syntax(self):
        """install-session.sh passes sh -n (dash syntax check)."""
        proc = subprocess.run(["/bin/dash", "-n", str(INSTALL_SCRIPT)], capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
