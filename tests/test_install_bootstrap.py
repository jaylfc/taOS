"""Bootstrap sanity: requires-python bound and pick_system_python preference.

These guard the Python version support contract that the installer and pyproject
share. All offline; the function under test is extracted verbatim from
scripts/install-server.sh so a regression in the production code fails this
gate, not a stale copy.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import packaging.specifiers
import pytest
import tomllib

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SCRIPT = REPO_ROOT / "scripts" / "install-server.sh"


def _extract_pick_system_python() -> str:
    """Return the body of pick_system_python() from install-server.sh."""
    text = INSTALL_SCRIPT.read_text()
    m = None
    for line in text.splitlines():
        if line.startswith("pick_system_python()"):
            m = line
            break
    assert m, "pick_system_python() not found in install-server.sh"
    start = text.index(m)
    depth = 0
    i = start
    while i < len(text):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
        i += 1
    raise AssertionError("could not find matching closing brace of pick_system_python()")


def test_requires_python_admits_3_11_and_3_14():
    """The pyproject bound must admit both the floor (3.11) and 3.14."""
    with open(REPO_ROOT / "pyproject.toml", "rb") as fh:
        doc = tomllib.load(fh)
    spec = packaging.specifiers.SpecifierSet(doc["project"]["requires-python"])
    assert "3.11" in spec, "requires-python must admit 3.11"
    assert "3.14" in spec, "requires-python must admit 3.14"
    assert "3.15" not in spec, "requires-python must not admit 3.15"
    assert "3.10" not in spec, "requires-python must not admit 3.10"


def _write_mock_python(tmp: Path, name: str, version: int) -> Path:
    """Write a mock python executable that prints `version` and exits 0."""
    exe = tmp / name
    exe.write_text(f"#!/bin/sh\necho {version}\n")
    exe.chmod(0o755)
    return exe


def _run_pick_system_python(tmp: Path, python_versions: dict[str, int]) -> str | None:
    """Run the real pick_system_python against mock python executables.

    `python_versions` maps interpreter name -> numeric version (e.g. 313).
    Returns the name of the selected interpreter, or None if none matched.
    """
    func_body = _extract_pick_system_python()
    lines = [
        "#!/bin/sh",
        "set -u",
        'PATH="' + str(tmp) + ':$PATH"',
    ]
    # The extracted body includes the function header and closing brace.
    # We only need to prepend a PATH export; the function definition is complete.
    for line in func_body.splitlines():
        lines.append(line)
    lines.append("result=$(pick_system_python || true)")
    lines.append('printf "%s\\n" "${result:-}"')
    script = tmp / "wrapper.sh"
    script.write_text("\n".join(lines) + "\n")
    script.chmod(0o755)
    # Write mock executables
    for name, version in python_versions.items():
        _write_mock_python(tmp, name, version)
    proc = subprocess.run(
        [str(script)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode == 0, f"wrapper failed: {proc.stderr}"
    out = proc.stdout.strip()
    return out if out else None


@pytest.mark.skipif(os.name != "posix", reason="bash-only test")
def test_pick_system_python_prefers_3_13_over_3_14():
    """When a 3.13 interpreter and a 3.14 python3 both exist, 3.13 must win."""
    with tempfile.TemporaryDirectory() as tmp:
        result = _run_pick_system_python(
            Path(tmp),
            {
                "python3.13": 313,
                "python3.12": 310,
                "python3.11": 310,
                "python3": 314,
            },
        )
    assert result == "python3.13", (
        f"expected python3.13 when 3.13 and a 3.14 python3 both exist, got {result}"
    )


@pytest.mark.skipif(os.name != "posix", reason="bash-only test")
def test_pick_system_python_accepts_3_14_via_system_python():
    """The range check must accept 3.14 (e.g. Alpine's system python3)."""
    with tempfile.TemporaryDirectory() as tmp:
        result = _run_pick_system_python(
            Path(tmp),
            {
                "python3.13": 310,
                "python3.12": 310,
                "python3.11": 310,
                "python3": 314,
            },
        )
    assert result == "python3", (
        f"expected python3 (3.14) as the only in-range interpreter, got {result}"
    )


@pytest.mark.skipif(os.name != "posix", reason="bash-only test")
def test_pick_system_python_rejects_3_10():
    """No interpreter below 3.11 must be selected."""
    with tempfile.TemporaryDirectory() as tmp:
        result = _run_pick_system_python(
            Path(tmp),
            {
                "python3.13": 310,
                "python3.12": 310,
                "python3.11": 310,
                "python3.10": 310,
                "python3": 310,
            },
        )
    assert result is None, f"expected no match for 3.10, got {result}"
