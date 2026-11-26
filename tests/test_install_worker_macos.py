"""macOS worker install: Apple Silicon registers as `gpu-metal`.

Gates issue #37 — the macOS branch of ``scripts/install-worker.sh`` and the
worker-side resource contract it feeds:

  * Apple Silicon (arm64 + Metal) registers the worker as ``gpu-metal``
    (docs/design/resource-scheduler.md resource table), with MLX probed only
    to advise on GPU-inference-on-MLX availability.
  * Intel Macs (and arm64 hosts whose Metal probe answers nothing, e.g. a VM)
    fall back to ``cpu-inference``.
  * The detected classes are baked into the launchd plist written under
    ``~/Library/LaunchAgents`` so the registration survives a re-login.
  * ``tinyagentos.worker.agent`` advertises the installer-detected classes and
    reports Apple Silicon as ``gpu-metal`` rather than the CUDA class.

The install-script cases run the production shell functions in a simulated
environment (stubbed ``uname``/``system_profiler``/``sysctl``/``python3`` and a
throwaway ``$HOME``), the same technique
``tests/test_install_hailo_preexisting_refusal.py`` uses. The functions are
extracted from the script, so the assertions exercise the shipped code, not a
copy of it.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SCRIPT = REPO_ROOT / "scripts" / "install-worker.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="bash required to exercise the installer"
)


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


def _run_wrapper(tmp_path: Path, body: str, env: dict[str, str] | None = None):
    wrapper = tmp_path / "wrapper.sh"
    wrapper.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body)
    wrapper.chmod(0o755)

    master, slave = pty.openpty()
    # No echo: the typed URL must not show up in the output on its own;
    # it only counts when the pair stub reports it in its arguments.
    attrs = termios.tcgetattr(slave)
    attrs[3] &= ~termios.ECHO
    termios.tcsetattr(slave, termios.TCSANOW, attrs)
    proc = _sp.Popen(
        ["bash", str(wrapper)],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
        close_fds=True,
    )
    os.close(slave)
    os.write(master, b"http://manual.example:6969\n")
    out = b""
    try:
        while True:
            ready, _, _ = select.select([master], [], [], 60)
            if not ready:
                proc.kill()
                raise AssertionError(f"pty run timed out; output: {out!r}")
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
    finally:
        proc.wait(timeout=10)
        os.close(master)
    text = out.decode(errors="replace")
    assert "PAIR_RC=0" in text, text
    pair_lines = [ln for ln in text.splitlines() if "PAIR_ARGS" in ln]
    assert any(
        "http://manual.example:6969" in ln for ln in pair_lines
    ), text
