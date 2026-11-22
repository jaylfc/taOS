"""Regression test for gnupg missing in ensure_linux_deps (tsk-cqdg43).

The NodeSource step in ensure_node22() uses gpg for key verification but
ensure_linux_deps() never installs gnupg (the package providing gpg).
On a fresh Debian system this causes a silent exit under set -e.

This test extracts the ensure_node22 function and runs it with gpg absent
from PATH. Before the fix, this should fail silently or with an unclear
error. After the fix, gnupg should be installed first or the installer
should die with a clear message.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

INSTALL_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "install-server.sh"


def _extract_func(name: str) -> str:
    """Return the shell source text for a single function from install-server.sh."""
    lines = INSTALL_SCRIPT.read_text().splitlines()
    collecting = False
    brace_depth = 0
    collected: list[str] = []
    for line in lines:
        if not collecting:
            if line.startswith(f"{name}()"):
                collecting = True
        if collecting:
            collected.append(line)
            brace_depth += line.count("{") - line.count("}")
            if brace_depth <= 0 and len(collected) > 1:
                break
    return "\n".join(collected)


# NodeSource key fingerprint the script expects
NODESOURCE_FP = "6F71F525282841EEDAF851B42F59B5F99B1BE0B4"


def _run_ensure_node22_apt(tmp_path, *, gpg_available: bool, gnupg_installed: bool):
    """Run the apt path of ensure_node22 against a fake environment.

    Returns (rc, stdout, stderr).
    """
    fake = tmp_path / "fakeroot"
    keyrings = fake / "usr/share/keyrings"
    sources = fake / "etc/apt/sources.list.d"
    keyrings.mkdir(parents=True)
    sources.mkdir(parents=True)
    apt_log = tmp_path / "apt.log"
    apt_log.touch()

    # Stub commands
    stubs = tmp_path / "stubs"
    stubs.mkdir()

    # curl: always succeeds, writes a dummy key
    (stubs / "curl").write_text(
        "#!/bin/sh\n"
        "while [ $# -gt 0 ]; do\n"
        '  case "$1" in\n'
        "    -o) out=$2; shift 2; continue ;;\n"
        "  esac\n"
        "  shift\n"
        "done\n"
        'printf "DUMMY-KEY\\n" > "$out"\n',
        encoding="utf-8",
    )

    # gpg: only present if gpg_available=True
    if gpg_available:
        (stubs / "gpg").write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "--with-colons" ] && [ "$2" = "--import-options" ] && [ "$3" = "show-only" ] && [ "$4" = "--import" ]; then\n'
            f'  printf "fpr:::::::::{NODESOURCE_FP}:\\n"\n'
            "  exit 0\n"
            "fi\n"
            'if [ "$1" = "--dearmor" ]; then\n'
            '  cat > "$3"\n'
            "  exit 0\n"
            "fi\n"
            "exit 1\n",
            encoding="utf-8",
        )

    # apt-get: logs invocations
    (stubs / "apt-get").write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> {apt_log}\n'
        'for a in "$@"; do\n'
        "  case $a in\n"
        "    update) exit 0 ;;\n"
        "    install) exit 0 ;;\n"
        "  esac\n"
        "done\n"
        "exit 0\n",
        encoding="utf-8",
    )

    # dpkg: for architecture
    (stubs / "dpkg").write_text("#!/bin/sh\necho amd64\n", encoding="utf-8")

    # mkdir, chmod, tee, etc. (create stubs for all to avoid permission issues)
    for name in ("mkdir", "chmod", "tee", "mktemp", "mv", "rm", "awk", "head", "sed", "cut", "tr", "printf"):
        (stubs / name).write_text(f"#!/bin/sh\n/usr/bin/{name} \"$@\"\n", encoding="utf-8")

    # sudo: drops env vars and runs command
    (stubs / "sudo").write_text(
        "#!/bin/sh\n"
        "while [ $# -gt 0 ]; do\n"
        '  case "$1" in\n'
        "    [A-Z_]*=*) shift; continue ;;\n"
        "  esac\n"
        "  break\n"
        "done\n"
        '"$@"\n',
        encoding="utf-8",
    )

    # command: for command -v checks
    (stubs / "command").write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "-v" ]; then\n'
        "  shift\n"
        '  case "$1" in\n'
        "    apt-get) exit 0 ;;\n"
        "    node) exit 1 ;;\n"  # node not found -> triggers upgrade
        "    gpg)\n"
        f"      [ {1 if gpg_available else 0} -eq 1 ] && exit 0 || exit 1\n"
        "      ;;\n"
        "    curl) exit 0 ;;\n"
        "  esac\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )

    for f in stubs.iterdir():
        f.chmod(0o755)

    # Build the script
    script_parts = [
        "set -euo pipefail",
        "log()  { printf '[log] %s\\n' \"$*\" >&2; }",
        "warn() { printf '[warn] %s\\n' \"$*\" >&2; }",
        "die()  { printf '[die] %s\\n' \"$*\" >&2; exit 1; }",
        # CURL_RETRY setup
        'CURL_RETRY=(--retry 5 --retry-delay 3 --connect-timeout 15 --max-time 60)',
        # APT_RETRY setup
        'APT_RETRY=(-o Acquire::Retries=3)',
        # Mock uname
        "uname() { echo Linux; }",
        # Mock lsb_release for codename
        "lsb_release() { echo trixie; }",
        # Mock dpkg --print-architecture
        "dpkg() { echo amd64; }",
        # Mock mktemp to use our temp dir
        f"mktemp() {{ echo {tmp_path}/nodesource-key.XXXXXX; }}",
        # Extract the real ensure_node22 function
        _extract_func("ensure_node22"),
        # Call it
        "ensure_node22 && echo RC=0 || echo RC=$?",
    ]
    script = "\n".join(script_parts).replace("/usr/share/keyrings", f"{fake}/usr/share/keyrings").replace("/etc/apt/sources.list.d", f"{fake}/etc/apt/sources.list.d")

    env = os.environ.copy()
    # Minimal PATH: our stubs first, then a filtered system PATH
    # Create a filtered bin dir with essential commands but no gpg when gpg_available=False
    filtered_bin = tmp_path / "filtered_bin"
    filtered_bin.mkdir()
    # Always need bash
    for cmd in ("bash", "sh", "mkdir", "chmod", "tee", "mktemp", "mv", "rm", "awk", "head", "sed", "cut", "tr", "printf", "curl", "sudo", "lsb_release", "dpkg", "cat", "echo", "rm", "chmod", "sed", "install", "tee", "mkdir", "chmod", "gpg"):
        src = shutil.which(cmd)
        if src and (cmd != "gpg" or gpg_available):
            try:
                (filtered_bin / cmd).symlink_to(src)
            except FileExistsError:
                pass
    # Also need /bin commands
    for cmd in ("ls", "cp", "sleep", "date", "uname", "id", "whoami"):
        src = shutil.which(cmd)
        if src:
            try:
                (filtered_bin / cmd).symlink_to(src)
            except FileExistsError:
                pass
    env["PATH"] = f"{stubs}:{filtered_bin}"
    env["APT_LOG"] = str(apt_log)

    proc = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    return proc.returncode, proc.stdout, proc.stderr


@pytest.mark.skipif(os.name != "posix", reason="bash-only test")
def test_ensure_node22_fails_without_gpg(tmp_path):
    """RED: ensure_node22 should not silently exit when gpg is missing.

    Before the fix: gpg is not installed by ensure_linux_deps, so the
    NodeSource key verification fails with 'command not found' and the
    script exits silently under set -e.

    After the fix: ensure_node22 checks for gpg and dies with a clear
    message if not found (safety net), and ensure_linux_deps installs
    gnupg before ensure_node22 runs.
    """
    rc, stdout, stderr = _run_ensure_node22_apt(tmp_path, gpg_available=False, gnupg_installed=False)

    # The fix adds a check at the start of the apt/dnf paths that dies
    # with a clear message if gpg is not found.
    print(f"rc={rc}")
    print(f"stdout={stdout}")
    print(f"stderr={stderr}")

    assert rc != 0, "ensure_node22 should fail when gpg is not available"
    # The error should mention gpg/gnupg clearly
    assert "gpg (gnupg)" in stderr, (
        f"Error message should mention 'gpg (gnupg)' clearly; stderr={stderr}"
    )
    assert "ensure_linux_deps should have installed it" in stderr, (
        f"Error message should reference ensure_linux_deps; stderr={stderr}"
    )


@pytest.mark.skipif(os.name != "posix", reason="bash-only test")
def test_ensure_node22_succeeds_with_gpg(tmp_path):
    """GREEN: ensure_node22 should succeed when gpg is available."""
    rc, stdout, stderr = _run_ensure_node22_apt(tmp_path, gpg_available=True, gnupg_installed=True)
    print(f"rc={rc}")
    print(f"stdout={stdout}")
    print(f"stderr={stderr}")
    assert rc == 0, f"ensure_node22 should succeed when gpg is available; stderr={stderr}"
