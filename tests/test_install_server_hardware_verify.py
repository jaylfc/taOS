"""Gate: install-server.sh hardware verification must FAIL LOUD when the
controller endpoint returns no profile, and must POST (not GET) the endpoint.

Background (taOS #2): the installer used to call GET /api/system/hardware/refresh
and silently skip on any non-2xx response. On a fresh install where the
controller hasn't finished first-boot init yet, that means the user never learns
whether their NPU was recognised -- exactly the May regression where wizard NPU
detection did not fire the rkllama install.

The gate extracts verify_hardware_capabilities() verbatim from the real
install-server.sh so it exercises the production function (not a copy), points
curl at a stub that returns empty, and asserts the function dies non-zero
instead of returning 0 with a quiet warn.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "install-server.sh"


def _extract_verify_function() -> str:
    """Return the body of verify_hardware_capabilities() from install-server.sh,
    from the opening line through the matching closing brace. We deliberately
    work on the real script so a regression in the production function fails
    this gate."""
    text = SCRIPT.read_text()
    # Find the function header line and then walk braces to find the close.
    m = re.search(r"^verify_hardware_capabilities\(\)\s*\{", text, re.MULTILINE)
    assert m, "verify_hardware_capabilities() not found in install-server.sh"
    start = m.start()
    depth = 0
    i = m.end() - 1  # at the '{'
    while i < len(text):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
        i += 1
    raise AssertionError("could not find matching closing brace of verify_hardware_capabilities()")


def _write_wrapper(tmp_path: Path, curl_body: str, function_body: str) -> Path:
    wrapper = tmp_path / "wrapper.sh"
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        "set -u\n"
        "log()  { printf '[server-install] %s\\n' \"$*\"; }\n"
        "warn() { printf '[server-install] %s\\n' \"$*\" >&2; }\n"
        "die()  { printf '[server-install] %s\\n' \"$*\" >&2; exit 1; }\n"
        f"curl() {{\n{curl_body}\n}}\n"
        "TAOS_PORT=\"$TAOS_PORT\"\n"
        "os_name=\"$os_name\"\n"
        "HW_PROFILE_ID=\"${HW_PROFILE_ID:-unknown}\"\n"
        "HW_NPU_TYPE=\"${HW_NPU_TYPE:-none}\"\n"
        "HW_NPU_DEVICE=\"${HW_NPU_DEVICE:-}\"\n"
        + function_body
        + "\nverify_hardware_capabilities\n"
    )
    wrapper.chmod(0o755)
    return wrapper


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_verify_dies_loud_when_endpoint_returns_empty(tmp_path: Path) -> None:
    """The bug from #2: a controller that returns nothing must not be a quiet
    skip. The installer must die non-zero so the operator knows hardware
    detection did not actually run."""
    function_body = _extract_verify_function()
    wrapper = _write_wrapper(tmp_path, "    :", function_body)

    # The function retries for ~30 s when curl returns empty. Cap the wrapper's
    # own wall-clock so a regression that breaks the deadline doesn't hang the
    # test; in the fixed script the function exits quickly via die().
    result = subprocess.run(
        ["/usr/bin/env", "bash", str(wrapper)],
        env={**os.environ, "TAOS_PORT": "0", "os_name": "Linux"},
        capture_output=True,
        text=True,
        timeout=90,
    )

    assert result.returncode != 0, (
        "verify_hardware_capabilities returned 0 against an empty endpoint; "
        "this is the regression from taOS #2 -- the installer must fail loud "
        "instead of printing 'hardware verification skipped' and walking away.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    combined = (result.stdout + result.stderr).lower()
    assert "hardware" in combined, (
        f"failure output does not even mention hardware; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_verify_returns_clean_when_endpoint_serves_a_profile(tmp_path: Path) -> None:
    """Sanity: when the endpoint returns a non-empty profile, the function
    should NOT die on the empty-profile path (it may still warn about claimed
    accelerators, but the empty-curl branch must not fire)."""
    function_body = _extract_verify_function()
    profile = (
        '{"profile_id":"arm-npu-16gb",'
        '"npu":{"type":"rknpu","device":"RK3588","tops":6,"cores":3},'
        '"gpu":{"type":"none","model":"","vram_mb":0,"vulkan":false,"cuda":false,"rocm":false}}'
    )
    curl_body = f"    printf '%s' '{profile}'\n    return 0\n"
    wrapper = _write_wrapper(tmp_path, curl_body, function_body)

    result = subprocess.run(
        ["/usr/bin/env", "bash", str(wrapper)],
        env={**os.environ, "TAOS_PORT": "0", "os_name": "Linux"},
        capture_output=True,
        text=True,
        timeout=90,
    )

    combined = (result.stdout + result.stderr).lower()
    assert "did not return a hardware profile" not in combined, (
        "verify_hardware_capabilities treated a real profile as empty -- "
        "the empty-profile guard is firing too eagerly.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_install_script_uses_post_for_hardware_refresh() -> None:
    """The installer must POST /api/system/hardware/refresh. The route is
    registered as POST (see tinyagentos/routes/system.py:225); a GET will get
    a 405 and the old curl -sf would swallow it, returning nothing. That is
    the exact mechanism that produced taOS #2's silent skip."""
    text = SCRIPT.read_text()
    assert "curl -sf --max-time \"$_curl_timeout\" -X POST" in text and "hardware/refresh" in text, (
        "install-server.sh must POST /api/system/hardware/refresh with a "
        "bounded --max-time; a GET against a POST-only route silently returns "
        "405 and triggers the empty-profile skip from taOS #2."
    )
