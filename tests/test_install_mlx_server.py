"""``scripts/install-mlx-server.sh``: the launchd agent that serves an MLX model.

Gates the serving half of taOS #329 (PR #3237 deliberately shipped no endpoint
because nothing started ``mlx_lm.server``):

  * the plist lands in ``~/Library/LaunchAgents`` pinned to ONE model, with the
    runtime venv's ``mlx_lm.server`` and the reserved port;
  * the agent is bootstrapped/kickstarted with ``launchctl`` (and unloaded
    first, so a re-install replaces rather than doubles it);
  * the health gate polls ``GET /v1/models`` and fails loudly, which is what
    stops a model install from reporting an endpoint nothing serves;
  * Apple Silicon + Metal is verified before any of that, on the device (an
    arm64 VM with no Metal GPU must not get a server that cannot load a model);
  * unloading a model's agent leaves an agent pinned to a *different* model
    alone.

Like ``tests/test_install_worker_macos.py`` this runs the production shell
functions in a simulated environment: the functions are extracted from the
shipped script (never copied), the host commands are shell-function stubs, and
``$HOME`` is a throwaway directory. Nothing here needs macOS.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVE_SCRIPT = REPO_ROOT / "scripts" / "install-mlx-server.sh"
LABEL = "com.taos.mlx-server"

#: Every production function ``main`` reaches on an install. Extracted together
#: so a whole ``main`` run is exercised against the shipped code.
_MAIN_FUNCTIONS = (
    "main",
    "parse_args",
    "resolve_venv",
    "label_for_model",
    "server_binary",
    "log_dir",
    "plist_path",
    "agent_target",
    "retire_legacy_agent_for_model",
    "write_launchd_plist",
    "load_launchd_agent",
    "wait_for_mlx_health",
    "uninstall_mlx_agent",
    "xml_escape",
    "require_apple_silicon",
    "require_server_binary",
    "macos_metal_available",
    "usage",
)

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


def _functions(*names: str) -> str:
    # log/warn/die come along by default: every shipped function logs, and the
    # wrapper would otherwise die with "log: command not found". Trailing
    # newline keeps the last function from running into what the wrapper
    # appends next (a stub or the call itself). LEGACY_LABEL is the script-level
    # constant label_for_model()/plist_path() read.
    wanted = ["log", "warn", "die"] + [n for n in names if n not in ("log", "warn", "die")]
    body = "\n".join(_extract_function(SERVE_SCRIPT, name) for name in wanted)
    return 'LEGACY_LABEL="com.taos.mlx-server"\n' + body + "\n"


def _runtime_venv(tmp_path: Path) -> Path:
    """A venv-looking tree with an executable ``bin/mlx_lm.server`` in it."""
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True, exist_ok=True)
    binary = venv / "bin" / "mlx_lm.server"
    binary.write_text("#!/usr/bin/env bash\n")
    binary.chmod(0o755)
    return venv


def _launchctl_stub(*, print_result: str = "absent") -> str:
    """A launchctl stand-in that logs its calls.

    ``print`` answers with the platform's unknown-service message by default
    (what a successful bootout leaves behind). Pass ``"loaded"`` for an agent
    launchd still has registered, or ``"error"`` for an inspection that fails
    for some other reason (permissions, session) - the two cases the script must
    not confuse.
    """
    if print_result == "loaded":
        print_body = '        print) printf "%s\\n" "com.taos.mlx-server = { ... }"; return 0 ;;\n'
    elif print_result == "error":
        print_body = (
            '        print) printf "%s\\n" "launchctl print: Operation not permitted" >&2;'
            " return 1 ;;\n"
        )
    else:
        print_body = (
            "        print) printf '%s\\n' 'Could not find service \"com.taos.mlx-server\""
            " in domain for user gui: 501' >&2; return 1 ;;\n"
        )
    return (
        'launchctl() {\n'
        '    printf "launchctl %s\\n" "$*" >> "$HOME/launchctl.log"\n'
        '    case "$1" in\n'
        + print_body
        + "    esac\n"
        "    return 0\n"
        "}\n"
    )


def _stub_host(
    *,
    system_profiler: str | None = 'printf "%s\\n" "      Metal Support: Metal 3"',
    shell: str = "Darwin",
    machine: str = "arm64",
) -> str:
    """Shell-function stubs for the macOS host commands the script probes.

    Omitting *system_profiler* makes ``command -v system_profiler`` fail, which
    is what a trimmed image looks like (the child runs with PATH=/usr/bin:/bin).
    """
    stub = (
        f'uname() {{ case "${{1:-}}" in -s) printf "%s\\n" "{shell}";;'
        f' -m) printf "%s\\n" "{machine}";; *) printf "%s\\n" "{shell}";; esac; }}\n'
    )
    if system_profiler is not None:
        stub += f"system_profiler() {{ {system_profiler}; }}\n"
    return stub


def _globals(
    tmp_path: Path, *, model: str, port: int = 7837, timeout: int = 3, label: str = LABEL
) -> str:
    return (
        f'HOME="{tmp_path}/home"\n'
        f'VENV_DIR="{tmp_path}/venv"\n'
        f'MODEL_DIR="{model}"\n'
        f'LABEL="{label}"\n'
        f'HOST="127.0.0.1"\n'
        f'PORT="{port}"\n'
        f'HEALTH_TIMEOUT="{timeout}"\n'
        f'UNINSTALL="0"\n'
        'mkdir -p "$HOME" "${VENV_DIR}/bin"\n'
    )


def _plist_dict(path: Path) -> dict:
    """The plist's top-level dict as a plain Python dict (plutil is macOS-only)."""
    root = ET.parse(path).getroot()
    node = root.find("dict")
    assert node is not None, "plist has no <dict>"
    children = list(node)
    out: dict = {}
    index = 0
    while index < len(children):
        key = children[index]
        assert key.tag == "key", key.tag
        value = children[index + 1]
        out[key.text] = value
        index += 2
    return out


def _plist_argv(path: Path) -> list[str]:
    node = _plist_dict(path)["ProgramArguments"]
    return [child.text for child in node.findall("string")]


# --- the plist ---------------------------------------------------------------


def test_plist_is_written_under_the_fake_home_and_pins_the_model(tmp_path: Path) -> None:
    model = tmp_path / "models" / "mlx" / "qwen2.5" / "qwen2.5-3b"
    model.mkdir(parents=True)
    _runtime_venv(tmp_path)

    result = _run_wrapper(
        tmp_path,
        _globals(tmp_path, model=str(model))
        + _functions(
            "write_launchd_plist", "server_binary", "log_dir", "plist_path", "xml_escape"
        )
        + _stub_host()
        + "write_launchd_plist\n",
    )
    assert result.returncode == 0, result.stderr

    plist = tmp_path / "home" / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    assert plist.exists(), f"plist not written; stdout={result.stdout!r}"
    argv = _plist_argv(plist)
    assert argv == [
        str(tmp_path / "venv" / "bin" / "mlx_lm.server"),
        "--model",
        str(model),
        "--host",
        "127.0.0.1",
        "--port",
        "7837",
    ], argv
    body = _plist_dict(plist)
    assert body["Label"].text == LABEL
    assert body["RunAtLoad"].tag == "true"
    assert body["KeepAlive"].tag == "true"
    # Logs sit next to the runtime venv, not inside the model tree, so the
    # Models app can clean models without taking the service log with it.
    assert body["StandardErrorPath"].text == str(tmp_path / "mlx-server.err.log")


def test_plist_honours_the_configured_port(tmp_path: Path) -> None:
    model = tmp_path / "models" / "mlx" / "qwen2.5" / "qwen2.5-3b"
    model.mkdir(parents=True)
    _runtime_venv(tmp_path)

    result = _run_wrapper(
        tmp_path,
        _globals(tmp_path, model=str(model), port=7899)
        + _functions(
            "write_launchd_plist", "server_binary", "log_dir", "plist_path", "xml_escape"
        )
        + _stub_host()
        + "write_launchd_plist\n",
    )
    assert result.returncode == 0, result.stderr
    plist = tmp_path / "home" / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    argv = _plist_argv(plist)
    assert argv[argv.index("--port") + 1] == "7899"


# --- loading the agent -------------------------------------------------------


def test_agent_is_booted_out_then_bootstrapped_and_started(tmp_path: Path) -> None:
    model = tmp_path / "models" / "mlx" / "qwen2.5" / "qwen2.5-3b"
    model.mkdir(parents=True)

    result = _run_wrapper(
        tmp_path,
        _globals(tmp_path, model=str(model))
        + _functions("load_launchd_agent", "plist_path", "agent_target")
        + _stub_host()
        + 'launchctl() { printf "launchctl %s\\n" "$*" >> "$HOME/launchctl.log"; }\n'
        + 'id() { printf "501\\n"; }\n'
        + "load_launchd_agent\n",
    )
    assert result.returncode == 0, result.stderr

    calls = (tmp_path / "home" / "launchctl.log").read_text().splitlines()
    # A stale agent from an earlier model path must be dropped first: bootstrap
    # on an already-loaded label fails and would leave the old model serving.
    assert calls[0] == f"launchctl bootout gui/501/{LABEL}"
    assert f"launchctl bootstrap gui/501 {tmp_path}/home/Library/LaunchAgents/{LABEL}.plist" in calls
    assert f"launchctl kickstart -k gui/501/{LABEL}" in calls


# --- the health gate ---------------------------------------------------------


def test_health_gate_polls_the_openai_surface(tmp_path: Path) -> None:
    result = _run_wrapper(
        tmp_path,
        _globals(tmp_path, model=str(tmp_path / "model"))
        + _functions("wait_for_mlx_health")
        + 'curl() { printf "curl %s\\n" "$*" >> "$HOME/curl.log"; return 0; }\n'
        + "wait_for_mlx_health\n",
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "home" / "curl.log").read_text().strip() == (
        "curl -fsS http://127.0.0.1:7837/v1/models"
    )


def test_health_gate_fails_when_the_server_never_answers(tmp_path: Path) -> None:
    """The failure path is the contract: no answer, no endpoint report."""
    result = _run_wrapper(
        tmp_path,
        _globals(tmp_path, model=str(tmp_path / "model"), timeout=2)
        + _functions("wait_for_mlx_health")
        + "curl() { return 7; }\n"
        + 'sleep() { printf "slept\\n" >> "$HOME/sleep.log"; }\n'
        + "if wait_for_mlx_health; then exit 9; fi\n"
        + 'printf "GATE_FAILED\\n"\n'
        + 'wc -l < "$HOME/sleep.log"\n',
    )
    assert result.returncode == 0, result.stderr
    assert "GATE_FAILED" in result.stdout
    # It retried instead of giving up on the first refused connection.
    assert result.stdout.strip().endswith("2")


def test_main_reports_no_endpoint_when_the_health_gate_fails(tmp_path: Path) -> None:
    """End-to-end: the script exits non-zero, so install() cannot claim an endpoint."""
    model = tmp_path / "models" / "mlx" / "qwen2.5" / "qwen2.5-3b"
    model.mkdir(parents=True)
    _runtime_venv(tmp_path)

    result = _run_wrapper(
        tmp_path,
        _globals(tmp_path, model=str(model), timeout=1)
        + _functions(*_MAIN_FUNCTIONS)
        + _stub_host()
        + "launchctl() { :; }\n"
        + 'id() { printf "501\\n"; }\n'
        + 'curl() { return 7; }\n'
        + "sleep() { :; }\n"
        + 'main --model "$MODEL_DIR" --venv "$VENV_DIR" --health-timeout 1\n',
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "did not answer http://127.0.0.1:7837/v1/models" in result.stderr
    # The endpoint only ever appears in the success summary below.
    assert "HTTP endpoint:" not in result.stdout


def test_main_prints_the_endpoint_once_the_server_answers(tmp_path: Path) -> None:
    model = tmp_path / "models" / "mlx" / "qwen2.5" / "qwen2.5-3b"
    model.mkdir(parents=True)
    _runtime_venv(tmp_path)

    result = _run_wrapper(
        tmp_path,
        _globals(tmp_path, model=str(model))
        + _functions(*_MAIN_FUNCTIONS)
        + _stub_host()
        + 'launchctl() { printf "%s\\n" "$*" >> "$HOME/launchctl.log"; }\n'
        + 'id() { printf "501\\n"; }\n'
        + "curl() { return 0; }\n"
        + 'main --model "$MODEL_DIR" --venv "$VENV_DIR"\n',
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "HTTP endpoint: http://127.0.0.1:7837/v1" in result.stdout
    assert (tmp_path / "home" / "Library" / "LaunchAgents" / f"{LABEL}.plist").exists()


# --- host gates --------------------------------------------------------------


def test_refuses_a_mac_without_a_metal_device(tmp_path: Path) -> None:
    """arm64 alone is not enough: Metal is the device, not the architecture."""
    result = _run_wrapper(
        tmp_path,
        _globals(tmp_path, model=str(tmp_path / "model"))
        + _functions("require_apple_silicon", "macos_metal_available")
        + _stub_host(system_profiler='printf "%s\\n" "      Metal Support: Unsupported"')
        + "require_apple_silicon\n",
    )
    assert result.returncode == 1
    assert "no Metal device" in result.stderr


def test_refuses_a_host_that_cannot_report_metal_support(tmp_path: Path) -> None:
    result = _run_wrapper(
        tmp_path,
        _globals(tmp_path, model=str(tmp_path / "model"))
        + _functions("require_apple_silicon", "macos_metal_available")
        + _stub_host(system_profiler=None)
        + "require_apple_silicon\n",
    )
    assert result.returncode == 1
    assert "cannot verify Metal support" in result.stderr


def test_refuses_a_linux_host_and_an_intel_mac(tmp_path: Path) -> None:
    linux = _run_wrapper(
        tmp_path,
        _globals(tmp_path, model=str(tmp_path / "model"))
        + _functions("require_apple_silicon", "macos_metal_available")
        + _stub_host(shell="Linux", machine="x86_64")
        + "require_apple_silicon\n",
    )
    assert linux.returncode == 1
    assert "macOS-only" in linux.stderr

    intel = _run_wrapper(
        tmp_path,
        _globals(tmp_path, model=str(tmp_path / "model"))
        + _functions("require_apple_silicon", "macos_metal_available")
        + _stub_host(machine="x86_64")
        + "require_apple_silicon\n",
    )
    assert intel.returncode == 1
    assert "Apple Silicon" in intel.stderr


def test_force_metal_short_circuits_the_probe(tmp_path: Path) -> None:
    result = _run_wrapper(
        tmp_path,
        _globals(tmp_path, model=str(tmp_path / "model"))
        + "TAOS_FORCE_METAL=1\n"
        + _functions("require_apple_silicon", "macos_metal_available")
        + _stub_host(system_profiler=None)
        + 'printf "ALLOWED\\n"\n'
        + "require_apple_silicon\n",
    )
    assert result.returncode == 0, result.stderr
    assert "ALLOWED" in result.stdout


def test_requires_the_pinned_runtime_before_touching_launchd(tmp_path: Path) -> None:
    """No runtime venv (no model installed yet) is an error, not an empty agent."""
    model = tmp_path / "models" / "mlx" / "qwen2.5" / "qwen2.5-3b"
    model.mkdir(parents=True)
    # Deliberately no `_runtime_venv`: the venv only exists once a model has
    # been installed on the mlx backend.

    result = _run_wrapper(
        tmp_path,
        _globals(tmp_path, model=str(model))
        + _functions("require_server_binary", "server_binary")
        + "require_server_binary\n",
    )
    assert result.returncode == 1
    assert "no MLX server at" in result.stderr


def test_missing_model_directory_is_an_error(tmp_path: Path) -> None:
    result = _run_wrapper(
        tmp_path,
        _globals(tmp_path, model=str(tmp_path / "gone"))
        + _functions("require_server_binary", "server_binary")
        + "require_server_binary\n",
    )
    assert result.returncode == 1
    assert "does not exist" in result.stderr


# --- uninstall ---------------------------------------------------------------


def _plist_with_model(tmp_path: Path, model: str) -> Path:
    plist = tmp_path / "home" / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_text(
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<plist version=\"1.0\"><dict>\n"
        f"<key>ProgramArguments</key><array><string>--model</string><string>{model}</string></array>\n"
        "</dict></plist>\n"
    )
    return plist


def test_uninstall_stops_and_removes_the_agent_for_its_own_model(tmp_path: Path) -> None:
    model = tmp_path / "models" / "mlx" / "qwen2.5" / "qwen2.5-3b"
    model.mkdir(parents=True)
    plist = _plist_with_model(tmp_path, str(model))

    result = _run_wrapper(
        tmp_path,
        _globals(tmp_path, model=str(model))
        + _functions("uninstall_mlx_agent", "xml_escape", "plist_path", "agent_target")
        + _launchctl_stub()
        + 'id() { printf "501\\n"; }\n'
        + "uninstall_mlx_agent\n",
    )
    assert result.returncode == 0, result.stderr
    assert not plist.exists()
    calls = (tmp_path / "home" / "launchctl.log").read_text().splitlines()
    assert calls[0] == f"launchctl bootout gui/501/{LABEL}"
    # ...and the bootout is confirmed with `print` before the plist is removed.
    assert calls[1] == f"launchctl print gui/501/{LABEL}"


def test_uninstall_leaves_an_agent_pinned_to_another_model(tmp_path: Path) -> None:
    """Removing model A must not take model B's server down, and the caller
    must be able to tell that apart from "unloaded" (exit 3, not 0)."""
    served = tmp_path / "models" / "mlx" / "qwen2.5" / "qwen2.5-3b"
    served.mkdir(parents=True)
    other = tmp_path / "models" / "mlx" / "qwen3" / "qwen3-4b"
    other.mkdir(parents=True)
    plist = _plist_with_model(tmp_path, str(served))

    result = _run_wrapper(
        tmp_path,
        _globals(tmp_path, model=str(other))
        + _functions("uninstall_mlx_agent", "xml_escape", "plist_path", "agent_target", "xml_escape")
        + _launchctl_stub()
        + 'id() { printf "501\\n"; }\n'
        + "uninstall_mlx_agent\n",
    )
    assert result.returncode == 3, result.stdout + result.stderr
    assert plist.exists(), "the agent serving another model was removed"
    assert not (tmp_path / "home" / "launchctl.log").exists(), "launchctl was called anyway"


def test_uninstall_without_an_agent_is_a_no_op(tmp_path: Path) -> None:
    result = _run_wrapper(
        tmp_path,
        _globals(tmp_path, model=str(tmp_path / "model"))
        + _functions("uninstall_mlx_agent", "xml_escape", "plist_path", "agent_target", "xml_escape")
        + _launchctl_stub()
        + 'id() { printf "501\\n"; }\n'
        + "uninstall_mlx_agent\n",
    )
    assert result.returncode == 0, result.stderr
    assert "no com.taos.mlx-server agent installed" in result.stdout


# --- XML escaping at the plist boundary --------------------------------------


def test_plist_escapes_xml_in_the_paths(tmp_path: Path) -> None:
    """A model directory is arbitrary user data: "Models & Data" must still
    produce a plist launchd can parse (CodeRabbit on #3337)."""
    model = tmp_path / "Models & Data" / "mlx" / "qwen2.5" / "qwen2.5-3b"
    model.mkdir(parents=True)
    _runtime_venv(tmp_path)

    result = _run_wrapper(
        tmp_path,
        _globals(tmp_path, model=str(model))
        + _functions(
            "write_launchd_plist", "server_binary", "log_dir", "plist_path", "xml_escape"
        )
        + _stub_host()
        + "write_launchd_plist\n",
    )
    assert result.returncode == 0, result.stderr

    plist = tmp_path / "home" / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    body = plist.read_text()
    assert "Models & Data" not in body, "raw ampersand in the XML"
    assert "Models &amp; Data" in body
    # ...and the *decoded* value is the path that was asked for, so mlx_lm.server
    # is pointed at the real directory rather than an entity-mangled one.
    argv = _plist_argv(plist)
    assert argv[argv.index("--model") + 1] == str(model)


def test_uninstall_matches_the_escaped_path_the_plist_pins(tmp_path: Path) -> None:
    """Escaping is only useful if --uninstall compares the same way: write the
    agent for an "&" path, then unload it by that path."""
    model = tmp_path / "Models & Data" / "mlx" / "qwen2.5" / "qwen2.5-3b"
    model.mkdir(parents=True)
    _runtime_venv(tmp_path)

    result = _run_wrapper(
        tmp_path,
        _globals(tmp_path, model=str(model))
        + _functions(
            "write_launchd_plist",
            "uninstall_mlx_agent", "xml_escape",
            "server_binary",
            "log_dir",
            "plist_path",
            "agent_target",
            "xml_escape",
        )
        + _stub_host()
        + _launchctl_stub()
        + 'id() { printf "501\\n"; }\n'
        + "write_launchd_plist\n"
        + "uninstall_mlx_agent\n",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    plist = tmp_path / "home" / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    assert not plist.exists(), "the agent pinned to this model was left loaded"


def test_uninstall_refuses_to_report_a_still_loaded_agent(tmp_path: Path) -> None:
    """`launchctl bootout` can fail while the label stays registered, and
    KeepAlive then keeps restarting the server: say so, keep the plist, and let
    the caller report a failure instead of "unloaded" (CodeRabbit on #3337)."""
    model = tmp_path / "models" / "mlx" / "qwen2.5" / "qwen2.5-3b"
    model.mkdir(parents=True)
    plist = _plist_with_model(tmp_path, str(model))

    result = _run_wrapper(
        tmp_path,
        _globals(tmp_path, model=str(model))
        + _functions("uninstall_mlx_agent", "plist_path", "agent_target", "xml_escape")
        + _launchctl_stub(print_result="loaded")
        + 'id() { printf "501\\n"; }\n'
        + "uninstall_mlx_agent\n",
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "still loaded" in result.stderr
    assert plist.exists(), "the plist of a still-loaded agent must not go away"


def test_uninstall_refuses_to_guess_when_launchctl_cannot_answer(
    tmp_path: Path,
) -> None:
    """`launchctl print` failing for a permission/session reason is NOT proof
    the agent is gone: only its unknown-service answer is (CodeRabbit #3337)."""
    model = tmp_path / "models" / "mlx" / "qwen2.5" / "qwen2.5-3b"
    model.mkdir(parents=True)
    plist = _plist_with_model(tmp_path, str(model))

    result = _run_wrapper(
        tmp_path,
        _globals(tmp_path, model=str(model))
        + _functions("uninstall_mlx_agent", "plist_path", "agent_target", "xml_escape")
        + _launchctl_stub(print_result="error")
        + 'id() { printf "501\\n"; }\n'
        + "uninstall_mlx_agent\n",
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "cannot confirm" in result.stderr
    assert plist.exists(), "an unconfirmed unload must not delete the plist"


def test_uninstall_reports_a_failed_plist_removal(tmp_path: Path) -> None:
    """A failing `rm` must not be reported as a successful unload."""
    model = tmp_path / "models" / "mlx" / "qwen2.5" / "qwen2.5-3b"
    model.mkdir(parents=True)
    plist = _plist_with_model(tmp_path, str(model))

    result = _run_wrapper(
        tmp_path,
        _globals(tmp_path, model=str(model))
        + _functions("uninstall_mlx_agent", "plist_path", "agent_target", "xml_escape")
        + _launchctl_stub()
        + 'id() { printf "501\\n"; }\n'
        + "rm() { return 1; }\n"
        + "uninstall_mlx_agent\n",
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "failed to remove" in result.stderr
    assert plist.exists()


def test_main_uninstall_propagates_a_failed_unload(tmp_path: Path) -> None:
    """The regression the review asked for: main --uninstall must exit non-zero,
    so MLXInstaller cannot record `unloaded` for an agent that is still there."""
    model = tmp_path / "models" / "mlx" / "qwen2.5" / "qwen2.5-3b"
    model.mkdir(parents=True)
    plist = _plist_with_model(tmp_path, str(model))

    result = _run_wrapper(
        tmp_path,
        _globals(tmp_path, model=str(model))
        + _functions(
            "main",
            "parse_args",
            "resolve_venv",
            "uninstall_mlx_agent",
            "plist_path",
            "agent_target",
            "xml_escape",
            "usage",
        )
        + _launchctl_stub()
        + 'id() { printf "501\\n"; }\n'
        + "rm() { return 1; }\n"
        + 'main --uninstall --model "$MODEL_DIR" --venv "$VENV_DIR"\n',
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "failed to remove" in result.stderr
    assert plist.exists()


# --- argument handling -------------------------------------------------------


def test_help_lists_the_flags() -> None:
    result = subprocess.run(
        ["/usr/bin/env", "bash", str(SERVE_SCRIPT), "--help"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    for flag in ("--model", "--venv", "--port", "--uninstall"):
        assert flag in result.stdout


def test_unknown_argument_is_rejected() -> None:
    result = subprocess.run(
        ["/usr/bin/env", "bash", str(SERVE_SCRIPT), "--nope"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 1
    assert "unknown argument" in result.stderr


# --- one agent per model (taOS #329 follow-up) -------------------------------
#
# mlx_lm.server serves one model per process, so serving N models means N agents.
# The pre-change script wrote the single legacy plist for whichever model was
# installed last, so installing a second model silently took the first one down.


def _plist_for(tmp_path: Path, *, label: str, model: str, port: int = 7837) -> Path:
    """A minimal agent plist that pins *model* (and the port) under *label*."""
    plist = tmp_path / "home" / "Library" / "LaunchAgents" / f"{label}.plist"
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<plist version="1.0"><dict>\n'
        f"<key>Label</key><string>{label}</string>\n"
        "<key>ProgramArguments</key><array>"
        "<string>--model</string>"
        f"<string>{model}</string>"
        "<string>--port</string>"
        f"<string>{port}</string>"
        "</array>\n</dict></plist>\n"
    )
    return plist


def _install_model(tmp_path: Path, model: Path, *, port: int = 7837):
    """Run the shipped ``main`` for *model* with a per-model label (empty LABEL)."""
    return _run_wrapper(
        tmp_path,
        _globals(tmp_path, model=str(model), label="", port=port)
        + _functions(*_MAIN_FUNCTIONS)
        + _stub_host()
        + 'launchctl() { printf "launchctl %s\\n" "$*" >> "$HOME/launchctl.log"; }\n'
        + 'id() { printf "501\\n"; }\n'
        + "curl() { return 0; }\n"
        + 'main --model "$MODEL_DIR" --venv "$VENV_DIR"\n',
    )


def test_two_models_get_their_own_agents(tmp_path: Path) -> None:
    """Two MLX models installed -> two agents, and the second install neither
    re-points nor stops the first. On the pre-change tree both installs wrote
    the one legacy plist, so the first model's agent was gone."""
    model_a = tmp_path / "models" / "mlx" / "qwen2.5" / "qwen2.5-3b"
    model_b = tmp_path / "models" / "mlx" / "qwen3" / "qwen3-4b"
    model_a.mkdir(parents=True)
    model_b.mkdir(parents=True)
    _runtime_venv(tmp_path)
    agents = tmp_path / "home" / "Library" / "LaunchAgents"

    first = _install_model(tmp_path, model_a)
    assert first.returncode == 0, first.stdout + first.stderr
    log = tmp_path / "home" / "launchctl.log"
    calls_after_first = log.read_text().splitlines()

    second = _install_model(tmp_path, model_b, port=30000)
    assert second.returncode == 0, second.stdout + second.stderr

    plists = sorted(p.name for p in agents.glob("com.taos.mlx-server*.plist"))
    assert plists == [
        "com.taos.mlx-server-qwen2.5-3b.plist",
        "com.taos.mlx-server-qwen3-4b.plist",
    ], plists

    # Each agent serves exactly its own model on its own port -- the first keeps
    # the reserved default, the second the free port the installer allocated.
    assert _plist_argv(agents / "com.taos.mlx-server-qwen2.5-3b.plist") == [
        str(tmp_path / "venv" / "bin" / "mlx_lm.server"),
        "--model", str(model_a), "--host", "127.0.0.1", "--port", "7837",
    ]
    assert _plist_argv(agents / "com.taos.mlx-server-qwen3-4b.plist") == [
        str(tmp_path / "venv" / "bin" / "mlx_lm.server"),
        "--model", str(model_b), "--host", "127.0.0.1", "--port", "30000",
    ]
    # ...and the second install never named the first agent's label.
    new_calls = log.read_text().splitlines()[len(calls_after_first):]
    assert not any("com.taos.mlx-server-qwen2.5-3b" in call for call in new_calls), new_calls

    # The success summary names each agent's own log, not the legacy shared one
    # (CodeRabbit on #3351).
    assert "mlx-server-qwen2.5-3b.log" in first.stdout
    assert "mlx-server-qwen3-4b.log" in second.stdout


def test_uninstalling_one_model_leaves_the_other_agent(tmp_path: Path) -> None:
    """Per-model uninstall: removing model A must not unload model B's agent."""
    model_a = tmp_path / "models" / "mlx" / "qwen2.5" / "qwen2.5-3b"
    model_b = tmp_path / "models" / "mlx" / "qwen3" / "qwen3-4b"
    model_a.mkdir(parents=True)
    model_b.mkdir(parents=True)
    plist_a = _plist_for(tmp_path, label="com.taos.mlx-server-qwen2.5-3b", model=str(model_a))
    plist_b = _plist_for(tmp_path, label="com.taos.mlx-server-qwen3-4b", model=str(model_b))

    result = _run_wrapper(
        tmp_path,
        _globals(tmp_path, model=str(model_a), label="")
        + _functions("uninstall_mlx_agent", "xml_escape", "plist_path", "agent_target")
        + _launchctl_stub()
        + 'id() { printf "501\\n"; }\n'
        + "uninstall_mlx_agent\n",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not plist_a.exists(), "the agent that served the removed model was left loaded"
    assert plist_b.exists(), "the other model's agent was unloaded too"
    calls = (tmp_path / "home" / "launchctl.log").read_text().splitlines()
    assert calls[0] == "launchctl bootout gui/501/com.taos.mlx-server-qwen2.5-3b"
    assert not any("qwen3-4b" in call for call in calls), calls


def test_a_legacy_agent_pinning_the_same_model_is_retired(tmp_path: Path) -> None:
    """Upgrade from #3337: the single legacy agent is replaced by the per-model
    one, so the model is not served twice (and 7837 is freed for the new agent)."""
    model = tmp_path / "models" / "mlx" / "qwen2.5" / "qwen2.5-3b"
    model.mkdir(parents=True)
    _runtime_venv(tmp_path)
    legacy = _plist_for(tmp_path, label=LABEL, model=str(model), port=7837)

    result = _install_model(tmp_path, model)
    assert result.returncode == 0, result.stdout + result.stderr

    assert not legacy.exists(), "the legacy agent was left serving the same model"
    assert (tmp_path / "home" / "Library" / "LaunchAgents"
            / "com.taos.mlx-server-qwen2.5-3b.plist").exists()
    calls = (tmp_path / "home" / "launchctl.log").read_text().splitlines()
    assert calls[0] == f"launchctl bootout gui/501/{LABEL}"


def test_a_legacy_agent_pinning_another_model_is_left_alone(tmp_path: Path) -> None:
    """Installing model A must not retire the legacy agent that serves model B."""
    model_a = tmp_path / "models" / "mlx" / "qwen2.5" / "qwen2.5-3b"
    model_b = tmp_path / "models" / "mlx" / "qwen3" / "qwen3-4b"
    model_a.mkdir(parents=True)
    model_b.mkdir(parents=True)
    _runtime_venv(tmp_path)
    legacy = _plist_for(tmp_path, label=LABEL, model=str(model_b), port=7837)

    result = _install_model(tmp_path, model_a)
    assert result.returncode == 0, result.stdout + result.stderr

    assert legacy.exists(), "another model's legacy agent was removed"
    assert (tmp_path / "home" / "Library" / "LaunchAgents"
            / "com.taos.mlx-server-qwen2.5-3b.plist").exists()


def test_label_for_model_slugifies_the_app_id(tmp_path: Path) -> None:
    """launchd labels are [A-Za-z0-9._-]; an app_id with spaces/'&' still yields
    a valid, unique label rather than a malformed plist filename."""
    result = _run_wrapper(
        tmp_path,
        'MODEL_DIR="/models/mlx/qwen2.5/Qwen2.5-3B & Co"\n'
        + _functions("label_for_model")
        + "label_for_model\n",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "com.taos.mlx-server-Qwen2.5-3B---Co"
