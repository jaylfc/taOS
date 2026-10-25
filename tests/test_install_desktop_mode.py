"""Gate: install-server.sh must ask local desktop vs headless remote-only, with
TAOS_DESKTOP=local|headless for unattended installs, and run kiosk-setup.sh
when local is chosen.

This test extracts resolve_desktop_mode() verbatim from the real
scripts/install-server.sh, runs it in bash -c with stub log/warn functions and
</dev/null plus no controlling tty (start_new_session=True so /dev/tty cannot
open), then echoes $DESKTOP_MODE.

Tests:
1. TAOS_DESKTOP=local -> local
2. TAOS_DESKTOP=headless -> headless
3. TAOS_DESKTOP=bogus -> headless and the warn output names 'bogus'
4. unset, no tty -> headless with no prompt text printed
5. assert by text that install-server.sh calls install_local_desktop_if_chosen
   AFTER the install_rk3588_perf_if_needed call line and calls resolve_desktop_mode
   AFTER the warn() definition line and BEFORE the 'Linux)  ensure_linux_deps ;;' line.
6. no em dashes (U+2014) in resolve_desktop_mode or install_local_desktop_if_chosen.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_SCRIPT = REPO_ROOT / "scripts" / "install-server.sh"


def _extract_function(script: Path, name: str) -> str:
    """Extract a production shell function from its header to its closing brace."""
    text = script.read_text()
    match = re.search(rf"^{re.escape(name)}\(\)\s*\{{", text, re.MULTILINE)
    assert match, f"{name}() not found in {script}"
    start = match.start()
    depth = 0
    index = match.end() - 1
    while index < len(text):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
        index += 1
    raise AssertionError(f"could not find matching closing brace of {name}()")


def _run_resolve_desktop_mode(
    tmp_path: Path,
    env_vars: dict[str, str],
    tty_available: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run resolve_desktop_mode with given env vars, return the CompletedProcess.
    The function's DESKTOP_MODE global is echoed at the end.
    """
    function_body = _extract_function(SERVER_SCRIPT, "resolve_desktop_mode")
    wrapper = tmp_path / "wrapper.sh"
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "log() { printf '[server-install] %s\\n' \"$*\"; }\n"
        "warn() { printf '[server-install] %s\\n' \"$*\" >&2; }\n"
        + function_body
        + "\nresolve_desktop_mode\n"
        + 'echo "DESKTOP_MODE=$DESKTOP_MODE"\n'
    )
    wrapper.chmod(0o755)

    # Build env: start with minimal PATH, add test env vars
    env = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "HOME": str(tmp_path),
        **env_vars,
    }

    # Run with start_new_session=True to ensure no controlling tty
    # when tty_available=False
    return subprocess.run(
        ["/usr/bin/env", "bash", str(wrapper)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        start_new_session=not tty_available,
    )


def _assert_desktop_mode_output(
    result: subprocess.CompletedProcess[str], expected_mode: str
) -> None:
    """Assert that the DESKTOP_MODE output matches expected."""
    assert f"DESKTOP_MODE={expected_mode}" in result.stdout, (
        f"expected DESKTOP_MODE={expected_mode}; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


# --- resolve_desktop_mode behavioural tests --------------------------------

@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_taos_desktop_local(tmp_path: Path) -> None:
    """TAOS_DESKTOP=local -> local, no prompt."""
    result = _run_resolve_desktop_mode(tmp_path, {"TAOS_DESKTOP": "local"})
    _assert_desktop_mode_output(result, "local")
    # Should not print prompt text
    assert "Use this machine with a screen" not in result.stdout
    assert "Use this machine with a screen" not in result.stderr


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_taos_desktop_headless(tmp_path: Path) -> None:
    """TAOS_DESKTOP=headless -> headless, no prompt."""
    result = _run_resolve_desktop_mode(tmp_path, {"TAOS_DESKTOP": "headless"})
    _assert_desktop_mode_output(result, "headless")
    assert "Use this machine with a screen" not in result.stdout
    assert "Use this machine with a screen" not in result.stderr


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_taos_desktop_bogus_warns_and_defaults_headless(tmp_path: Path) -> None:
    """TAOS_DESKTOP=bogus -> headless and warn names the bad value."""
    result = _run_resolve_desktop_mode(tmp_path, {"TAOS_DESKTOP": "bogus"})
    _assert_desktop_mode_output(result, "headless")
    # warn should mention the bad value and the two valid ones
    combined = result.stdout + result.stderr
    assert "bogus" in combined, (
        f"warn must name the bad value 'bogus'; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "local" in combined and "headless" in combined, (
        f"warn must name both valid values; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_unset_no_tty_defaults_headless_no_prompt(tmp_path: Path) -> None:
    """TAOS_DESKTOP unset, no /dev/tty -> headless, no prompt text printed."""
    result = _run_resolve_desktop_mode(tmp_path, {}, tty_available=False)
    _assert_desktop_mode_output(result, "headless")
    # No prompt text should be printed
    assert "Use this machine with a screen" not in result.stdout
    assert "Use this machine with a screen" not in result.stderr


# --- install-server.sh call-order assertions ---------------------------------

def test_call_order_resolve_desktop_mode_before_linux_deps() -> None:
    """resolve_desktop_mode is called AFTER warn() definition and BEFORE 'Linux)  ensure_linux_deps ;;'."""
    text = SERVER_SCRIPT.read_text()
    lines = text.splitlines()

    # Find line numbers (1-indexed)
    warn_def_line = None
    resolve_line = None
    linux_deps_line = None

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("warn()"):
            warn_def_line = i
        elif stripped == "resolve_desktop_mode":
            resolve_line = i
        elif stripped == "Linux)  ensure_linux_deps ;;":
            linux_deps_line = i

    assert warn_def_line is not None, "warn() definition not found"
    assert resolve_line is not None, "resolve_desktop_mode call not found"
    assert linux_deps_line is not None, "'Linux)  ensure_linux_deps ;;' not found"

    assert resolve_line > warn_def_line, (
        f"resolve_desktop_mode (line {resolve_line}) must be called "
        f"AFTER warn() definition (line {warn_def_line})"
    )
    assert resolve_line < linux_deps_line, (
        f"resolve_desktop_mode (line {resolve_line}) must be called "
        f"BEFORE 'Linux)  ensure_linux_deps ;;' (line {linux_deps_line})"
    )


def test_call_order_install_local_desktop_after_install_rk3588_perf() -> None:
    """install_local_desktop_if_chosen is called AFTER install_rk3588_perf_if_needed."""
    text = SERVER_SCRIPT.read_text()
    perf_line = None
    desktop_line = None
    for i, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped == "install_rk3588_perf_if_needed":
            perf_line = i
        elif stripped == "install_local_desktop_if_chosen":
            desktop_line = i
    assert perf_line is not None, "install_rk3588_perf_if_needed call not found"
    assert desktop_line is not None, "install_local_desktop_if_chosen call not found"
    assert desktop_line > perf_line, (
        f"install_local_desktop_if_chosen (line {desktop_line}) must be called "
        f"AFTER install_rk3588_perf_if_needed (line {perf_line})"
    )


def test_no_em_dash_in_desktop_functions() -> None:
    """resolve_desktop_mode and install_local_desktop_if_chosen must not contain em dashes (U+2014)."""
    for func_name in ("resolve_desktop_mode", "install_local_desktop_if_chosen"):
        func_text = _extract_function(SERVER_SCRIPT, func_name)
        assert "\u2014" not in func_text, (
            f"em dash (U+2014) found in {func_name}(); replace with ' -- '"
        )