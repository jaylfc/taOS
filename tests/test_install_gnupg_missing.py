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

# Pre-PR ensure_node22 function from commit 88fe9372f (exec/tsk-cqdg43 base).
# Embedded as a fixture so the test does not depend on git history (shallow CI
# checkouts lack the object).
PRE_PR_ENSURE_NODE22 = r"""
ensure_node22() {
    local node_major=0
    if command -v node >/dev/null 2>&1; then
        node_major="$(node --version 2>/dev/null | sed 's/v//' | cut -d. -f1)"
    fi

    if [[ "$node_major" -ge 22 ]]; then
        log "node $(node --version) — ok (>=22 required)"
        return 0
    fi

    log "node ${node_major:-not found} is too old (need >=22) — upgrading to Node 22 LTS via NodeSource"

    if command -v apt-get >/dev/null 2>&1; then
        # Install Node 22 via NodeSource's GPG-signed apt repository.
        # This mirrors how Zabbly/Caddy keys are handled in this file: download
        # the key, verify its fingerprint against a known-good value, import to a
        # named keyring, then add the signed-by sources entry.
        #
        # NodeSource repo GPG key fingerprint — verified 2026-06-08 against
        # https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key
        # and confirmed against NodeSource's official documentation at
        # https://github.com/nodesource/distributions
        # Update if NodeSource rotates their signing key.
        local _ns_expected_fp="6F71F525282841EEDAF851B42F59B5F99B1BE0B4"
        local _ns_key_tmp
        _ns_key_tmp="$(mktemp /tmp/nodesource-key.XXXXXX)"
        mv -- "$_ns_key_tmp" "${_ns_key_tmp}.asc"
        _ns_key_tmp="${_ns_key_tmp}.asc"
        # shellcheck disable=SC2064
        trap "rm -f '$_ns_key_tmp'" RETURN
        curl -fsSL "${CURL_RETRY[@]}" https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
            -o "$_ns_key_tmp" \
            || die "failed to download NodeSource repo GPG key after retries -- check the network connection and re-run; re-running this installer is safe"
        local _ns_actual_fp
        _ns_actual_fp="$(gpg --with-colons --import-options show-only \
            --import "$_ns_key_tmp" 2>/dev/null \
            | awk -F: '/^fpr:/{print $10}' | head -1)"
        _ns_actual_fp="${_ns_actual_fp//[[:space:]]/}"
        if [[ "$_ns_actual_fp" != "$_ns_expected_fp" ]]; then
            die "NodeSource repo key fingerprint mismatch: expected $_ns_expected_fp, got '$_ns_actual_fp' — refusing to import"
        fi
        log "NodeSource key fingerprint ok (${_ns_actual_fp:0:16}…)"
        sudo mkdir -p /usr/share/keyrings
        sudo gpg --dearmor -o /usr/share/keyrings/nodesource.gpg < "$_ns_key_tmp" \
            || die "gpg --dearmor for NodeSource key failed"
        sudo chmod 644 /usr/share/keyrings/nodesource.gpg
        printf 'Types: deb\nURIs: https://deb.nodesource.com/node_22.x\nSuites: nodistro\nComponents: main\nSigned-By: /usr/share/keyrings/nodesource.gpg\n' \
            | sudo tee /etc/apt/sources.list.d/nodesource.sources > /dev/null
        sudo DEBIAN_FRONTEND=noninteractive apt-get "${APT_RETRY[@]}" update -qq \
            || die "apt-get update after NodeSource repo add failed"
        sudo DEBIAN_FRONTEND=noninteractive apt-get "${APT_RETRY[@]}" install -y nodejs \
            || die "apt-get install nodejs (22) failed"
    elif command -v dnf >/dev/null 2>&1; then
        # Install Node 22 via NodeSource's GPG-signed dnf repository.
        # Key fingerprint verified 2026-06-08 against
        # https://rpm.nodesource.com/gpgkey/ns-operations-public.key
        # Update if NodeSource rotates their RPM signing key.
        local _ns_rpm_expected_fp="242B813831AF09562B6C46F76B88DA4E3AF28A14"
        local _ns_rpm_key_tmp
        _ns_rpm_key_tmp="$(mktemp /tmp/nodesource-rpm-key.XXXXXX)"
        mv -- "$_ns_rpm_key_tmp" "${_ns_rpm_key_tmp}.asc"
        _ns_rpm_key_tmp="${_ns_rpm_key_tmp}.asc"
        # shellcheck disable=SC2064
        trap "rm -f '$_ns_rpm_key_tmp'" RETURN
        curl -fsSL "${CURL_RETRY[@]}" https://rpm.nodesource.com/gpgkey/ns-operations-public.key \
            -o "_ns_rpm_key_tmp" \
            || die "failed to download NodeSource RPM repo GPG key after retries -- check the network connection and re-run; re-running this installer is safe"
        local _ns_rpm_actual_fp
        _ns_rpm_actual_fp="$(gpg --with-colons --import-options show-only \
            --import "$_ns_rpm_key_tmp" 2>/dev/null \
            | awk -F: '/^fpr:/{print $10}' | head -1)"
        _ns_rpm_actual_fp="${_ns_rpm_actual_fp//[[:space:]]/}"
        if [[ "$_ns_rpm_actual_fp" != "$_ns_rpm_expected_fp" ]]; then
            die "NodeSource RPM key fingerprint mismatch: expected $_ns_rpm_expected_fp, got '$_ns_rpm_actual_fp' — refusing to import"
        fi
        log "NodeSource RPM key fingerprint ok (${_ns_rpm_actual_fp:0:16}…)"
        local _ns_rpm_arch
        _ns_rpm_arch="$(uname -m)"
        sudo rpm --import "$_ns_rpm_key_tmp" \
            || die "rpm --import for NodeSource key failed"
        printf '[nodesource-nodejs]\nname=Node.js Packages for Linux RPM based distros - %s\nbaseurl=https://rpm.nodesource.com/pub_22.x/nodistro/nodejs/%s\npriority=9\nenabled=1\ngpgcheck=1\ngpgkey=https://rpm.nodesource.com/gpgkey/ns-operations-public.key\nmodule_hotfixes=1\n' \
            "$_ns_rpm_arch" "$_ns_rpm_arch" \
            | sudo tee /etc/yum.repos.d/nodesource-nodejs.repo > /dev/null
        sudo dnf install -y nodejs \
            || die "dnf install nodejs (22) failed"
    elif command -v pacman >/dev/null 2>&1; then
        # Arch ships a current node in extra — just upgrade whatever is installed
        sudo pacman -Sy --noconfirm nodejs npm \
            || warn "pacman upgrade of nodejs failed — qmd install may fail on old Node"
    else
        warn "cannot auto-upgrade Node $node_major — unsupported package manager"
        warn "  install Node 22 manually then re-run this script"
        warn "  see: https://nodejs.org/en/download"
    fi

    # Re-check after upgrade
    if command -v node >/dev/null 2>&1; then
        log "node now at $(node --version)"
    fi
}
"""


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
        return PRE_PR_ENSURE_NODE22
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