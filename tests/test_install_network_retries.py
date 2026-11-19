from __future__ import annotations

import os
import subprocess
from pathlib import Path
from textwrap import dedent

INSTALL = Path("scripts/install-server.sh")


def test_pip_install_has_retries():
    text = INSTALL.read_text()
    pip_lines = [line for line in text.splitlines() if "./.venv/bin/pip install" in line]
    assert len(pip_lines) >= 2, f"expected >=2 pip install lines, got {len(pip_lines)}"
    for line in pip_lines:
        assert "--retries" in line, f"missing --retries in: {line}"


def _find_curl_for_url(text: str, url: str) -> str:
    """Find the curl invocation that fetches the given URL.
    Returns the line containing the curl command (including ${CURL_RETRY[@]})."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if "curl " not in line:
            continue
        if url in line:
            return line
        # Handle continuation lines where URL is on next line
        if i + 1 < len(lines) and url in lines[i + 1]:
            # Found the line with curl, return it (it should have ${CURL_RETRY[@]})
            return line
    return ""


def test_key_download_curls_have_retries():
    text = INSTALL.read_text()

    # NodeSource deb key curl
    deb_curl = _find_curl_for_url(text, "https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key")
    assert "${CURL_RETRY[@]}" in deb_curl, f"missing CURL_RETRY in deb curl: {deb_curl}"

    # NodeSource rpm key curl
    rpm_curl = _find_curl_for_url(text, "https://rpm.nodesource.com/gpgkey/ns-operations-public.key")
    assert "${CURL_RETRY[@]}" in rpm_curl, f"missing CURL_RETRY in rpm curl: {rpm_curl}"

    # Zabbly key curl
    zabbly_curl = _find_curl_for_url(text, "https://pkgs.zabbly.com/key.asc")
    assert "${CURL_RETRY[@]}" in zabbly_curl, f"missing CURL_RETRY in zabbly curl: {zabbly_curl}"

    # Docker key curl
    docker_curl = _find_curl_for_url(text, "https://download.docker.com/linux/$docker_os/gpg")
    assert "${CURL_RETRY[@]}" in docker_curl, f"missing CURL_RETRY in docker curl: {docker_curl}"


def _write_curl(path: Path, help_output: str) -> Path:
    path.mkdir(exist_ok=True)
    curl = path / "curl"
    content = dedent(
        f"""
        #!/bin/sh
        if [ "$1" = "--help" ] && [ "$2" = "all" ]; then
            printf '%s\\n' "{help_output}"
            exit 0
        fi
        for arg in "$@"; do
            if [ "$arg" = "--retry-all-errors" ]; then
                exit 2
            fi
        done
        exit 0
        """
    )
    curl.write_text(content.lstrip("\n"))
    curl.chmod(0o755)
    return path


def _extract_curl_retry_block(text: str) -> str:
    """Extract the CURL_RETRY block from install-server.sh text."""
    lines = text.splitlines()
    block_lines = []
    in_block = False
    
    for line in lines:
        if line.strip().startswith("CURL_RETRY=("):
            in_block = True
            block_lines.append(line)
        elif in_block:
            block_lines.append(line)
            if line.strip() == "fi":
                break
    
    assert block_lines, "CURL_RETRY=( block not found in install-server.sh"
    return "\n".join(block_lines)


def _curl_commands(text: str):
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if "curl " in line and not line.strip().startswith("#") and "curl --help" not in line:
            parts = [line]
            while parts[-1].rstrip().endswith("\\") and i + 1 < len(lines):
                i += 1
                parts.append(lines[i])
            yield "\n".join(parts)
        i += 1


def test_no_unconditional_retry_all_errors():
    """No complete continued `curl` command in install-server.sh contains a
    literal `--retry-all-errors`. Only the guarded
    `CURL_RETRY+=(--retry-all-errors)` line may carry the flag."""
    text = INSTALL.read_text()
    offenders = [cmd for cmd in _curl_commands(text) if "--retry-all-errors" in cmd]
    assert offenders == [], f"curl commands with an unconditional --retry-all-errors: {offenders}"
    guarded = [line for line in text.splitlines() if "--retry-all-errors" in line and line.strip().startswith("CURL_RETRY+=(")]
    assert len(guarded) == 1, f"expected exactly one guarded CURL_RETRY+=(--retry-all-errors) line, got {guarded}"


def test_curl_retry_without_retry_all_errors(tmp_path):
    bin_path = _write_curl(tmp_path, "curl 7.68.0\nOptions:\n  --retry <num>")
    env = os.environ.copy()
    env["PATH"] = f"{bin_path}:{env['PATH']}"
    
    # Use the REAL CURL_RETRY block from install-server.sh
    curl_retry_block = _extract_curl_retry_block(INSTALL.read_text())
    
    result = subprocess.run(
        ["/bin/bash", "-c", f"set -euo pipefail; {curl_retry_block}; echo \"${{CURL_RETRY[*]}}\""],
        cwd=str(Path.cwd()),
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    curl_retry = result.stdout.strip().split()
    assert "--retry-all-errors" not in curl_retry


def test_curl_retry_with_retry_all_errors(tmp_path):
    bin_path = _write_curl(tmp_path, "curl 7.88.1\nOptions:\n  --retry <num>\n  --retry-all-errors")
    env = os.environ.copy()
    env["PATH"] = f"{bin_path}:{env['PATH']}"
    
    # Use the REAL CURL_RETRY block from install-server.sh (which will include --retry-all-errors for curl >= 7.71)
    curl_retry_block = _extract_curl_retry_block(INSTALL.read_text())
    
    result = subprocess.run(
        ["/bin/bash", "-c", f"set -euo pipefail; {curl_retry_block}; echo \"${{CURL_RETRY[*]}}\""],
        cwd=str(Path.cwd()),
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    curl_retry = result.stdout.strip().split()
    assert "--retry-all-errors" in curl_retry


def _apt_get_fetch_lines(text: str) -> list[str]:
    """Command lines that make apt-get download something (update/install).

    Skips comments and lines where apt-get only appears inside a
    warn/log/echo/die string (e.g. the 'run: sudo apt-get install ...'
    advice in a failure message). The retry insertion is normalised
    out before matching, so the same lines are found with and without
    the fix.
    """
    out = []
    for line in text.splitlines():
        if line.strip().startswith("#"):
            continue
        idx = line.find("apt-get")
        if idx == -1:
            continue
        prefix = line[:idx]
        if 'warn "' in prefix or 'log "' in prefix or 'echo "' in prefix or 'die "' in prefix:
            continue
        norm = line.replace('apt-get "${APT_RETRY[@]}" ', "apt-get ")
        norm = norm.replace("apt-get -o Acquire::Retries=3 ", "apt-get ")
        if "apt-get update" in norm or "apt-get install" in norm:
            out.append(line)
    return out


def test_apt_get_fetches_have_acquire_retries():
    """#2088 point 3: a .deb download dropped mid-transfer must be
    retried inside the apt run (Acquire::Retries), not fail the whole
    install. Every apt-get update/install command line carries the
    retry option, via the shared APT_RETRY array or (in the pre-bash
    bootstrap, which runs under sh before the array exists) the
    literal option."""
    text = INSTALL.read_text()
    lines = _apt_get_fetch_lines(text)
    assert len(lines) >= 15, f"expected >=15 apt-get fetch lines, got {len(lines)}"
    for line in lines:
        assert "${APT_RETRY[@]}" in line or "Acquire::Retries" in line, (
            f"apt-get fetch without download retries: {line.strip()}"
        )


def test_pip_deps_install_is_retried_as_a_whole():
    """#2088 point 1: pip --retries covers connection setup, but a wheel
    truncated mid-body (IncompleteRead) still aborts the pip run, so the
    controller-deps pip pair is wrapped in the script's retry helper."""
    text = INSTALL.read_text()
    assert "retry 3 _install_controller_deps" in text
    lines = text.splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("_install_controller_deps() {"))
    block = []
    for line in lines[start:]:
        block.append(line)
        if line == "}":
            break
    pip_cmds = [l for l in block if "./.venv/bin/pip install" in l]
    assert len(pip_cmds) == 2, f"expected the pip pair inside _install_controller_deps, got {pip_cmds}"


def _extract_retry_fn(text: str) -> str:
    lines = text.splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("retry() {"))
    block = []
    for line in lines[start:]:
        block.append(line)
        if line == "}":
            break
    return "\n".join(block)


def _run_retry_harness(body: str) -> subprocess.CompletedProcess:
    fn = _extract_retry_fn(INSTALL.read_text())
    script = "\n".join([
        "set -uo pipefail",
        "warn() { :; }",
        "sleep() { :; }",
        fn,
        body,
    ])
    return subprocess.run(["/bin/bash", "-c", script], text=True, capture_output=True)


def test_retry_helper_recovers_from_transient_failures(tmp_path):
    """The flaky command fails the way pip fails on a dropped download
    (IncompleteRead in its output) and succeeds on the third attempt."""
    body = "\n".join([
        f"count_file='{tmp_path}/n'",
        'echo 0 > "$count_file"',
        'flaky() { local n; n=$(cat "$count_file"); n=$((n+1)); echo "$n" > "$count_file"; if [ "$n" -lt 3 ]; then echo "ERROR: IncompleteRead(1234 bytes read, 5678 more expected)" >&2; return 1; fi; }',
        "retry 3 flaky || exit $?",
        'echo "attempts=$(cat "$count_file")"',
    ])
    result = _run_retry_harness(body)
    assert result.returncode == 0, result.stderr
    assert "attempts=3" in result.stdout


def test_retry_helper_gives_up_after_the_bound(tmp_path):
    """A command that keeps failing with a network-drop marker
    (ProtocolError) is attempted exactly the bounded number of times."""
    body = "\n".join([
        f"count_file='{tmp_path}/n'",
        'echo 0 > "$count_file"',
        'always_fail() { local n; n=$(cat "$count_file"); n=$((n+1)); echo "$n" > "$count_file"; echo "ERROR: ProtocolError: connection aborted" >&2; return 1; }',
        "retry 3 always_fail || rc=$?",
        'echo "rc=${rc:-0} attempts=$(cat "$count_file")"',
    ])
    result = _run_retry_harness(body)
    assert result.returncode == 0, result.stderr
    assert "rc=1 attempts=3" in result.stdout


def test_retry_helper_retries_network_drop_failures_the_full_count(tmp_path):
    """A failure whose output carries a network-drop marker is retried
    up to the bound before the helper gives up. The marker here is the
    lowercase 'connection broken', pinning case-insensitive matching."""
    body = "\n".join([
        f"count_file='{tmp_path}/n'",
        'echo 0 > "$count_file"',
        'dropped() { local n; n=$(cat "$count_file"); n=$((n+1)); echo "$n" > "$count_file"; echo "error: connection broken by peer while downloading" >&2; return 1; }',
        "retry 3 dropped || rc=$?",
        'echo "rc=${rc:-0} attempts=$(cat "$count_file")"',
    ])
    result = _run_retry_harness(body)
    assert result.returncode == 0, result.stderr
    assert "rc=1 attempts=3" in result.stdout


def test_retry_helper_fails_fast_on_non_network_failures(tmp_path):
    """A failure whose output shows a real error rather than a network
    drop (here a pip dependency conflict) must run exactly once:
    re-running pip cannot fix a conflict, it only delays the real
    error behind two pointless retries."""
    body = "\n".join([
        f"count_file='{tmp_path}/n'",
        'echo 0 > "$count_file"',
        'conflict() { local n; n=$(cat "$count_file"); n=$((n+1)); echo "$n" > "$count_file"; echo "ERROR: Cannot install pkg-a and pkg-b because these package versions have conflicting dependencies." >&2; return 1; }',
        "retry 3 conflict || rc=$?",
        'echo "rc=${rc:-0} attempts=$(cat "$count_file")"',
    ])
    result = _run_retry_harness(body)
    assert result.returncode == 0, result.stderr
    assert "rc=1 attempts=1" in result.stdout
