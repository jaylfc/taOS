"""macOS worker install: Apple Silicon registers as `gpu-metal`.

Gates issue #37 — the macOS branch of ``scripts/install-worker.sh`` and the
worker-side resource contract it feeds:

  * Apple Silicon (arm64 + Metal) registers the worker as ``gpu-metal``
    (docs/design/resource-scheduler.md resource table), with MLX probed only
    to advise on GPU-inference-on-MLX availability.
  * Intel Macs (and arm64 hosts whose Metal probe answers nothing, e.g. a VM)
    fall back to ``cpu-inference``.
  * The detected classes are baked into the launchd plist written under
    ``~/Library/LaunchAgents`` so the registration survives a re-login.
  * ``tinyagentos.worker.agent`` advertises the installer-detected classes and
    reports Apple Silicon as ``gpu-metal`` rather than the CUDA class.

The install-script cases run the production shell functions in a simulated
environment (stubbed ``uname``/``system_profiler``/``sysctl``/``python3`` and a
throwaway ``$HOME``), the same technique
``tests/test_install_hailo_preexisting_refusal.py`` uses. The functions are
extracted from the script, so the assertions exercise the shipped code, not a
copy of it.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SCRIPT = REPO_ROOT / "scripts" / "install-worker.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="bash required to exercise the installer"
)


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


def _run_wrapper(tmp_path: Path, body: str, env: dict[str, str] | None = None):
    wrapper = tmp_path / "wrapper.sh"
    wrapper.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body)
    wrapper.chmod(0o755)
    child_env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
    if env:
        child_env.update(env)
    return subprocess.run(
        ["/usr/bin/env", "bash", str(wrapper)],
        env=child_env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _detection_wrapper(
    tmp_path: Path,
    mach: str,
    sp_output: str,
    mlx_present: bool,
    sp_available: bool = True,
) -> str:
    """Build a wrapper that runs the shipped detect_macos_accelerator()."""
    functions = "\n".join(
        _extract_function(INSTALL_SCRIPT, name)
        for name in ("macos_metal_available", "macos_mlx_available", "detect_macos_accelerator")
    )
    # When sp_available is False the stub is omitted entirely, so
    # `command -v system_profiler` fails exactly as it would on a trimmed
    # image (the child runs with PATH=/usr/bin:/bin).
    sp_stub = (
        f"system_profiler() {{ printf '%s\\n' {sp_output!r}; }}\n" if sp_available else ""
    )
    return (
        "log() { printf '%s\\n' \"$*\"; }\n"
        "warn() { printf '%s\\n' \"$*\" >&2; }\n"
        f"INSTALL_DIR={tmp_path}/install\n"
        f'uname() {{ printf \'%s\\n\' "{mach}"; }}\n'
        + sp_stub
        + f"python3() {{ return {0 if mlx_present else 1}; }}\n"
        + functions
        + "\ndetect_macos_accelerator\n"
        + "printf 'RESULT_MACOS=%s\\n' \"$TAOS_MACOS_RESOURCE\"\n"
        + "printf 'RESULT_RESOURCES=%s\\n' \"$TAOS_WORKER_RESOURCES\"\n"
    )


def _result(stdout: str, key: str) -> str:
    match = re.search(rf"^{key}=(.*)$", stdout, re.MULTILINE)
    assert match, f"{key} missing from wrapper output:\n{stdout}"
    return match.group(1)


# --- detection: Apple Silicon → gpu-metal ---------------------------------


def test_apple_silicon_with_metal_registers_gpu_metal(tmp_path: Path) -> None:
    result = _run_wrapper(
        tmp_path,
        _detection_wrapper(
            tmp_path, mach="arm64", sp_output="Metal Support: Metal 3", mlx_present=True
        ),
    )
    assert result.returncode == 0, result.stderr
    assert _result(result.stdout, "RESULT_MACOS") == "gpu-metal"
    assert _result(result.stdout, "RESULT_RESOURCES") == "gpu-metal,cpu-inference"
    assert "resource registration: gpu-metal" in result.stdout, result.stdout
    assert "Apple Silicon (arm64): Metal available" in result.stdout, result.stdout
    assert "MLX runtime detected" in result.stdout, result.stdout


def test_apple_silicon_without_mlx_still_registers_gpu_metal(tmp_path: Path) -> None:
    """MLX is optional: Metal-backed llama.cpp/Core ML still use the GPU class."""
    result = _run_wrapper(
        tmp_path,
        _detection_wrapper(
            tmp_path, mach="arm64", sp_output="Metal Support: Metal 3", mlx_present=False
        ),
    )
    assert result.returncode == 0, result.stderr
    assert _result(result.stdout, "RESULT_RESOURCES") == "gpu-metal,cpu-inference"
    assert "MLX runtime not found" in result.stderr, result.stderr
    # The hint has to name a package that exists and the interpreter the
    # service actually runs (#3237 installs mlx-lm into the service venv;
    # a bare `pip install mlx` with system python does not reach the daemon).
    assert "pip install mlx-lm" in result.stderr, result.stderr
    assert f"{tmp_path}/install/.venv/bin/pip install mlx-lm" in result.stderr, result.stderr


def test_arm64_without_metal_falls_back_to_cpu(tmp_path: Path) -> None:
    """A VM can report arm64 with no Metal-capable GPU; do not claim gpu-metal."""
    result = _run_wrapper(
        tmp_path,
        _detection_wrapper(
            tmp_path, mach="arm64", sp_output="Chipset Model: Virtual Display", mlx_present=True
        ),
    )
    assert result.returncode == 0, result.stderr
    assert _result(result.stdout, "RESULT_MACOS") == "cpu-inference"
    assert _result(result.stdout, "RESULT_RESOURCES") == "cpu-inference"
    assert "gpu-metal" not in _result(result.stdout, "RESULT_RESOURCES")


def test_intel_mac_falls_back_to_cpu_only(tmp_path: Path) -> None:
    result = _run_wrapper(
        tmp_path,
        _detection_wrapper(
            tmp_path, mach="x86_64", sp_output="Chipset Model: Intel Iris Plus", mlx_present=False
        ),
    )
    assert result.returncode == 0, result.stderr
    assert _result(result.stdout, "RESULT_MACOS") == "cpu-inference"
    assert _result(result.stdout, "RESULT_RESOURCES") == "cpu-inference"
    assert "Intel Mac" in result.stdout, result.stdout
    assert "resource registration: cpu-inference" in result.stdout, result.stdout


def test_force_metal_overrides_a_silent_probe(tmp_path: Path) -> None:
    """TAOS_FORCE_METAL=1 is the bench-box escape hatch (mirrors TAOS_FORCE_HAILO)."""
    result = _run_wrapper(
        tmp_path,
        _detection_wrapper(
            tmp_path, mach="arm64", sp_output="Chipset Model: Apple M2", mlx_present=False
        ),
        env={"TAOS_FORCE_METAL": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert _result(result.stdout, "RESULT_MACOS") == "gpu-metal"


def test_metal_support_unsupported_is_not_treated_as_metal(tmp_path: Path) -> None:
    """`grep -i metal` would read "Metal Support: Unsupported" as support.

    CodeRabbit flagged the bare substring match on the second revision: a GPU
    the OS cannot drive reports Unsupported, and registering it as gpu-metal
    would be wrong. arm64 is deliberate — on x86_64 the arch check short-
    circuits before the probe, so the test would pass without exercising the
    regex at all (CodeRabbit nitpick on the third revision). Verified
    discriminating: against a scratch copy of the script carrying the old bare
    `grep -qi 'metal'`, this test fails with 'gpu-metal' != 'cpu-inference'.
    """
    result = _run_wrapper(
        tmp_path,
        _detection_wrapper(
            tmp_path,
            mach="arm64",
            sp_output="Metal Support: Unsupported",
            mlx_present=False,
        ),
    )
    assert result.returncode == 0, result.stderr
    assert _result(result.stdout, "RESULT_MACOS") == "cpu-inference"
    assert "gpu-metal" not in _result(result.stdout, "RESULT_RESOURCES")


def test_metal_support_with_a_version_is_accepted(tmp_path: Path) -> None:
    """The affirmative form is `Metal Support: Metal 3` — do not over-anchor."""
    result = _run_wrapper(
        tmp_path,
        _detection_wrapper(
            tmp_path, mach="arm64", sp_output="Metal Support: Metal 3", mlx_present=True
        ),
    )
    assert _result(result.stdout, "RESULT_MACOS") == "gpu-metal"


def test_unverifiable_metal_probe_falls_back_to_cpu(tmp_path: Path) -> None:
    """No system_profiler means the Metal probe cannot be trusted either way.

    An arm64 test VM with a trimmed image must not be registered as gpu-metal
    on the strength of `hw.optional.arm64` alone (Kilo WARNING on the first
    revision): the sysctl key reports the CPU architecture, not a GPU.
    """
    result = _run_wrapper(
        tmp_path,
        _detection_wrapper(
            tmp_path, mach="arm64", sp_output="", mlx_present=False, sp_available=False
        ),
    )
    assert result.returncode == 0, result.stderr
    assert _result(result.stdout, "RESULT_MACOS") == "cpu-inference"
    assert "gpu-metal" not in _result(result.stdout, "RESULT_RESOURCES")
    assert "cannot verify Metal support" in result.stderr, result.stderr


def test_force_metal_covers_an_unverifiable_probe(tmp_path: Path) -> None:
    """TAOS_FORCE_METAL=1 is the operator override when the probe cannot tell."""
    result = _run_wrapper(
        tmp_path,
        _detection_wrapper(
            tmp_path, mach="arm64", sp_output="", mlx_present=False, sp_available=False
        ),
        env={"TAOS_FORCE_METAL": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert _result(result.stdout, "RESULT_MACOS") == "gpu-metal"
    assert _result(result.stdout, "RESULT_RESOURCES") == "gpu-metal,cpu-inference"


def test_explicit_resources_env_wins_over_detection(tmp_path: Path) -> None:
    """The documented TAOS_WORKER_RESOURCES override is not silently rewritten."""
    result = _run_wrapper(
        tmp_path,
        _detection_wrapper(
            tmp_path, mach="arm64", sp_output="Metal Support: Metal 3", mlx_present=True
        ),
        env={"TAOS_WORKER_RESOURCES": "cpu-inference"},
    )
    assert result.returncode == 0, result.stderr
    assert _result(result.stdout, "RESULT_RESOURCES") == "cpu-inference"
    assert "set explicitly" in result.stdout, result.stdout


# --- launchd plist --------------------------------------------------------


def test_launchd_plist_is_written_to_library_launchagents(tmp_path: Path) -> None:
    """The agent is user-owned: ~/Library/LaunchAgents, no root, no /Library."""
    home = tmp_path / "home"
    home.mkdir()
    functions = "\n".join(
        _extract_function(INSTALL_SCRIPT, name)
        for name in ("install_macos_launchd",)
    )
    body = (
        "log() { printf '%s\\n' \"$*\"; }\n"
        'HOME="$(printf %s "$FAKE_HOME")"\n'
        'INSTALL_DIR="$HOME/.local/share/tinyagentos-worker"\n'
        "CONTROLLER_URL=http://controller:6969\n"
        "WORKER_NAME=mac-mini-worker\n"
        "TAOS_WORKER_RESOURCES=gpu-metal,cpu-inference\n"
        f'launchctl() {{ printf \'launchctl %s\\n\' "$*" >> "$FAKE_HOME/launchctl.log"; }}\n'
        + functions
        + "\ninstall_macos_launchd\n"
    )
    result = _run_wrapper(tmp_path, body, env={"FAKE_HOME": str(home)})
    assert result.returncode == 0, result.stderr

    plist = home / "Library" / "LaunchAgents" / "com.tinyagentos.worker.plist"
    assert plist.exists(), f"launchd plist not written; stdout={result.stdout!r}"
    assert "installed" in result.stdout, result.stdout

    # Valid XML (plutil is macOS-only; ElementTree is the portable check).
    root = ET.parse(plist).getroot()
    assert root.tag == "plist"
    text = plist.read_text()
    assert "<key>TAOS_WORKER_RESOURCES</key><string>gpu-metal,cpu-inference</string>" in text
    assert "tinyagentos.worker" in text
    assert "mac-mini-worker" in text

    # Loaded via launchctl, not systemctl.
    log = home / "launchctl.log"
    assert log.exists() and "load" in log.read_text(), "launchctl load not invoked"


# --- macOS pairing (the launchd agent must not boot unpaired) -------------


def _extract_section(script: Path, start_marker: str, end_marker: str) -> str:
    """Extract the shipped text between two markers (end marker excluded)."""
    text = script.read_text()
    assert start_marker in text, (
        f"{start_marker!r} not found in {script}: the macOS pair step is missing"
    )
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    return text[start:end]


def _write_fake_venv_python(install_dir: Path) -> None:
    """Stand-in for $INSTALL_DIR/.venv/bin/python that records its argv."""
    bin_dir = install_dir / ".venv" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    fake = bin_dir / "python"
    fake.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@" >> "$ARGS_LOG"\n')
    fake.chmod(0o755)


def test_pair_step_writes_the_key_where_the_launchd_agent_looks(tmp_path: Path) -> None:
    """The pair step and the plist must name the SAME state dir.

    pair.py defaults to ~/.local/state/taos-worker while the plist sets
    TAOS_WORKER_STATE_DIR=$INSTALL_DIR/.taos-worker-state, so a pair run
    without --state-dir saves the key where the launched daemon never looks
    and the worker reports "not paired" despite a successful pairing.
    """
    home = tmp_path / "home"
    home.mkdir()
    install_dir = home / ".local" / "share" / "tinyagentos-worker"
    args_log = tmp_path / "pair-args.log"
    _write_fake_venv_python(install_dir)

    functions = "\n".join(
        _extract_function(INSTALL_SCRIPT, name)
        for name in ("pair_worker", "install_macos_launchd")
    )
    body = (
        "log() { printf '%s\\n' \"$*\"; }\n"
        "warn() { printf '%s\\n' \"$*\" >&2; }\n"
        'HOME="$(printf %s "$FAKE_HOME")"\n'
        'INSTALL_DIR="$HOME/.local/share/tinyagentos-worker"\n'
        "CONTROLLER_URL=http://controller:6969\n"
        "WORKER_NAME=mac-mini-worker\n"
        "TAOS_WORKER_RESOURCES=gpu-metal,cpu-inference\n"
        f'launchctl() {{ printf \'launchctl %s\\n\' "$*" >> "$FAKE_HOME/launchctl.log"; }}\n'
        + functions
        + "\npair_worker\ninstall_macos_launchd\n"
    )
    result = _run_wrapper(
        tmp_path, body, env={"FAKE_HOME": str(home), "ARGS_LOG": str(args_log)}
    )
    assert result.returncode == 0, result.stderr

    argv = args_log.read_text().splitlines()
    assert "tinyagentos.worker.pair" in argv, argv
    assert "--register-after" in argv, argv
    assert argv[argv.index("--name") + 1] == "mac-mini-worker", argv
    assert argv[argv.index("--state-dir") + 1] == f"{install_dir}/.taos-worker-state", argv
    # The Linux path advertises the DNAT'd LXC port; macOS must not.
    assert "--url" not in argv, f"macOS must let pair.py infer the URL, got {argv}"
    assert "8443" not in " ".join(argv), argv

    plist = home / "Library" / "LaunchAgents" / "com.tinyagentos.worker.plist"
    match = re.search(
        r"<key>TAOS_WORKER_STATE_DIR</key><string>(.*?)</string>", plist.read_text()
    )
    assert match, "the plist must set TAOS_WORKER_STATE_DIR"
    assert argv[argv.index("--state-dir") + 1] == match.group(1), (
        "pairing wrote the key to a different directory than the service reads"
    )


def test_darwin_pairs_before_installing_the_launchd_agent(tmp_path: Path) -> None:
    """Wiring: the Darwin service-install path pairs, and pairs first.

    Runs the shipped tail of install-worker.sh (the pairing block plus the
    service-install dispatch) with both platform functions stubbed, so the
    call order comes from the script itself, not from a copy of it.
    """
    section = _extract_section(
        INSTALL_SCRIPT, "# --- pairing (macOS) ---", 'log "install complete"'
    )
    for os_name, expect_pair in (("Darwin", True), ("Linux", False)):
        work = tmp_path / os_name
        work.mkdir()
        body = (
            "log() { printf '%s\\n' \"$*\"; }\n"
            "warn() { printf '%s\\n' \"$*\" >&2; }\n"
            "INSTALL_DIR=/tmp/install\n"
            "CONTROLLER_URL=http://controller:6969\n"
            "WORKER_NAME=w\n"
            f"os_name={os_name}\n"
            "SERVICE_MODE=auto\n"
            "pair_worker() { printf 'PAIR\\n'; }\n"
            "install_macos_launchd() { printf 'LAUNCHD\\n'; }\n"
            "install_linux_systemd() { printf 'SYSTEMD\\n'; }\n"
            + section
        )
        result = _run_wrapper(work, body)
        assert result.returncode == 0, result.stderr
        lines = result.stdout.splitlines()
        if expect_pair:
            assert "PAIR" in lines, f"Darwin never pairs:\n{result.stdout}"
            assert lines.index("PAIR") < lines.index("LAUNCHD"), (
                "the plist must be written after pairing, not before"
            )
        else:
            assert "PAIR" not in lines, "Linux pairs inside install_and_enroll_incus"
            assert "SYSTEMD" in lines, result.stdout


def test_pair_failure_stops_the_install(tmp_path: Path) -> None:
    """A failed pair returns 1 so the caller can skip the service install."""
    functions = _extract_function(INSTALL_SCRIPT, "pair_worker")
    body = (
        "log() { printf '%s\\n' \"$*\"; }\n"
        "warn() { printf '%s\\n' \"$*\" >&2; }\n"
        "INSTALL_DIR=/nonexistent-install-dir\n"
        "CONTROLLER_URL=http://controller:6969\n"
        "WORKER_NAME=w\n"
        + functions
        + "\nif pair_worker; then printf 'PAIR_RC=0\\n'; else printf 'PAIR_RC=1\\n'; fi\n"
    )
    result = _run_wrapper(tmp_path, body)
    assert "PAIR_RC=1" in result.stdout, result.stdout


def test_install_script_dispatches_macos_branches(tmp_path: Path) -> None:
    """Run the shipped OS-deps case block instead of grepping for it.

    The Darwin branch has to call ensure_macos_deps and then
    detect_macos_accelerator (the probe, which is what sets
    TAOS_WORKER_RESOURCES and TAOS_MACOS_RESOURCE). The service-install
    dispatch is exercised behaviourally by
    test_darwin_pairs_before_installing_the_launchd_agent.
    """
    text = INSTALL_SCRIPT.read_text()
    match = re.search(
        r'(case "\$os_name" in\n    Linux\) ensure_linux_deps ;;\n.*?\nesac)',
        text,
        re.DOTALL,
    )
    assert match, "the OS deps dispatch block moved; update this test"
    body = (
        "log() { printf '%s\\n' \"$*\"; }\n"
        "warn() { printf '%s\\n' \"$*\" >&2; }\n"
        "os_name=Darwin\n"
        "ensure_linux_deps() { printf 'LINUX\\n'; }\n"
        "ensure_macos_deps() { printf 'MACOS_DEPS\\n'; }\n"
        "detect_macos_accelerator() { printf 'DETECT\\n'; }\n"
        "die() { printf 'DIE %s\\n' \"$*\"; exit 1; }\n"
        + match.group(1)
    )
    result = _run_wrapper(tmp_path, body)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert "MACOS_DEPS" in lines, result.stdout
    assert lines.index("MACOS_DEPS") < lines.index("DETECT"), result.stdout
    assert "LINUX" not in lines, "the Darwin run must not take the Linux branch"


def test_install_script_is_syntactically_valid() -> None:
    result = subprocess.run(
        ["/usr/bin/env", "bash", "-n", str(INSTALL_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


# --- worker-side resource contract ----------------------------------------


def test_worker_reports_gpu_metal_on_apple_silicon(monkeypatch: pytest.MonkeyPatch) -> None:
    from tinyagentos.worker.agent import _collect_resources

    monkeypatch.delenv("TAOS_WORKER_RESOURCES", raising=False)
    resources = _collect_resources([{"type": "mlx"}], "apple")
    assert "gpu-metal" in resources
    assert "gpu-cuda-0" not in resources, "Apple Silicon is not a CUDA device"
    assert resources[0] == "cpu-inference"


def test_worker_keeps_gpu_cuda_for_non_apple(monkeypatch: pytest.MonkeyPatch) -> None:
    from tinyagentos.worker.agent import _collect_resources

    monkeypatch.delenv("TAOS_WORKER_RESOURCES", raising=False)
    assert _collect_resources([{"type": "ollama"}], "cuda") == [
        "cpu-inference",
        "gpu-cuda-0",
    ]
    assert _collect_resources([], "none") == ["cpu-inference"]
    assert _collect_resources([{"type": "rkllama"}], "none") == [
        "cpu-inference",
        "npu-rk3588",
    ]


def test_intel_mac_ollama_does_not_advertise_cuda(monkeypatch: pytest.MonkeyPatch) -> None:
    """CPU-mode Ollama on an Intel Mac must stay cpu-inference.

    macOS has no CUDA/ROCm class, so the installer's Intel-Mac fallback and
    the runtime resource report have to agree (CodeRabbit Major on the second
    revision): 'ollama is running' is not evidence of a CUDA device.
    """
    from tinyagentos.worker.agent import _collect_resources

    monkeypatch.delenv("TAOS_WORKER_RESOURCES", raising=False)
    assert _collect_resources([{"type": "ollama"}], "", "darwin") == ["cpu-inference"]
    assert _collect_resources([{"type": "mlx"}], "", "darwin") == ["cpu-inference"]
    # The same backend on Linux is still the CUDA class.
    assert _collect_resources([{"type": "ollama"}], "", "linux") == [
        "cpu-inference",
        "gpu-cuda-0",
    ]
    # And the installer's own Darwin classes still land.
    monkeypatch.setenv("TAOS_WORKER_RESOURCES", "cpu-inference")
    assert _collect_resources([{"type": "ollama"}], "", "darwin") == ["cpu-inference"]


def test_worker_unions_installer_detected_resources(monkeypatch: pytest.MonkeyPatch) -> None:
    """install-worker.sh's TAOS_WORKER_RESOURCES survives a stopped backend."""
    from tinyagentos.worker.agent import _collect_resources

    monkeypatch.setenv("TAOS_WORKER_RESOURCES", "gpu-metal,cpu-inference")
    resources = _collect_resources([], "apple")
    assert resources == ["cpu-inference", "gpu-metal"]

    monkeypatch.setenv("TAOS_WORKER_RESOURCES", " gpu-metal , ")
    assert _collect_resources([], None) == ["cpu-inference", "gpu-metal"]


def test_worker_env_resources_never_duplicate(monkeypatch: pytest.MonkeyPatch) -> None:
    from tinyagentos.worker.agent import _collect_resources

    monkeypatch.setenv("TAOS_WORKER_RESOURCES", "cpu-inference,cpu-inference")
    assert _collect_resources([], None) == ["cpu-inference"]


def test_gpu_type_helper_handles_serialised_profiles() -> None:
    """register() and heartbeat() read the GPU class through one helper."""
    from tinyagentos.worker.agent import _gpu_type

    assert _gpu_type({"gpu": {"type": "apple"}}) == "apple"
    assert _gpu_type({"gpu": {"type": "cuda"}}) == "cuda"
    assert _gpu_type({"gpu": None}) is None
    assert _gpu_type({}) is None
    assert _gpu_type({"gpu": "apple"}) is None, "a non-dict gpu must not raise"


def test_both_resource_call_sites_share_the_gpu_extraction() -> None:
    """No attribute-vs-dict drift between the register and heartbeat paths."""
    source = (REPO_ROOT / "tinyagentos" / "worker" / "agent.py").read_text()
    assert "getattr(hw.gpu" not in source, "the attribute path was the drift risk"
    # One definition plus exactly two call sites.
    assert source.count("_gpu_type(") == 3


def test_install_script_and_worker_agree_on_the_env_var() -> None:
    """A renamed env var in either half must not silently break the contract."""
    assert "TAOS_WORKER_RESOURCES" in INSTALL_SCRIPT.read_text()
    assert "TAOS_WORKER_RESOURCES" in (
        REPO_ROOT / "tinyagentos" / "worker" / "agent.py"
    ).read_text()


def test_no_stale_systemctl_hint_on_macos() -> None:
    """The install summary must not tell a Mac user to run systemctl."""
    text = INSTALL_SCRIPT.read_text()
    darwin_branch = re.search(r"Darwin\)\n(.*?)\n        ;;", text, re.DOTALL)
    assert darwin_branch, "Darwin upgrade-hint branch not found"
    assert "launchctl" in darwin_branch.group(1)
    assert "systemctl" not in darwin_branch.group(1)


def test_module_import_is_not_host_dependent() -> None:
    """`_collect_resources` takes the GPU class as an argument: no live probe."""
    source = (
        REPO_ROOT / "tinyagentos" / "worker" / "agent.py"
    ).read_text()
    start = source.index("def _collect_resources(")
    end = source.index("\n\n\nclass WorkerAgent", start)
    body = source[start:end]
    # Ignore the docstring: it *names* platform.system() when documenting the
    # argument. The point is that the code never calls a host probe itself.
    code = body.split('"""', 2)[2] if body.count('"""') >= 2 else body
    for host_probe in ("platform.system", "detect_hardware", "sys.platform", "uname"):
        assert host_probe not in code, (
            f"_collect_resources must stay a pure function; found {host_probe}"
        )
    assert 'os.environ.get("TAOS_WORKER_RESOURCES"' in code
