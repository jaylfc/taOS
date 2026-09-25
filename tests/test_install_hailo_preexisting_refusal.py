"""Gate the Hailo pre-install conflict refusal and both auto-install callers."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HAILO_SCRIPT = REPO_ROOT / "scripts" / "install-hailo.sh"
CALLERS = {
    "controller": REPO_ROOT / "scripts" / "install-server.sh",
    "worker": REPO_ROOT / "scripts" / "install-worker.sh",
}


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


def _run_detection(tmp_path: Path, curl_body: str, after_call: str = "") -> subprocess.CompletedProcess[str]:
    function_body = _extract_function(HAILO_SCRIPT, "detect_preexisting_hailoollama")
    wrapper = tmp_path / "wrapper.sh"
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "log() { :; }\n"
        "warn() { printf '%s\\n' \"$*\" >&2; }\n"
        f"curl() {{\n{curl_body}\n}}\n"
        "HAILO_OLLAMA_PORT=7836\n"
        + function_body
        + "\ndetect_preexisting_hailoollama\n"
        + after_call
    )
    wrapper.chmod(0o755)
    return subprocess.run(
        ["/usr/bin/env", "bash", str(wrapper)],
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_preexisting_instance_refuses_with_exit_3(tmp_path: Path) -> None:
    """A live upstream tags endpoint must refuse with the reserved status 3."""
    result = _run_detection(
        tmp_path,
        "    printf '%s' '{\"models\":[]}'\n",
    )

    assert result.returncode == 3, (
        "pre-existing hailo-ollama must exit 3, not silently report success; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_no_instance_on_8000_allows_install_to_proceed(tmp_path: Path) -> None:
    """An unanswered upstream probe is a clean no-op and must not block setup."""
    result = _run_detection(
        tmp_path,
        "    return 1\n",
        "printf 'install proceeds\\n'",
    )

    assert result.returncode == 0, f"unexpected probe failure: {result.stderr}"
    assert "install proceeds" in result.stdout, (
        "a clean :8000 probe must allow the installer to continue; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


def _assert_caller_reports_conflict(script: Path, caller: str) -> None:
    text = script.read_text()
    branch = re.compile(
        r"if \(\( rc == 3 \)\); then\s*"
        r"warn \"[^\"]*pre-existing hailo-ollama on :8000[^\"]*"
        r"taOS backend not installed on 7836[^\"]*\""
    )
    assert branch.search(text), (
        f"{caller} must branch on exit status 3 and name the :8000 conflict; "
        "the generic failure warning alone leaves the auto-install silence bug uncovered"
    )
    assert 'warn "install-hailo.sh failed - continuing ' in text, (
        f"{caller} must retain its generic warning for non-3 failures"
    )


def test_install_server_reports_hailo_conflict() -> None:
    _assert_caller_reports_conflict(CALLERS["controller"], "install-server.sh")


def test_install_worker_reports_hailo_conflict() -> None:
    _assert_caller_reports_conflict(CALLERS["worker"], "install-worker.sh")
