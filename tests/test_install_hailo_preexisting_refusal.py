"""Gate: install-hailo.sh pre-existing hailo-ollama detection must exit 3
(refused) so callers can surface the conflict, and both install-server.sh and
install-worker.sh must branch on exit 3 with a message that names the :8000
conflict.

Background: PR #3002 (from @hognek, issue #2083) correctly added
detect_preexisting_hailoollama() but it exited 0, so both auto-install callers
(install-server.sh:655 and install-worker.sh:1080) stayed SILENT via their
`|| warn` chains. The operator was told the install succeeded while the machine
has a Hailo-10H, no taOS backend on 7836, no hailo-ollama.service, and the
upstream instance still on 8000 -- exactly the end state #2083 was filed about.

This gate extracts detect_preexisting_hailoollama() verbatim from the real
scripts/install-hailo.sh, stubs curl to return a "models" payload, and asserts
the function exits exactly 3. It also asserts the caller files contain a branch
keyed to status 3 whose message names the :8000 conflict.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HAILO_SCRIPT = REPO_ROOT / "scripts" / "install-hailo.sh"
SERVER_SCRIPT = REPO_ROOT / "scripts" / "install-server.sh"
WORKER_SCRIPT = REPO_ROOT / "scripts" / "install-worker.sh"
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


def _run_detection(
    tmp_path: Path,
    curl_body: str,
    after_call: str = "",
    path: str | None = None,
) -> subprocess.CompletedProcess[str]:
    function_body = _extract_function(HAILO_SCRIPT, "detect_preexisting_hailoollama")
    wrapper = tmp_path / "wrapper.sh"
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "log() { :; }\n"
        "warn() { printf '%s\\n' \"$*\" >&2; }\n"
        f"curl() {{\n{curl_body}\n}}\n"
        "HAILO_OLLAMA_PORT=7836\n"
        "HAILO_OLLAMA_DIR=\"$HOME/hailo_model_zoo_genai\"\n"
        + function_body
        + "\ndetect_preexisting_hailoollama\n"
        + after_call
    )
    wrapper.chmod(0o755)
    return subprocess.run(
        ["/usr/bin/env", "bash", str(wrapper)],
        env={
            "PATH": f"{path or '/usr/bin:/bin'}:{os.environ.get('PATH', '/usr/bin:/bin')}",
            "LANG": "C.UTF-8",
            "HOME": str(tmp_path),
        },
        capture_output=True,
        text=True,
        timeout=30,
    )


def _extract_detect_preexisting_function() -> str:
    """Return the body of detect_preexisting_hailoollama() from
    install-hailo.sh, from the opening line through the matching closing brace.
    We deliberately work on the real script so a regression in the production
    function fails this gate."""
    text = HAILO_SCRIPT.read_text()
    m = re.search(r"^detect_preexisting_hailoollama\(\)\s*\{", text, re.MULTILINE)
    assert m, "detect_preexisting_hailoollama() not found in install-hailo.sh"
    start = m.start()
    depth = 0
    i = m.end() - 1
    while i < len(text):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
        i += 1
    raise AssertionError("could not find matching closing brace of detect_preexisting_hailoollama()")


def _write_hailo_wrapper(tmp_path: Path, curl_body: str, function_body: str) -> Path:
    """Create a wrapper script that defines the extracted function and calls it,
    with a stubbed curl that returns the given body."""
    wrapper = tmp_path / "wrapper.sh"
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        "set -u\n"
        "log()  { printf '[hailo] %s\\n' \"$*\"; }\n"
        "warn() { printf '[hailo] %s\\n' \"$*\" >&2; }\n"
        "die()  { printf '[hailo] %s\\n' \"$*\" >&2; exit 1; }\n"
        f"curl() {{\n{curl_body}\n}}\n"
        "HAILO_OLLAMA_PORT=7836\n"
        + function_body
        + "\ndetect_preexisting_hailoollama\n"
    )
    wrapper.chmod(0o755)
    return wrapper


def _mock_systemctl_unit_without_marker(tmp_path: Path) -> None:
    """systemctl that reports hailo-ollama.service exists with a wrong marker."""
    mock = tmp_path / "systemctl"
    mock.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$*" == *"list-unit-files"* ]]; then\n'
        '    echo "hailo-ollama.service"\n'
        'elif [[ "$*" == *"cat hailo-ollama.service"* ]]; then\n'
        '    echo "OLLAMA_HOST=0.0.0.0:8000"\n'
        'fi\n'
    )
    mock.chmod(0o755)


def _mock_systemctl_unit_no_marker(tmp_path: Path) -> None:
    """systemctl that reports hailo-ollama.service exists but has NO OLLAMA_HOST marker."""
    mock = tmp_path / "systemctl"
    mock.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$*" == *"list-unit-files"* ]]; then\n'
        '    echo "hailo-ollama.service"\n'
        'elif [[ "$*" == *"cat hailo-ollama.service"* ]]; then\n'
        '    echo "[Unit]"\n'
        '    echo "Description=Upstream hailo-ollama"\n'
        '    echo "[Service]"\n'
        '    echo "ExecStart=hailo-ollama"\n'
        'fi\n'
    )
    mock.chmod(0o755)


def _mock_systemctl_unit_with_marker(tmp_path: Path) -> None:
    """systemctl that reports hailo-ollama.service exists WITH the taOS marker."""
    mock = tmp_path / "systemctl"
    mock.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$*" == *"list-unit-files"* ]]; then\n'
        '    echo "hailo-ollama.service"\n'
        'elif [[ "$*" == *"cat hailo-ollama.service"* ]]; then\n'
        '    echo "OLLAMA_HOST=127.0.0.1:7836"\n'
        'fi\n'
    )
    mock.chmod(0o755)


def _mock_hailo_ollama_binary_outside_dir(tmp_path: Path) -> None:
    """Place a hailo-ollama binary on PATH that resolves outside the install dir."""
    binary = tmp_path / "hailo-ollama"
    binary.write_text("#!/usr/bin/env bash\necho running\n")
    binary.chmod(0o755)


def _mock_hailo_ollama_binary_inside_dir(tmp_path: Path) -> None:
    """Place a hailo-ollama binary inside the install directory."""
    install_dir = tmp_path / "hailo_model_zoo_genai" / "bin"
    install_dir.mkdir(parents=True)
    binary = install_dir / "hailo-ollama"
    binary.write_text("#!/usr/bin/env bash\necho running\n")
    binary.chmod(0o755)


def _write_caller_wrapper(
    tmp_path: Path,
    function_body: str,
    call_line: str,
) -> Path:
    """Create a wrapper script that defines log/warn, sources the extracted
    caller function, and invokes it."""
    wrapper = tmp_path / "caller_wrapper.sh"
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "log(){ echo \"LOG $*\"; }\n"
        "warn(){ echo \"WARN $*\"; }\n"
        + function_body
        + "\n"
        + call_line
        + "\n"
        + "echo CONTINUED\n"
    )
    wrapper.chmod(0o755)
    return wrapper


def _run_caller(wrapper: Path, env: dict) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(wrapper)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _assert_exit_3_conflict(result: subprocess.CompletedProcess[str]) -> None:
    assert "7836" in result.stdout, (
        "exit-3 must print the conflict message mentioning 7836; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "CONTINUED" in result.stdout


def _assert_exit_1_generic(result: subprocess.CompletedProcess[str]) -> None:
    assert "install-hailo.sh failed - continuing" in result.stdout, (
        "exit-1 must print the generic failure message; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "CONTINUED" in result.stdout


def _assert_exit_0_no_warn(result: subprocess.CompletedProcess[str]) -> None:
    assert "7836" not in result.stdout
    assert "install-hailo.sh failed - continuing" not in result.stdout
    assert "CONTINUED" in result.stdout


# --- detect_preexisting_hailoollama behavioural tests -------------------------

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


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_preexisting_detected_exits_3_not_0(tmp_path: Path) -> None:
    """When a pre-existing hailo-ollama answers on :8000 with a "models"
    payload, the function must exit 3 (refused), not 0 (success). Exit 0 is
    the bug from PR #3002 that made auto-install callers silent."""
    function_body = _extract_detect_preexisting_function()
    curl_body = '    printf \'{"models":[{"name":"llama3.2:3b"}]}\'\n    return 0\n'
    wrapper = _write_hailo_wrapper(tmp_path, curl_body, function_body)

    result = subprocess.run(
        ["/usr/bin/env", "bash", str(wrapper)],
        env={**os.environ},
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 3, (
        f"detect_preexisting_hailoollama returned {result.returncode} "
        f"when pre-existing instance found; must be exactly 3 (refused), "
        f"not 0 (silent success) or 1 (generic failure).\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    combined = (result.stdout + result.stderr).lower()
    assert "pre-existing" in combined or "8000" in combined, (
        f"output does not mention pre-existing or 8000; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_nothing_on_8000_returns_0_proceeds(tmp_path: Path) -> None:
    """When nothing answers on :8000, the function must return 0 (not exit)
    so the install proceeds normally."""
    function_body = _extract_detect_preexisting_function()
    curl_body = "    return 1\n"
    wrapper = _write_hailo_wrapper(tmp_path, curl_body, function_body)

    result = subprocess.run(
        ["/usr/bin/env", "bash", str(wrapper)],
        env={**os.environ},
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, (
        f"detect_preexisting_hailoollama returned {result.returncode} "
        f"when nothing on 8000; must return 0 to let install proceed.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_preexisting_unit_without_marker_refuses(tmp_path: Path) -> None:
    """An upstream unit with a wrong OLLAMA_HOST marker must refuse with exit 3."""
    _mock_systemctl_unit_without_marker(tmp_path)
    result = _run_detection(
        tmp_path,
        "    return 1\n",
        path=str(tmp_path),
    )
    assert result.returncode == 3, (
        f"unit with wrong marker must refuse with exit 3; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    combined = (result.stdout + result.stderr).lower()
    assert "upstream" in combined or "marker" in combined, (
        f"refusal message must mention upstream or marker; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_upstream_unit_without_marker_is_refused(tmp_path: Path) -> None:
    """An upstream hailo-ollama.service with NO OLLAMA_HOST marker must refuse with exit 3.

    This is the bug from #3201: the detection code only checks if the marker is WRONG,
    but not if the marker is MISSING. An upstream unit without any OLLAMA_HOST line
    was accepted as our own install (rc 0) instead of being refused (rc 3).
    """
    _mock_systemctl_unit_no_marker(tmp_path)
    result = _run_detection(
        tmp_path,
        "    return 1\n",
        path=str(tmp_path),
    )
    assert result.returncode == 3, (
        f"upstream unit without marker must refuse with exit 3; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    combined = (result.stdout + result.stderr).lower()
    assert "upstream" in combined or "marker" in combined, (
        f"refusal message must mention upstream or marker; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_preexisting_binary_outside_install_dir_refuses(tmp_path: Path) -> None:
    """An upstream hailo-ollama binary outside the install directory must refuse."""
    _mock_hailo_ollama_binary_outside_dir(tmp_path)
    result = _run_detection(
        tmp_path,
        "    return 1\n",
        path=str(tmp_path),
    )
    assert result.returncode == 3, (
        f"binary outside install dir must refuse with exit 3; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    combined = (result.stdout + result.stderr).lower()
    assert "upstream" in combined or "binary" in combined, (
        f"refusal message must mention upstream or binary; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_our_own_install_without_markers_allowed(tmp_path: Path) -> None:
    """Our own install (unit with correct marker + binary inside dir) must be allowed."""
    _mock_systemctl_unit_with_marker(tmp_path)
    _mock_hailo_ollama_binary_inside_dir(tmp_path)
    result = _run_detection(
        tmp_path,
        "    return 1\n",
        path=str(tmp_path),
    )
    assert result.returncode == 0, (
        f"our own install must be allowed (exit 0); "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


# --- caller behavioural tests -------------------------------------------------

_CALLER_CONFIGS = {
    "controller": {
        "script": SERVER_SCRIPT,
        "function": "install_hailo_if_pending",
        "call_line": lambda tmp: (
            f"HAILO_PENDING_INSTALL=1 INSTALL_DIR={tmp} install_hailo_if_pending"
        ),
        "extra_env": {},
    },
    "worker": {
        "script": WORKER_SCRIPT,
        "function": "chain_hailo_installer",
        "call_line": lambda tmp: (
            f"chain_hailo_installer {tmp}/scripts/install-hailo.sh"
        ),
        "extra_env": {},
    },
}


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
@pytest.mark.parametrize("caller,exit_code", [
    ("controller", 3),
    ("controller", 1),
    ("controller", 0),
    ("worker", 3),
    ("worker", 1),
    ("worker", 0),
])
def test_caller_exit_codes(
    tmp_path: Path, caller: str, exit_code: int
) -> None:
    """Each installer caller must branch correctly on exit codes 3, 1, and 0
    from install-hailo.sh, running the real function against a stubbed script."""
    config = _CALLER_CONFIGS[caller]

    hailo_script = tmp_path / "scripts" / "install-hailo.sh"
    hailo_script.parent.mkdir(parents=True)
    hailo_script.write_text(f"#!/usr/bin/env bash\nexit {exit_code}\n")
    hailo_script.chmod(0o755)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    sudo_stub = bin_dir / "sudo"
    sudo_stub.write_text(
        "#!/usr/bin/sh\n"
        '[ "$1" = -E ] && shift\n'
        'exec "$@"\n'
    )
    sudo_stub.chmod(0o755)

    function_body = _extract_function(config["script"], config["function"])
    call_line = config["call_line"](tmp_path)
    wrapper = _write_caller_wrapper(
        tmp_path,
        function_body,
        call_line,
    )
    env = {
        "PATH": str(bin_dir) + ":/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "HOME": str(tmp_path),
        **config["extra_env"],
    }
    result = _run_caller(wrapper, env)

    if exit_code == 3:
        _assert_exit_3_conflict(result)
    elif exit_code == 1:
        _assert_exit_1_generic(result)
    else:
        _assert_exit_0_no_warn(result)


# --- misc ---------------------------------------------------------------------

def test_probe_url_uses_localhost_not_0000() -> None:
    """The probe URL in detect_preexisting_hailoollama must use
    http://localhost:8000/api/tags, not http://0.0.0.0:8000/api/tags.
    0.0.0.0 is a BIND address; localhost is correct for a client probe."""
    text = HAILO_SCRIPT.read_text()
    assert "http://localhost:8000/api/tags" in text, (
        "detect_preexisting_hailoollama must probe http://localhost:8000/api/tags "
        "(found http://0.0.0.0:8000/api/tags which is a bind address, not a client URL)"
    )
    assert "http://0.0.0.0:8000/api/tags" not in text, (
        "detect_preexisting_hailoollama still uses http://0.0.0.0:8000/api/tags; "
        "must be http://localhost:8000/api/tags"
    )
