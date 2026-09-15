"""The README one-liner must work on images that ship no bash.

Reported by an end-user tester on postmarketOS (Alpine base), 2026-09-15:
`curl -fsSL .../install-server.sh | sudo bash` dies at
`sudo: 'bash': command not found`. The interpreter is chosen by the PIPE, so
the script's own `#!/usr/bin/env bash` shebang never gets a say and its Alpine
apk branch -- which would have installed the dependencies -- is unreachable by
the documented command.
"""
from __future__ import annotations

import os
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
