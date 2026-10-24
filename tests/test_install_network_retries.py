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


def test_no_unconditional_retry_all_errors():
    """No line of install-server.sh that contains `curl ` also contains a literal `--retry-all-errors`.
    Only the guarded `CURL_RETRY+=(--retry-all-errors)` line may carry the flag."""
    text = INSTALL.read_text()
    offenders = [
        line for line in text.splitlines()
        if not line.strip().startswith("#")
        and "curl " in line
        and "curl --help" not in line
        and "--retry-all-errors" in line
    ]
    assert offenders == [], f"curl lines with an unconditional --retry-all-errors: {offenders}"
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
