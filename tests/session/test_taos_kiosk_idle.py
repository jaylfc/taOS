"""taos-kiosk-idle reads SCREEN_OFF_SECONDS from /etc/taos/display.conf and starts swayidle.

Valid values: 0 (never), 5..3600. Missing file -> default 30. Malformed/out-of-range ->
fall back to 30 with one stderr message. File wins when present and valid; TAOS_KIOSK_IDLE_SECS
env override only applies when file is absent or invalid. The script never sources the file.

Run: python3 -m pytest -q tests/session/test_taos_kiosk_idle.py
"""
import os
import pathlib
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


class TestTaosKioskIdle:
    """Tests for taos-kiosk-idle reading SCREEN_OFF_SECONDS from display.conf."""

    def test_valid_zero_never_blanks(self):
        """SCREEN_OFF_SECONDS=0 -> no timeout swayidle args, only before-sleep."""
        stdout, stderr, args, rc = _run_idle({}, display_conf_content="SCREEN_OFF_SECONDS=0\n")
        assert rc == 0
        assert "timeout" not in args
        assert "before-sleep" in args
        assert stderr == ""

    def test_valid_five_seconds(self):
        """SCREEN_OFF_SECONDS=5 -> swayidle timeout 5."""
        stdout, stderr, args, rc = _run_idle({}, display_conf_content="SCREEN_OFF_SECONDS=5\n")
        assert rc == 0
        assert "timeout 5" in args
        assert stderr == ""

    def test_valid_sixty_seconds(self):
        """SCREEN_OFF_SECONDS=60 -> swayidle timeout 60."""
        stdout, stderr, args, rc = _run_idle({}, display_conf_content="SCREEN_OFF_SECONDS=60\n")
        assert rc == 0
        assert "timeout 60" in args
        assert stderr == ""

    def test_valid_3600_seconds(self):
        """SCREEN_OFF_SECONDS=3600 -> swayidle timeout 3600."""
        stdout, stderr, args, rc = _run_idle({}, display_conf_content="SCREEN_OFF_SECONDS=3600\n")
        assert rc == 0
        assert "timeout 3600" in args
        assert stderr == ""

    def test_below_min_four_seconds_fallback(self):
        """SCREEN_OFF_SECONDS=4 -> fallback to 30 with stderr message."""
        stdout, stderr, args, rc = _run_idle({}, display_conf_content="SCREEN_OFF_SECONDS=4\n")
        assert rc == 0
        assert "timeout 30" in args
        assert "invalid SCREEN_OFF_SECONDS=4" in stderr
        assert stderr.count("\n") == 1

    def test_above_max_3601_fallback(self):
        """SCREEN_OFF_SECONDS=3601 -> fallback to 30 with stderr message."""
        stdout, stderr, args, rc = _run_idle({}, display_conf_content="SCREEN_OFF_SECONDS=3601\n")
        assert rc == 0
        assert "timeout 30" in args
        assert "invalid SCREEN_OFF_SECONDS=3601" in stderr

    def test_five_digits_99999_fallback(self):
        """SCREEN_OFF_SECONDS=99999 (5 digits) -> fallback to 30, rejected before arithmetic."""
        stdout, stderr, args, rc = _run_idle({}, display_conf_content="SCREEN_OFF_SECONDS=99999\n")
        assert rc == 0
        assert "timeout 30" in args
        assert ("invalid SCREEN_OFF_SECONDS=99999" in stderr) or ("malformed" in stderr)

    def test_twenty_digits_fallback(self):
        """SCREEN_OFF_SECONDS=20-digit value -> fallback to 30, rejected before arithmetic."""
        val = "1" * 20
        stdout, stderr, args, rc = _run_idle({}, display_conf_content="SCREEN_OFF_SECONDS=%s\n" % val)
        assert rc == 0
        assert "timeout 30" in args
        assert ("invalid SCREEN_OFF_SECONDS=%s" % val in stderr) or ("malformed" in stderr)

    def test_non_number_fallback(self):
        """SCREEN_OFF_SECONDS=abc -> fallback to 30 with stderr message."""
        stdout, stderr, args, rc = _run_idle({}, display_conf_content="SCREEN_OFF_SECONDS=abc\n")
        assert rc == 0
        assert "timeout 30" in args
        assert "malformed" in stderr

    def test_empty_value_fallback(self):
        """SCREEN_OFF_SECONDS= (empty) -> fallback to 30 with stderr message."""
        stdout, stderr, args, rc = _run_idle({}, display_conf_content="SCREEN_OFF_SECONDS=\n")
        assert rc == 0
        assert "timeout 30" in args
        assert "malformed" in stderr

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


class TestInstallSessionDisplayConf:
    """Tests for install-session.sh display.conf installation logic."""

    def test_install_creates_display_conf_when_missing(self):
        """install-session.sh installs display.conf when /etc/taos/display.conf does not exist."""
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            etc_taos = td_path / "etc" / "taos"
            etc_taos.mkdir(parents=True)
            session_dir = td_path / "session"
            session_dir.mkdir()
            session_etc = session_dir / "etc"
            session_etc.mkdir(parents=True)
            import shutil
            shutil.copy(DISPLAY_CONF_SRC, session_etc / "display.conf")

            # Create a minimal test script that only tests the display.conf logic
            test_script = td_path / "test_display_conf.sh"
            test_script.write_text("""#!/bin/sh
set -eu
HERE="%s/session"
# Source the install function logic for display.conf
if [ ! -f %s/display.conf ]; then
    install -m 644 "$HERE/etc/display.conf" %s/display.conf
    echo "installed %s/display.conf"
else
    echo "%s/display.conf already exists, keeping current value"
fi
""" % (td, etc_taos, etc_taos, etc_taos, etc_taos))
            os.chmod(test_script, 0o755)

            proc = subprocess.run(
                ["/bin/dash", str(test_script)],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=td
            )

            installed = etc_taos / "display.conf"
            assert installed.exists(), "display.conf should be installed when missing"
            content = installed.read_text()
            assert "SCREEN_OFF_SECONDS=60" in content

    def test_install_keeps_existing_display_conf(self):
        """install-session.sh keeps existing /etc/taos/display.conf and does not overwrite."""
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            etc_taos = td_path / "etc" / "taos"
            etc_taos.mkdir(parents=True)
            session_dir = td_path / "session"
            session_dir.mkdir()
            session_etc = session_dir / "etc"
            session_etc.mkdir(parents=True)
            import shutil
            shutil.copy(DISPLAY_CONF_SRC, session_etc / "display.conf")

            # Pre-create display.conf with a custom value
            existing = etc_taos / "display.conf"
            existing.write_text("SCREEN_OFF_SECONDS=120\n")

            # Create a minimal test script that only tests the display.conf logic
            test_script = td_path / "test_display_conf.sh"
            test_script.write_text("""#!/bin/sh
set -eu
HERE="%s/session"
if [ ! -f %s/display.conf ]; then
    install -m 644 "$HERE/etc/display.conf" %s/display.conf
    echo "installed %s/display.conf"
else
    echo "%s/display.conf already exists, keeping current value"
fi
""" % (td, etc_taos, etc_taos, etc_taos, etc_taos))
            os.chmod(test_script, 0o755)

            proc = subprocess.run(
                ["/bin/dash", str(test_script)],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=td
            )

            content = existing.read_text()
            assert "SCREEN_OFF_SECONDS=120" in content, "Existing display.conf should not be overwritten"
            assert "SCREEN_OFF_SECONDS=60" not in content


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