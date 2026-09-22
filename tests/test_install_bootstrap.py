"""Bootstrap sanity for the installer: bash bootstrap and the Python version contract.

Part 1 -- the README one-liner must work on images that ship no bash:

Reported by an end-user tester on postmarketOS (Alpine base), 2026-09-15:
`curl -fsSL .../install-server.sh | sudo bash` dies at
`sudo: 'bash': command not found`. The interpreter is chosen by the PIPE, so
the script's own `#!/usr/bin/env bash` shebang never gets a say and its Alpine
apk branch -- which would have installed the dependencies -- is unreachable by
the documented command.

Part 2 -- requires-python bound and pick_system_python preference:

These guard the Python version support contract that the installer and pyproject
share. All offline; the function under test is extracted verbatim from
scripts/install-server.sh so a regression in the production code fails this
gate, not a stale copy.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import packaging.specifiers
import pytest
import tomllib

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


def test_mktemp_templates_end_in_x() -> None:
    """BSD/macOS/busybox mktemp requires the template to end in Xs (no suffix).

    The script already documents this rule at the bundle mktemp site; this
    test guards all five sites at once.
    """
    import re

    bad = []
    for lineno, line in enumerate(_script().splitlines(), start=1):
        if "mktemp" not in line:
            continue
        stripped = line.strip()
        # Skip comments and traps.
        if stripped.startswith("#") or stripped.startswith("trap"):
            continue
        # Extract the first non-flag argument after mktemp.
        # Matches mktemp [flags] <template> where template is the last token
        # on the line or inside a $(...) assignment.
        m = re.search(r"mktemp\s+(?:-\w+\s+)?([^\s#\"')\]]+)", stripped)
        if not m:
            continue
        template = m.group(1).strip("'\"")
        if "X" in template and not template.endswith("X"):
            bad.append((lineno, stripped))
    assert not bad, (
        "mktemp templates with a suffix after the Xs (fails on busybox/BSD/macOS):\n"
        + "\n".join(f"  line {ln}: {txt}" for ln, txt in bad)
    )


def test_qmd_npm_install_has_no_unsafe_perm() -> None:
    """`--unsafe-perm` was removed in npm >= 11 and now hard-errors."""
    bad = []
    in_qmd = False
    for line in _script().splitlines():
        if "TAOS_SKIP_QMD" in line and ":" in line:
            in_qmd = True
        if in_qmd:
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "npm install -g" in stripped and "--unsafe-perm" in stripped:
                bad.append(stripped)
            if stripped == "fi" and bad:
                break
    assert not bad, (
        "qmd npm install still passes removed flag --unsafe-perm:\n"
        + "\n".join(f"  {ln}" for ln in bad)
    )

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
