"""Regression test for the gnupg silent-exit fix (tsk-cqdg43, fix-forward tsk-7losak).

ensure_node22() verifies the NodeSource repo key with gpg. Before the fix,
ensure_linux_deps() never installed gnupg, so on a fresh system the
$(gpg ... | awk) assignment failed under `set -euo pipefail` with gpg's
stderr discarded: a silent non-zero exit and no error message.

This test extracts ensure_node22 from install-server.sh and runs it as a bare
statement under `set -euo pipefail` with gpg absent from PATH:
- against the pre-PR script (git object 88fe9372f, exec/tsk-cqdg43 base):
  non-zero exit, empty output, no clear error message (the bug);
- against the current script: non-zero exit with a clear die message naming
  gpg (gnupg).

A second test asserts ensure_linux_deps installs gnupg on apt and gnupg2 on
dnf.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

INSTALL_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "install-server.sh"

# exec/tsk-cqdg43 base: install-server.sh before the gnupg fix landed.
PRE_PR_COMMIT = "88fe9372f7667b4ba97a295784fc1bfd998d6313"

# NodeSource key fingerprint the script expects
NODESOURCE_FP = "6F71F525282841EEDAF851B42F59B5F99B1BE0B4"


def _extract_func(text: str, name: str) -> str:
    """Return the shell source text for a single function from script source text."""
    lines = text.splitlines()
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


def _install_script_source(use_pre_pr: bool) -> str:
    """Source of install-server.sh: current working tree, or the pre-PR base."""
    if use_pre_pr:
        proc = subprocess.run(
            ["git", "show", f"{PRE_PR_COMMIT}:scripts/install-server.sh"],
            capture_output=True,
            text=True,
            check=True,
        )
        return proc.stdout
    return INSTALL_SCRIPT.read_text()


def _run_ensure_node22(tmp_path, *, source: str, gpg_available: bool):
    """Run a given ensure_node22 source with gpg absent or present.

    Returns (rc, stdout, stderr). ensure_node22 is invoked as a bare statement
    so `set -euo pipefail` applies to it.
    """
    fake = tmp_path / "fakeroot"
    keyrings = fake / "usr/share/keyrings"
    sources = fake / "etc/apt/sources.list.d"
    keyrings.mkdir(parents=True)
    sources.mkdir(parents=True)

    stubs = tmp_path / "stubs"
    stubs.mkdir()

    # curl: writes a dummy key to the -o output path.
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

    # gpg: fingerprint stub when available, silent failing stub otherwise.
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
    else:
        (stubs / "gpg").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")

    # apt-get: always succeeds.
    (stubs / "apt-get").write_text(
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        "  update|install) exit 0 ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )

    # dpkg: for architecture queries.
    (stubs / "dpkg").write_text("#!/bin/sh\necho amd64\n", encoding="utf-8")

    # sudo: drops env var assignments and runs the command.
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

    # command: for command -v checks in the script.
    (stubs / "command").write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "-v" ]; then\n'
        "  shift\n"
        '  case "$1" in\n'
        "    apt-get) exit 0 ;;\n"
        "    node) exit 1 ;;  # node not found -> triggers the NodeSource upgrade\n"
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

    # Build the script: harness helpers, then the function, then a bare call.
    # log/warn are silent so the silent-exit path produces truly empty output;
    # die still reports to stderr for the clear-message assertion.
    script_parts = [
        "set -euo pipefail",
        # Disable the `command` builtin so `command -v` in the script resolves
        # to our stubs/command instead of doing a real PATH lookup (which
        # would find stubs/gpg even when gpg must be absent).
        "enable -n command",
        "log() { :; }",
        "warn() { :; }",
        "die()  { printf '[die] %s\\n' \"$*\" >&2; exit 1; }",
        'CURL_RETRY=(--retry 5 --retry-delay 3 --connect-timeout 15 --max-time 60)',
        'APT_RETRY=(-o Acquire::Retries=3)',
        "uname() { echo Linux; }",
        "lsb_release() { echo trixie; }",
        "dpkg() { echo amd64; }",
        # mktemp must CREATE the file it returns: the old script `mv`s the key
        # file right after mktemp, and a missing file would fail there instead
        # of at the gpg pipeline -- which is the bug this test proves.
        f"mktemp() {{ echo {tmp_path}/nodesource-key.XXXXXX; : > {tmp_path}/nodesource-key.XXXXXX; }}",
        source,
        "ensure_node22",
    ]
    script = "\n".join(script_parts).replace("/usr/share/keyrings", f"{fake}/usr/share/keyrings").replace("/etc/apt/sources.list.d", f"{fake}/etc/apt/sources.list.d")

    # Write the script to a file and run it, keeping argv small.
    script_path = tmp_path / "run.sh"
    script_path.write_text(script, encoding="utf-8")

    # Minimal PATH: stubs first, then filtered system commands (no real gpg).
    filtered_bin = tmp_path / "filtered_bin"
    filtered_bin.mkdir()
    for cmd in ("bash", "sh", "mkdir", "chmod", "tee", "mv", "rm", "awk", "head", "sed", "cut", "tr", "printf", "curl", "sudo", "lsb_release", "dpkg", "cat", "echo", "install", "ls", "cp", "sleep", "date", "uname", "id", "whoami"):
        src = shutil.which(cmd)
        if src:
            try:
                (filtered_bin / cmd).symlink_to(src)
            except FileExistsError:
                pass
    env = os.environ.copy()
    env["PATH"] = f"{stubs}:{filtered_bin}"

    proc = subprocess.run(
        ["bash", str(script_path)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    return proc.returncode, proc.stdout, proc.stderr


@pytest.mark.skipif(os.name != "posix", reason="bash-only test")
def test_ensure_node22_pre_pr_script_silent_exit(tmp_path):
    """The bug: pre-PR installer exits non-zero silently when gpg is missing."""
    rc, stdout, stderr = _run_ensure_node22(
        tmp_path,
        source=_extract_func(_install_script_source(True), "ensure_node22"),
        gpg_available=False,
    )

    assert rc != 0, (
        f"pre-PR ensure_node22 should exit non-zero without gpg; rc={rc} stderr={stderr}"
    )
    assert stdout == "", f"pre-PR ensure_node22 should produce no stdout; stdout={stdout!r}"
    assert stderr == "", f"pre-PR ensure_node22 should be silent; stderr={stderr!r}"
    assert "gpg (gnupg)" not in stderr
    assert "fingerprint mismatch" not in stderr


@pytest.mark.skipif(os.name != "posix", reason="bash-only test")
def test_ensure_node22_current_script_clear_message(tmp_path):
    """After the fix: non-zero exit with a clear message naming gnupg."""
    rc, stdout, stderr = _run_ensure_node22(
        tmp_path,
        source=_extract_func(_install_script_source(False), "ensure_node22"),
        gpg_available=False,
    )

    assert rc != 0, f"ensure_node22 should fail without gpg; stderr={stderr}"
    assert "gpg (gnupg)" in stderr, (
        f"Error message should mention 'gpg (gnupg)' clearly; stderr={stderr}"
    )
    assert "ensure_linux_deps should have installed it" in stderr, (
        f"Error message should reference ensure_linux_deps; stderr={stderr}"
    )


@pytest.mark.skipif(os.name != "posix", reason="bash-only test")
def test_ensure_node22_succeeds_with_gpg(tmp_path):
    """ensure_node22 should succeed when gpg is available."""
    rc, stdout, stderr = _run_ensure_node22(
        tmp_path,
        source=_extract_func(_install_script_source(False), "ensure_node22"),
        gpg_available=True,
    )
    assert rc == 0, f"ensure_node22 should succeed with gpg; stderr={stderr}"


@pytest.mark.skipif(os.name != "posix", reason="bash-only test")
def test_ensure_linux_deps_includes_gnupg():
    """ensure_linux_deps installs gnupg (apt) / gnupg2 (dnf) before NodeSource."""
    body = _extract_func(INSTALL_SCRIPT.read_text(), "ensure_linux_deps")

    apt = body[body.index("command -v apt-get") : body.index("command -v dnf")]
    assert "gnupg" in apt, f"apt path must install gnupg:\n{apt}"
    assert "pciutils" not in apt
    assert "lsb-release" not in apt

    dnf = body[body.index("command -v dnf") : body.index("command -v pacman")]
    assert "gnupg2" in dnf, f"dnf path must install gnupg2:\n{dnf}"
    assert "pciutils" not in dnf

    pacman = body[body.index("command -v pacman") : body.index("command -v apk")]
    assert "gnupg" in pacman
    assert "pciutils" not in pacman

    apk = body[body.index("command -v apk") :]
    assert "gnupg" in apk
    assert "pciutils" not in apk
