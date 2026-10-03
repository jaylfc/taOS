"""Tests for the MLX (Apple Silicon) backend installer.

The macOS/MLX branch is never exercised by CI (Linux runners), so these tests
pin the four things that decide whether an MLX install is honest: the
Apple-Silicon gate, the pinned runtime install (its own venv, vendored
hash-pinned lock), the delegation of the weight download into the shared models
tree, and the fact that no endpoint is reported for a service nothing starts.
``TestMLXMultiModelServing`` then pins the taOS #329 follow-up: one launchd agent
and one port per model, so installing a second model does not evict the first.

The new symbols are reached through their modules (``hardware_mod``,
``mlx_mod``) rather than imported by name, so this file also runs against the
pre-fix tree, where it shows exactly which behaviours the change is what makes
pass.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinyagentos import hardware as hardware_mod
from tinyagentos.installers import mlx_installer as mlx_mod
from tinyagentos.installers import port_allocator as mlx_mod_allocator
from tinyagentos.installers.mlx_installer import DEFAULT_PORT, MLXInstaller

MLX_VARIANT = {
    "id": "mlx-4bit",
    "hf_repo": "mlx-community/Qwen2.5-3B-Instruct-4bit",
    "size_mb": 1900,
}


@pytest.fixture(autouse=True)
def _isolated_home(monkeypatch, tmp_path):
    """Keep launchd-agent lookups off the host's real HOME.

    ``_serve_port`` reads ``~/Library/LaunchAgents`` to see which models are
    already served. On an Apple Silicon dev box that directory really exists and
    holds agents; CI runs these tests on Linux. Pinning HOME to a throwaway dir
    makes the port decision identical on both.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def _agent_plist(home: Path, *, label: str, model: str, port: int) -> Path:
    """Write a launchd plist that looks like the one the shipped script emits."""
    agents = home / "Library" / "LaunchAgents"
    agents.mkdir(parents=True, exist_ok=True)
    path = agents / f"{label}.plist"
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<plist version="1.0"><dict>\n'
        f"<key>Label</key><string>{label}</string>\n"
        "<key>ProgramArguments</key><array>"
        "<string>/venv/bin/mlx_lm.server</string>"
        f"<string>--model</string><string>{model}</string>"
        "<string>--host</string><string>127.0.0.1</string>"
        f"<string>--port</string><string>{port}</string>"
        "</array>\n</dict></plist>\n"
    )
    return path


def _fake_apple(monkeypatch, *, arm64: bool = True, metal: bool = True) -> None:
    """Make the platform probes report an Apple Silicon host."""
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(hardware_mod.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        hardware_mod.platform, "machine", lambda: "arm64" if arm64 else "x86_64"
    )
    # metal_available() memoises per process; start each test unprobed.
    monkeypatch.setattr(hardware_mod, "_metal_support", None)
    monkeypatch.setattr(
        hardware_mod, "_probe_metal_support", lambda: True if metal else False
    )


def _patch_hf_downloader(target_dir: str = "/models/mlx/qwen2.5/qwen2.5-3b"):
    """Patch the HF multi-file downloader used for repo-backed variants."""
    fake_cls = MagicMock()
    fake_cls.return_value.install = AsyncMock(
        return_value={"success": True, "target_dir": target_dir}
    )
    return patch("tinyagentos.installers.mlx_installer.HFMultiInstaller", fake_cls), fake_cls


def _patch_download_downloader():
    """Patch the single-file DownloadInstaller used for URL-backed variants."""
    fake_cls = MagicMock()
    fake_cls.return_value.install = AsyncMock(
        return_value={"success": True, "path": "/models/mlx/qwen2.5/qwen2.5-3b/model.gguf"}
    )
    return patch("tinyagentos.installers.mlx_installer.DownloadInstaller", fake_cls), fake_cls


def _venv_with_interpreter(venv_dir: Path) -> Path:
    """Materialise a venv directory that already has an interpreter."""
    (venv_dir / "bin").mkdir(parents=True, exist_ok=True)
    (venv_dir / "bin" / "python").write_text("")
    return venv_dir


def _patch_run_cmd(
    *,
    installed: str = "",
    python_version: str = "3.11",
    pip_rc: int = 0,
    pip_out: str = "",
    verified: str | None = None,
):
    """Fake `run_cmd` for the runtime step, dispatching on the command.

    ``installed`` answers the pre-install version probe, ``verified`` the
    post-install one (default: the pin, i.e. a good install).
    """
    state = {"metadata_calls": 0}
    calls: list[list[str]] = []

    async def _run_cmd(cmd, cwd=None, timeout=300):
        calls.append(list(cmd))
        joined = " ".join(cmd)
        if "venv" in cmd and "-m" in cmd:
            return 0, ""
        if "pip" in cmd and "install" in cmd:
            return pip_rc, pip_out
        if "sys.version_info" in joined:
            return 0, python_version
        state["metadata_calls"] += 1
        if state["metadata_calls"] == 1:
            return (0, installed) if installed else (1, "PackageNotFoundError")
        expected = verified if verified is not None else mlx_mod.MLX_LM_VERSION
        return (0, expected) if expected else (1, "ModuleNotFoundError")

    return _run_cmd, calls


class TestAvailabilityProbes:
    def test_not_apple_silicon_off_macos(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(hardware_mod.platform, "system", lambda: "Linux")
        monkeypatch.setattr(hardware_mod, "_metal_support", None)
        assert mlx_mod.is_apple_silicon() is False
        assert hardware_mod.metal_available() is False

    def test_intel_mac_is_not_apple_silicon(self, monkeypatch):
        """Darwin/x86_64 (Intel Mac) has no unified-memory GPU: CPU only."""
        _fake_apple(monkeypatch, arm64=False)
        assert mlx_mod.is_apple_silicon() is False
        assert hardware_mod.metal_available() is False

    def test_apple_silicon_with_metal(self, monkeypatch):
        _fake_apple(monkeypatch)
        assert mlx_mod.is_apple_silicon() is True
        assert hardware_mod.metal_available() is True

    def test_arm64_vm_without_a_metal_device_is_not_metal(self, monkeypatch):
        """Apple Silicon is the platform; Metal is the device (taOS #329)."""
        _fake_apple(monkeypatch, metal=False)
        assert mlx_mod.is_apple_silicon() is True
        assert hardware_mod.metal_available() is False

    def test_runtime_installed_follows_the_runtime_venv(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TAOS_MLX_VENV", str(tmp_path / "runtime"))
        assert mlx_mod.mlx_runtime_installed() is False
        _venv_with_interpreter(tmp_path / "runtime")
        assert mlx_mod.mlx_runtime_installed() is True

    def test_runtime_venv_defaults_outside_the_model_tree(self, monkeypatch, tmp_path):
        """The venv must survive the Models app cleaning up model files."""
        monkeypatch.setenv("TAOS_MODELS_ROOT", str(tmp_path / "models"))
        monkeypatch.delenv("TAOS_MLX_VENV", raising=False)
        venv = mlx_mod.mlx_runtime_venv()
        assert "mlx-runtime" in venv.parts
        assert not venv.is_relative_to(tmp_path / "models")

    def test_server_probe_is_false_when_nothing_listens(self, monkeypatch):
        monkeypatch.setenv("TAOS_MLX_PORT", "1")
        assert mlx_mod.mlx_server_is_running(timeout=0.2) is False

    def test_probe_fails_closed_on_a_malformed_response_but_not_on_a_bug(
        self, monkeypatch
    ):
        """Kilo on #3351: a half-up server (garbage status line, truncated body)
        must read as "not running", while an unrelated programming error must
        surface instead of being swallowed as a dead backend."""
        import contextlib
        import http.client
        import urllib.request

        monkeypatch.setattr(
            mlx_mod.socket, "create_connection", lambda *a, **k: contextlib.nullcontext()
        )
        raised: dict[str, BaseException] = {"exc": http.client.BadStatusLine("garbage")}

        def _urlopen(req, timeout=None):
            raise raised["exc"]

        monkeypatch.setattr(urllib.request, "urlopen", _urlopen)

        assert mlx_mod.mlx_server_is_running(timeout=0.1, port=12345) is False
        raised["exc"] = http.client.IncompleteRead(b"", 10)
        assert mlx_mod.mlx_server_is_running(timeout=0.1, port=12345) is False
        raised["exc"] = RuntimeError("boom")
        with pytest.raises(RuntimeError):
            mlx_mod.mlx_server_is_running(timeout=0.1, port=12345)

    def test_server_probe_follows_the_port_it_is_given(self):
        """A live OpenAI-compatible server counts; the probe is a real GET, and
        the port argument overrides TAOS_MLX_PORT (the installer pins a port)."""
        import http.server
        import threading

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 (BaseHTTPRequestHandler API)
                if self.path == "/v1/models":
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"{}")
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, *args):  # keep the test output clean
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            assert mlx_mod.mlx_server_is_running(timeout=2.0, port=port) is True
        finally:
            server.shutdown()
            server.server_close()
        assert mlx_mod.mlx_server_is_running(timeout=0.2, port=port) is False


class TestVendoredRuntimeLocks:
    """The locks are the supply-chain control; a lock that lost its hashes or
    its pin would make `--require-hashes` fail at install time instead."""

    def test_every_supported_python_has_a_hash_pinned_lock(self):
        for python_version in mlx_mod.SUPPORTED_PYTHONS:
            lock = mlx_mod.requirements_file(python_version)
            assert lock is not None, python_version
            body = lock.read_text()
            assert f"mlx-lm=={mlx_mod.MLX_LM_VERSION}" in body
            assert f"mlx=={mlx_mod.MLX_VERSION}" in body

            lines = body.splitlines()
            index = 0
            pinned = 0
            while index < len(lines):
                line = lines[index]
                if not line or line.startswith(("#", " ")):
                    index += 1
                    continue
                assert line.endswith(" \\"), line
                assert "==" in line, line
                index += 1
                hashes = 0
                while index < len(lines) and lines[index].lstrip().startswith("--hash=sha256:"):
                    hashes += 1
                    index += 1
                assert hashes >= 1, f"{line} has no digest"
                pinned += 1
            # mlx-lm's closure: mlx, numpy, transformers, tokenizers, ...
            assert pinned >= 25

    def test_an_unsupported_python_has_no_lock(self):
        assert mlx_mod.requirements_file("3.10") is None
        assert mlx_mod.requirements_file("3.14") is None


@pytest.mark.asyncio
class TestMLXInstallerInstall:
    async def test_rejects_non_apple_silicon_host(self, monkeypatch):
        """No MLX on Linux/Intel: fail loudly instead of 'installing' a file
        the runtime can never load."""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(hardware_mod.platform, "system", lambda: "Linux")
        monkeypatch.setattr(hardware_mod, "_metal_support", None)
        with patch("tinyagentos.installers.mlx_installer.HFMultiInstaller") as hf:
            result = await MLXInstaller().install(
                "qwen2.5-3b", install_config={"backend": "mlx"}, variant=MLX_VARIANT
            )
        assert result["success"] is False
        assert "Apple Silicon" in result["error"]
        hf.assert_not_called()

    async def test_requires_a_variant(self, monkeypatch):
        _fake_apple(monkeypatch)
        result = await MLXInstaller().install("qwen2.5-3b", install_config={})
        assert result["success"] is False
        assert "variant" in result["error"]

    async def test_requires_a_repo_or_download_url(self, monkeypatch):
        _fake_apple(monkeypatch)
        result = await MLXInstaller().install(
            "qwen2.5-3b", install_config={}, variant={"id": "empty"}
        )
        assert result["success"] is False
        assert "mlx_repo" in result["error"]

    async def test_hf_repo_variant_goes_through_the_multi_file_downloader(
        self, monkeypatch, tmp_path
    ):
        _fake_apple(monkeypatch)
        patcher, fake_cls = _patch_hf_downloader()
        with patcher, patch.object(
            MLXInstaller, "_ensure_mlx_lm", AsyncMock(return_value=(True, ""))
        ):
            result = await MLXInstaller(models_dir=tmp_path).install(
                "qwen2.5-3b",
                install_config={"backend": "mlx"},
                variant=MLX_VARIANT,
            )

        assert result["success"] is True
        assert fake_cls.call_args.kwargs["_root_override"] == tmp_path
        call = fake_cls.return_value.install.await_args
        assert call.args[0] == "qwen2.5-3b"
        assert call.kwargs["variant"]["hf_repo"] == MLX_VARIANT["hf_repo"]
        # The dispatcher's backend marker survives, so the repo lands under
        # <models root>/mlx/... rather than the huggingface default.
        assert call.kwargs["install_config"]["backend"] == "mlx"

    async def test_hf_repo_variant_defaults_backend_to_mlx(self, monkeypatch, tmp_path):
        """A direct (non-dispatcher) call still writes into the mlx tree."""
        _fake_apple(monkeypatch)
        patcher, fake_cls = _patch_hf_downloader()
        with patcher, patch.object(
            MLXInstaller, "_ensure_mlx_lm", AsyncMock(return_value=(True, ""))
        ):
            await MLXInstaller(models_dir=tmp_path).install(
                "qwen2.5-3b", install_config={}, variant=MLX_VARIANT
            )
        call = fake_cls.return_value.install.await_args
        assert call.kwargs["install_config"]["backend"] == "mlx"

    async def test_mlx_repo_overrides_hf_repo(self, monkeypatch, tmp_path):
        """A manifest can aim the MLX variant at a pre-quantised repo while
        keeping its GGUF hf_repo for the other backends."""
        _fake_apple(monkeypatch)
        patcher, fake_cls = _patch_hf_downloader()
        variant = dict(MLX_VARIANT, mlx_repo="mlx-community/Qwen3-4B-8bit")
        with patcher, patch.object(
            MLXInstaller, "_ensure_mlx_lm", AsyncMock(return_value=(True, ""))
        ):
            await MLXInstaller(models_dir=tmp_path).install(
                "qwen2.5-3b", install_config={"backend": "mlx"}, variant=variant
            )
        call = fake_cls.return_value.install.await_args
        assert call.kwargs["variant"]["hf_repo"] == "mlx-community/Qwen3-4B-8bit"

    async def test_blank_mlx_repo_falls_back_to_hf_repo(self, monkeypatch, tmp_path):
        """A whitespace-only mlx_repo is not "set": it must not shadow a valid
        hf_repo and reject the install."""
        _fake_apple(monkeypatch)
        patcher, fake_cls = _patch_hf_downloader()
        variant = dict(MLX_VARIANT, mlx_repo="   ")
        with patcher, patch.object(
            MLXInstaller, "_ensure_mlx_lm", AsyncMock(return_value=(True, ""))
        ):
            result = await MLXInstaller(models_dir=tmp_path).install(
                "qwen2.5-3b", install_config={"backend": "mlx"}, variant=variant
            )
        assert result["success"] is True
        call = fake_cls.return_value.install.await_args
        assert call.kwargs["variant"]["hf_repo"] == MLX_VARIANT["hf_repo"]

    async def test_single_file_variant_falls_back_to_download_installer(
        self, monkeypatch, tmp_path
    ):
        _fake_apple(monkeypatch)
        patcher, fake_cls = _patch_download_downloader()
        variant = {"id": "gguf", "download_url": "https://example/q4.gguf"}
        with patcher, patch.object(
            MLXInstaller, "_ensure_mlx_lm", AsyncMock(return_value=(True, ""))
        ):
            result = await MLXInstaller(models_dir=tmp_path).install(
                "qwen2.5-3b", install_config={"backend": "mlx"}, variant=variant
            )
        assert result["success"] is True
        call = fake_cls.return_value.install.await_args
        assert call.kwargs["variant"] == variant

    async def test_result_claims_no_endpoint_and_names_the_runtime_interpreter(
        self, monkeypatch, tmp_path
    ):
        """The installer must not advertise a service that is not up (taOS #329):
        when the launchd agent cannot be started, ``endpoint`` stays out of the
        result while the runtime interpreter that holds the pinned `mlx-lm` is
        still reported."""
        _fake_apple(monkeypatch)
        venv = tmp_path / "rt"
        patcher, _ = _patch_hf_downloader(
            target_dir=str(tmp_path / "mlx" / "qwen2.5" / "qwen2.5-3b")
        )
        with patcher, patch.object(
            MLXInstaller, "_ensure_mlx_lm", AsyncMock(return_value=(True, ""))
        ), patch.object(
            MLXInstaller,
            "_serve_model",
            AsyncMock(return_value=(False, "no Metal device on this host", 7837)),
        ):
            result = await MLXInstaller(models_dir=tmp_path, venv_dir=venv).install(
                "qwen2.5-3b", install_config={"backend": "mlx"}, variant=MLX_VARIANT
            )
        assert result["success"] is True
        assert "endpoint" not in result
        assert "runtime_location" not in result
        assert result["mlx_serving"] is False
        assert "no Metal device" in result["mlx_serving_error"]
        # What is true instead: the interpreter that holds the pinned runtime.
        assert result["mlx_python"] == str(venv / "bin" / "python")
        assert result["mlx_lm_version"] == mlx_mod.MLX_LM_VERSION

    async def test_download_failure_is_passed_through(self, monkeypatch, tmp_path):
        _fake_apple(monkeypatch)
        with patch.object(
            MLXInstaller, "_ensure_mlx_lm", AsyncMock(return_value=(True, ""))
        ), patch("tinyagentos.installers.mlx_installer.HFMultiInstaller") as hf:
            hf.return_value.install = AsyncMock(
                return_value={"success": False, "error": "download failed for repo"}
            )
            result = await MLXInstaller(models_dir=tmp_path).install(
                "qwen2.5-3b", install_config={"backend": "mlx"}, variant=MLX_VARIANT
            )
        assert result["success"] is False
        assert "download failed" in result["error"]


@pytest.mark.asyncio
class TestMLXInstallerRuntime:
    async def test_creates_its_own_venv_and_installs_from_a_hashed_lock(self, tmp_path):
        """Own venv + vendored lock + --require-hashes: an authenticated Store
        install must not be able to choose what the runtime runs (taOS #329)."""
        run_cmd, calls = _patch_run_cmd(python_version="3.12")
        venv = tmp_path / "mlx-runtime"
        installer = MLXInstaller(pip_python="/usr/local/bin/python3", venv_dir=venv)
        with patch("tinyagentos.installers.mlx_installer.run_cmd", run_cmd):
            ok, err = await installer._ensure_mlx_lm()
        assert ok is True and err == ""

        assert calls[0] == ["/usr/local/bin/python3", "-m", "venv", str(venv)]
        pip_cmd = next(c for c in calls if "install" in c)
        assert pip_cmd[0] == str(venv / "bin" / "python")
        assert "--require-hashes" in pip_cmd
        assert "--only-binary=:all:" in pip_cmd
        lock = Path(pip_cmd[pip_cmd.index("-r") + 1])
        assert lock.name == "mlx_lm_requirements_py312.txt"
        assert lock.exists(), "the lock ships with the package"
        assert f"mlx-lm=={mlx_mod.MLX_LM_VERSION}" in lock.read_text()

    async def test_matching_pinned_runtime_is_not_reinstalled(self, tmp_path):
        """Re-installing a model must not touch an index again."""
        run_cmd, calls = _patch_run_cmd(installed=mlx_mod.MLX_LM_VERSION)
        installer = MLXInstaller(
            venv_dir=_venv_with_interpreter(tmp_path / "mlx-runtime")
        )
        with patch("tinyagentos.installers.mlx_installer.run_cmd", run_cmd):
            ok, err = await installer._ensure_mlx_lm()
        assert ok is True and err == ""
        assert not any("install" in c for c in calls), calls

    async def test_wrong_version_after_install_is_a_failure(self, tmp_path):
        run_cmd, _ = _patch_run_cmd(verified="0.30.0")
        installer = MLXInstaller(venv_dir=tmp_path / "mlx-runtime")
        with patch("tinyagentos.installers.mlx_installer.run_cmd", run_cmd):
            ok, err = await installer._ensure_mlx_lm()
        assert ok is False
        assert "0.30.0" in err and mlx_mod.MLX_LM_VERSION in err

    async def test_unimportable_after_pip_is_a_failure(self, tmp_path):
        """pip exit 0 is not proof the runtime loads: the check runs in the venv
        that will serve the model."""
        run_cmd, _ = _patch_run_cmd(verified="")
        installer = MLXInstaller(venv_dir=tmp_path / "mlx-runtime")
        with patch("tinyagentos.installers.mlx_installer.run_cmd", run_cmd):
            ok, err = await installer._ensure_mlx_lm()
        assert ok is False
        assert "not importable" in err

    async def test_pip_failure_surfaces_the_output(self, tmp_path):
        run_cmd, _ = _patch_run_cmd(pip_rc=1, pip_out="ERROR: no matching distribution")
        installer = MLXInstaller(venv_dir=tmp_path / "mlx-runtime")
        with patch("tinyagentos.installers.mlx_installer.run_cmd", run_cmd):
            ok, err = await installer._ensure_mlx_lm()
        assert ok is False
        assert "MLX runtime" in err
        assert "no matching distribution" in err

    async def test_unsupported_interpreter_has_no_lock_and_says_so(self, tmp_path):
        run_cmd, calls = _patch_run_cmd(python_version="3.14")
        installer = MLXInstaller(
            venv_dir=_venv_with_interpreter(tmp_path / "mlx-runtime")
        )
        with patch("tinyagentos.installers.mlx_installer.run_cmd", run_cmd):
            ok, err = await installer._ensure_mlx_lm()
        assert ok is False
        assert "3.14" in err
        assert "3.11" in err
        assert not any("install" in c for c in calls), "must not pip install unhashed"

    async def test_install_stops_when_the_runtime_cannot_be_installed(
        self, monkeypatch, tmp_path
    ):
        _fake_apple(monkeypatch)
        with patch.object(
            MLXInstaller, "_ensure_mlx_lm",
            AsyncMock(return_value=(False, "installing the pinned MLX runtime failed")),
        ), patch("tinyagentos.installers.mlx_installer.HFMultiInstaller") as hf:
            result = await MLXInstaller(models_dir=tmp_path).install(
                "qwen2.5-3b", install_config={"backend": "mlx"}, variant=MLX_VARIANT
            )
        assert result["success"] is False
        assert "runtime" in result["error"]
        hf.assert_not_called()

    async def test_port_honours_env_override(self, monkeypatch):
        monkeypatch.setenv("TAOS_MLX_PORT", "7899")
        assert MLXInstaller().port == 7899


def test_default_port_is_the_reserved_one():
    assert DEFAULT_PORT == 7837


@pytest.mark.asyncio
class TestMLXServing:
    """The serving half of taOS #329: the installed model is pinned in the
    launchd agent, and ``endpoint`` is reported only once the probe answers."""

    @staticmethod
    def _model_dir(tmp_path: Path) -> Path:
        return tmp_path / "mlx" / "qwen2.5" / "qwen2.5-3b"

    async def test_serving_step_runs_the_shipped_script_for_the_installed_model(
        self, monkeypatch, tmp_path
    ):
        _fake_apple(monkeypatch)
        target = self._model_dir(tmp_path)
        calls: list[list[str]] = []

        async def _run_cmd(cmd, cwd=None, timeout=300):
            calls.append(list(cmd))
            return 0, ""

        monkeypatch.setattr(mlx_mod, "run_cmd", _run_cmd)
        monkeypatch.setattr(mlx_mod, "mlx_server_is_running", lambda *a, **k: True)
        patcher, _ = _patch_hf_downloader(target_dir=str(target))
        with patcher, patch.object(
            MLXInstaller, "_ensure_mlx_lm", AsyncMock(return_value=(True, ""))
        ):
            result = await MLXInstaller(
                models_dir=tmp_path, venv_dir=tmp_path / "rt", port=7899
            ).install(
                "qwen2.5-3b", install_config={"backend": "mlx"}, variant=MLX_VARIANT
            )

        assert result["success"] is True
        assert len(calls) == 1, calls
        assert calls[0][0].endswith("scripts/install-mlx-server.sh")
        assert calls[0][1:] == [
            "--model",
            str(target),
            "--venv",
            str(tmp_path / "rt"),
            "--port",
            "7899",
        ]
        assert result["mlx_serving"] is True
        assert result["mlx_model_dir"] == str(target)
        assert result["endpoint"] == "http://127.0.0.1:7899/v1"
        assert result["runtime_location"] == {
            "host": "127.0.0.1",
            "port": 7899,
            "backend": "mlx",
        }

    async def test_no_endpoint_while_the_probe_never_answers(
        self, monkeypatch, tmp_path
    ):
        """The script can load the agent and the server still never come up:
        the endpoint claim waits for the same probe the checklist uses."""
        _fake_apple(monkeypatch)
        monkeypatch.setattr(mlx_mod, "run_cmd", AsyncMock(return_value=(0, "")))
        monkeypatch.setattr(mlx_mod, "_SERVE_PROBE_INTERVAL_S", 0)
        probes = {"count": 0}

        def _probe(*args, **kwargs):
            probes["count"] += 1
            return False

        monkeypatch.setattr(mlx_mod, "mlx_server_is_running", _probe)
        patcher, _ = _patch_hf_downloader(target_dir=str(self._model_dir(tmp_path)))
        with patcher, patch.object(
            MLXInstaller, "_ensure_mlx_lm", AsyncMock(return_value=(True, ""))
        ):
            result = await MLXInstaller(models_dir=tmp_path, port=7899).install(
                "qwen2.5-3b", install_config={"backend": "mlx"}, variant=MLX_VARIANT
            )

        assert result["success"] is True, "the weights did land; only serving failed"
        assert "endpoint" not in result
        assert result["mlx_serving"] is False
        assert "did not answer" in result["mlx_serving_error"]
        assert probes["count"] == mlx_mod._SERVE_PROBE_ATTEMPTS

    async def test_script_failure_reports_why_and_keeps_the_model(
        self, monkeypatch, tmp_path
    ):
        _fake_apple(monkeypatch)
        monkeypatch.setattr(
            mlx_mod,
            "run_cmd",
            AsyncMock(return_value=(1, "no Metal device on this host: MLX cannot load\n")),
        )
        patcher, _ = _patch_hf_downloader(target_dir=str(self._model_dir(tmp_path)))
        with patcher, patch.object(
            MLXInstaller, "_ensure_mlx_lm", AsyncMock(return_value=(True, ""))
        ):
            result = await MLXInstaller(models_dir=tmp_path).install(
                "qwen2.5-3b", install_config={"backend": "mlx"}, variant=MLX_VARIANT
            )

        assert result["success"] is True
        assert "endpoint" not in result
        assert result["mlx_serving"] is False
        assert "starting the MLX server failed" in result["mlx_serving_error"]
        assert "no Metal device" in result["mlx_serving_error"]

    async def test_serving_is_not_attempted_off_macos(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "linux")
        serving, err, _port = await MLXInstaller()._serve_model(tmp_path / "model")
        assert serving is False
        assert "macOS-only" in err

    async def test_a_target_outside_the_mlx_root_is_never_served(
        self, monkeypatch, tmp_path
    ):
        """A server must not be pointed outside the mlx backend root, whatever
        the downloader reports."""
        _fake_apple(monkeypatch)
        patcher, _ = _patch_hf_downloader(target_dir="/models/mlx/somewhere-else")
        with patcher, patch.object(
            MLXInstaller, "_ensure_mlx_lm", AsyncMock(return_value=(True, ""))
        ), patch.object(
            MLXInstaller, "_serve_model", AsyncMock(return_value=(True, "", 7837))
        ) as serve:
            result = await MLXInstaller(models_dir=tmp_path).install(
                "qwen2.5-3b", install_config={"backend": "mlx"}, variant=MLX_VARIANT
            )
        serve.assert_not_awaited()
        assert result["success"] is True
        assert result["mlx_serving"] is False
        assert "not inside the mlx backend root" in result["mlx_serving_error"]
        assert "endpoint" not in result

    async def test_single_file_variant_has_nothing_to_serve(
        self, monkeypatch, tmp_path
    ):
        """`mlx_lm.server --model <dir>` loads a directory; the single-file
        fallback is not servable, so no endpoint is claimed for it."""
        _fake_apple(monkeypatch)
        patcher, _ = _patch_download_downloader()
        variant = {"id": "gguf", "download_url": "https://example/q4.gguf"}
        with patcher, patch.object(
            MLXInstaller, "_ensure_mlx_lm", AsyncMock(return_value=(True, ""))
        ), patch.object(
            MLXInstaller, "_serve_model", AsyncMock(return_value=(True, "", 7837))
        ) as serve:
            result = await MLXInstaller(models_dir=tmp_path).install(
                "qwen2.5-3b", install_config={"backend": "mlx"}, variant=variant
            )
        serve.assert_not_awaited()
        assert result["success"] is True
        assert result["mlx_serving"] is False
        assert "serves an MLX model directory" in result["mlx_serving_note"]
        assert "endpoint" not in result

    async def test_the_pinned_path_is_the_path_uninstall_matches(
        self, monkeypatch, tmp_path
    ):
        """Both halves must name the same (resolved) model directory: the
        script's --uninstall greps the plist for the path it is given, so a
        symlinked models root would otherwise leave the agent loaded."""
        _fake_apple(monkeypatch)
        monkeypatch.setattr(sys, "platform", "darwin")
        real = tmp_path / "real-models"
        real.mkdir()
        link = tmp_path / "models"
        link.symlink_to(real, target_is_directory=True)
        unresolved = link / "mlx" / "qwen2.5" / "qwen2.5-3b"
        unresolved.mkdir(parents=True)
        resolved = real / "mlx" / "qwen2.5" / "qwen2.5-3b"

        calls: list[list[str]] = []

        async def _run_cmd(cmd, cwd=None, timeout=300):
            calls.append(list(cmd))
            return 0, ""

        monkeypatch.setattr(mlx_mod, "run_cmd", _run_cmd)
        monkeypatch.setattr(mlx_mod, "mlx_server_is_running", lambda *a, **k: True)
        patcher, _ = _patch_hf_downloader(target_dir=str(unresolved))
        installer = MLXInstaller(models_dir=link, venv_dir=tmp_path / "rt")
        with patcher, patch.object(
            MLXInstaller, "_ensure_mlx_lm", AsyncMock(return_value=(True, ""))
        ):
            result = await installer.install(
                "qwen2.5-3b", install_config={"backend": "mlx"}, variant=MLX_VARIANT
            )
        assert result["mlx_model_dir"] == str(resolved)
        pinned = calls[0][calls[0].index("--model") + 1]
        assert pinned == str(resolved), "the plist must pin the resolved model dir"

        uninstall = await installer.uninstall("qwen2.5-3b")
        assert uninstall["mlx_agent_unloaded"] is True
        matched = calls[-1][calls[-1].index("--model") + 1]
        assert matched == pinned, "uninstall must name the directory the plist pins"

    async def test_uninstall_unloads_the_agent_that_served_this_model(
        self, monkeypatch, tmp_path
    ):
        """launchd KeepAlive would restart a server against the deleted model
        directory forever, so removing the served model unloads its agent."""
        monkeypatch.setattr(sys, "platform", "darwin")
        calls: list[list[str]] = []

        async def _run_cmd(cmd, cwd=None, timeout=300):
            calls.append(list(cmd))
            return 0, ""

        monkeypatch.setattr(mlx_mod, "run_cmd", _run_cmd)
        target = self._model_dir(tmp_path)
        target.mkdir(parents=True)

        result = await MLXInstaller(models_dir=tmp_path).uninstall("qwen2.5-3b")

        assert result["success"] is True and result["deleted"] == 1
        assert result["mlx_agent_state"] == "unloaded"
        assert result["mlx_agent_unloaded"] is True
        assert "mlx_agent_error" not in result
        assert calls[0][0].endswith("scripts/install-mlx-server.sh")
        assert calls[0][1:] == [
            "--uninstall",
            "--model",
            str(target.resolve()),
            "--venv",
            str(mlx_mod.mlx_runtime_venv()),
        ]

    async def test_uninstall_does_not_claim_to_have_unloaded_a_left_running_agent(
        self, monkeypatch, tmp_path
    ):
        """The script exits 3 when it deliberately leaves an agent that serves a
        different model alone; that is not "unloaded" (Kilo on #3337)."""
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(
            mlx_mod,
            "run_cmd",
            AsyncMock(return_value=(3, "... pins another model; leaving the agent running\n")),
        )
        target = self._model_dir(tmp_path)
        target.mkdir(parents=True)

        result = await MLXInstaller(models_dir=tmp_path).uninstall("qwen2.5-3b")

        assert result["success"] is True
        assert result["mlx_agent_state"] == "left-running"
        assert result["mlx_agent_unloaded"] is False
        assert "mlx_agent_error" not in result

    async def test_a_serving_script_timeout_is_a_serving_failure_not_a_crash(
        self, monkeypatch, tmp_path
    ):
        """CodeRabbit on #3351: ``run_cmd`` raises ``TimeoutError`` when the
        script overruns, so the install must keep the weights and report why
        rather than propagate out of ``install()``."""
        _fake_apple(monkeypatch)

        async def _run_cmd(cmd, cwd=None, timeout=300):
            raise TimeoutError

        monkeypatch.setattr(mlx_mod, "run_cmd", _run_cmd)
        patcher, _ = _patch_hf_downloader(target_dir=str(self._model_dir(tmp_path)))
        with patcher, patch.object(
            MLXInstaller, "_ensure_mlx_lm", AsyncMock(return_value=(True, ""))
        ):
            result = await MLXInstaller(models_dir=tmp_path, serve_timeout=5).install(
                "qwen2.5-3b", install_config={"backend": "mlx"}, variant=MLX_VARIANT
            )

        assert result["success"] is True, "the weights are on disk; only serving failed"
        assert "endpoint" not in result
        assert result["mlx_serving"] is False
        assert "did not finish within 5s" in result["mlx_serving_error"]

    async def test_an_unload_timeout_is_reported_as_a_failed_unload(
        self, monkeypatch, tmp_path
    ):
        """The same for uninstall: the model is already gone, so the caller must
        hear "failed", not get an exception (CodeRabbit on #3351)."""
        monkeypatch.setattr(sys, "platform", "darwin")

        async def _run_cmd(cmd, cwd=None, timeout=300):
            raise TimeoutError

        monkeypatch.setattr(mlx_mod, "run_cmd", _run_cmd)
        target = self._model_dir(tmp_path)
        target.mkdir(parents=True)

        result = await MLXInstaller(models_dir=tmp_path).uninstall("qwen2.5-3b")

        assert result["success"] is True
        assert result["mlx_agent_state"] == "failed"
        assert result["mlx_agent_unloaded"] is False
        assert "timed out" in result["mlx_agent_error"]

    async def test_a_failed_unload_is_reported_as_a_failure(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(
            mlx_mod, "run_cmd", AsyncMock(return_value=(1, "launchctl: no such label\n"))
        )
        target = self._model_dir(tmp_path)
        target.mkdir(parents=True)

        result = await MLXInstaller(models_dir=tmp_path).uninstall("qwen2.5-3b")

        assert result["success"] is True, "the model itself was removed"
        assert result["mlx_agent_state"] == "failed"
        assert result["mlx_agent_unloaded"] is False
        assert "no such label" in result["mlx_agent_error"]

    async def test_uninstall_off_macos_does_not_touch_the_agent(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(sys, "platform", "linux")

        async def _boom(*args, **kwargs):
            raise AssertionError("the serving script must not run off macOS")

        monkeypatch.setattr(mlx_mod, "run_cmd", _boom)
        target = self._model_dir(tmp_path)
        target.mkdir(parents=True)

        result = await MLXInstaller(models_dir=tmp_path).uninstall("qwen2.5-3b")

        assert result["success"] is True
        assert "mlx_agent_unloaded" not in result


@pytest.mark.asyncio
class TestMLXInstallerUninstall:
    async def test_removes_only_this_manifests_mlx_directory(self, tmp_path):
        target = tmp_path / "mlx" / "qwen2.5" / "qwen2.5-3b"
        target.mkdir(parents=True)
        (target / "config.json").write_text("{}")
        other = tmp_path / "llama-cpp" / "qwen2.5" / "qwen2.5-3b"
        other.mkdir(parents=True)
        (other / "qwen.gguf").write_text("weights")

        result = await MLXInstaller(models_dir=tmp_path).uninstall("qwen2.5-3b")

        assert result["success"] is True
        assert not target.exists()
        assert (other / "qwen.gguf").exists(), "other backends' copies are untouched"

    async def test_missing_directory_is_not_an_error(self, tmp_path):
        result = await MLXInstaller(models_dir=tmp_path).uninstall("qwen2.5-3b")
        assert result["success"] is True
        assert result["deleted"] == 0

    async def test_refuses_an_app_id_that_escapes_the_mlx_root(self, monkeypatch, tmp_path):
        """Both the download and the recursive delete must fail closed on a
        traversal app_id."""
        _fake_apple(monkeypatch)
        outside = tmp_path / "keep-me"
        outside.mkdir(parents=True)
        (outside / "important.txt").write_text("do not delete")

        installer = MLXInstaller(models_dir=tmp_path)
        ensure_runtime = AsyncMock(return_value=(True, ""))
        with patch.object(MLXInstaller, "_ensure_mlx_lm", ensure_runtime), patch(
            "tinyagentos.installers.mlx_installer.HFMultiInstaller"
        ) as hf:
            install_result = await installer.install(
                "../keep-me", install_config={"backend": "mlx"}, variant=MLX_VARIANT
            )
        assert install_result["success"] is False
        assert "refusing to install" in install_result["error"]
        # Rejected before the runtime step: an escaping id must not even reach
        # the pip install.
        ensure_runtime.assert_not_awaited()
        hf.assert_not_called()

        uninstall_result = await installer.uninstall("../keep-me")
        assert uninstall_result["success"] is False
        assert "refusing to remove" in uninstall_result["error"]
        assert (outside / "important.txt").exists()


@pytest.mark.asyncio
class TestMLXMultiModelServing:
    """taOS #329 follow-up: two MLX models served at once, with no silent eviction.

    ``mlx_lm.server`` serves one model per process, so several models coexist by
    each having **its own** agent and port. Installing a second model must not
    re-point (and stop) the first, and each install must report the endpoint of
    the server it actually health-gated.
    """

    @staticmethod
    def _model(root: Path, app_id: str) -> Path:
        return root / "mlx" / "qwen" / app_id

    @staticmethod
    def _capture(monkeypatch):
        calls: list[list[str]] = []

        async def _run_cmd(cmd, cwd=None, timeout=300):
            calls.append(list(cmd))
            return 0, ""

        monkeypatch.setattr(mlx_mod, "run_cmd", _run_cmd)
        monkeypatch.setattr(mlx_mod, "mlx_server_is_running", lambda *a, **k: True)
        return calls

    async def test_the_first_model_keeps_the_reserved_default_port(
        self, monkeypatch, tmp_path
    ):
        """Nothing else is served, so the setup checklist's Metal probe (which
        only knows 7837) must still find the backend."""
        _fake_apple(monkeypatch)
        target = self._model(tmp_path / "models", "qwen2.5-3b")
        calls = self._capture(monkeypatch)
        patcher, _ = _patch_hf_downloader(target_dir=str(target))
        with patcher, patch.object(
            MLXInstaller, "_ensure_mlx_lm", AsyncMock(return_value=(True, ""))
        ):
            result = await MLXInstaller(models_dir=tmp_path / "models").install(
                "qwen2.5-3b", install_config={"backend": "mlx"}, variant=MLX_VARIANT
            )

        assert result["runtime_location"]["port"] == DEFAULT_PORT
        assert calls[0][calls[0].index("--port") + 1] == str(DEFAULT_PORT)

    async def test_a_second_model_is_served_on_its_own_port(
        self, monkeypatch, tmp_path
    ):
        """The regression this slice fixes: installing model B must not re-point
        model A's agent *and* must not squat its port. On the pre-change tree both
        installs passed --port 7837, so the second server could not bind."""
        _fake_apple(monkeypatch)
        root = tmp_path / "models"
        model_a = self._model(root, "qwen2.5-3b")
        model_b = self._model(root, "qwen3-4b")
        # Model A is already served by an agent on the reserved default.
        _agent_plist(
            tmp_path / "home", label="com.taos.mlx-server-qwen2.5-3b",
            model=str(model_a), port=DEFAULT_PORT,
        )
        calls = self._capture(monkeypatch)
        patcher, _ = _patch_hf_downloader(target_dir=str(model_b))
        with patcher, patch.object(
            MLXInstaller, "_ensure_mlx_lm", AsyncMock(return_value=(True, ""))
        ):
            result = await MLXInstaller(models_dir=root).install(
                "qwen3-4b", install_config={"backend": "mlx"}, variant=MLX_VARIANT
            )

        port = int(calls[0][calls[0].index("--port") + 1])
        assert port != DEFAULT_PORT, "model B was pinned to model A's port"
        # A port no app host-port allocation may squat, so it is genuinely free
        # for the second server rather than a reserved taOS service port.
        assert port not in mlx_mod_allocator.RESERVED_PORTS
        assert result["endpoint"] == f"http://127.0.0.1:{port}/v1"
        assert result["runtime_location"]["port"] == port
        # ...and model A's directory is never named: its agent is not touched.
        assert str(model_a) not in " ".join(calls[0])

    async def test_a_reinstall_keeps_the_port_its_agent_already_uses(
        self, monkeypatch, tmp_path
    ):
        """A re-install must not move a running server to a new port (the old
        agent would still hold the old one)."""
        _fake_apple(monkeypatch)
        root = tmp_path / "models"
        model_a = self._model(root, "qwen2.5-3b")
        model_b = self._model(root, "qwen3-4b")
        _agent_plist(
            tmp_path / "home", label="com.taos.mlx-server-qwen2.5-3b",
            model=str(model_a), port=DEFAULT_PORT,
        )
        _agent_plist(
            tmp_path / "home", label="com.taos.mlx-server-qwen3-4b",
            model=str(model_b), port=34567,
        )
        calls = self._capture(monkeypatch)
        patcher, _ = _patch_hf_downloader(target_dir=str(model_b))
        with patcher, patch.object(
            MLXInstaller, "_ensure_mlx_lm", AsyncMock(return_value=(True, ""))
        ):
            result = await MLXInstaller(models_dir=root).install(
                "qwen3-4b", install_config={"backend": "mlx"}, variant=MLX_VARIANT
            )

        assert result["runtime_location"]["port"] == 34567
        assert calls[0][calls[0].index("--port") + 1] == "34567"

    async def test_the_legacy_single_agent_is_seen_as_the_same_model(
        self, monkeypatch, tmp_path
    ):
        """An install upgraded from #3337 keeps 7837: the legacy plist pins this
        model, so it is not "another model" occupying the port."""
        _fake_apple(monkeypatch)
        root = tmp_path / "models"
        model_a = self._model(root, "qwen2.5-3b")
        _agent_plist(
            tmp_path / "home", label="com.taos.mlx-server",
            model=str(model_a), port=DEFAULT_PORT,
        )
        self._capture(monkeypatch)
        patcher, _ = _patch_hf_downloader(target_dir=str(model_a))
        with patcher, patch.object(
            MLXInstaller, "_ensure_mlx_lm", AsyncMock(return_value=(True, ""))
        ):
            result = await MLXInstaller(models_dir=root).install(
                "qwen2.5-3b", install_config={"backend": "mlx"}, variant=MLX_VARIANT
            )

        assert result["runtime_location"]["port"] == DEFAULT_PORT

    async def test_the_default_port_comes_back_when_no_agent_holds_it(
        self, monkeypatch, tmp_path
    ):
        """CodeRabbit on #3351: a model is only pushed off 7837 when another
        agent actually claims it, so a free default is not wasted."""
        _fake_apple(monkeypatch)
        root = tmp_path / "models"
        model_a = self._model(root, "qwen2.5-3b")
        model_b = self._model(root, "qwen3-4b")
        # A is served, but on an allocated port: the reserved default is free.
        _agent_plist(
            tmp_path / "home", label="com.taos.mlx-server-qwen2.5-3b",
            model=str(model_a), port=34567,
        )
        self._capture(monkeypatch)
        patcher, _ = _patch_hf_downloader(target_dir=str(model_b))
        with patcher, patch.object(
            MLXInstaller, "_ensure_mlx_lm", AsyncMock(return_value=(True, ""))
        ):
            result = await MLXInstaller(models_dir=root).install(
                "qwen3-4b", install_config={"backend": "mlx"}, variant=MLX_VARIANT
            )

        assert result["runtime_location"]["port"] == DEFAULT_PORT

    async def test_a_new_model_never_takes_a_port_another_agent_claims(
        self, monkeypatch, tmp_path
    ):
        """CodeRabbit on #3351: an agent's plist names its port even while its
        server is still loading (no socket bound), so allocation must be told to
        skip every claimed port, not just the bound ones."""
        _fake_apple(monkeypatch)
        root = tmp_path / "models"
        model_a = self._model(root, "qwen2.5-3b")
        model_b = self._model(root, "qwen3-4b")
        model_c = self._model(root, "qwen3-8b")
        _agent_plist(
            tmp_path / "home", label="com.taos.mlx-server-qwen2.5-3b",
            model=str(model_a), port=DEFAULT_PORT,
        )
        _agent_plist(
            tmp_path / "home", label="com.taos.mlx-server-qwen3-4b",
            model=str(model_b), port=34567,
        )
        calls = self._capture(monkeypatch)
        patcher, _ = _patch_hf_downloader(target_dir=str(model_c))
        with patcher, patch.object(
            MLXInstaller, "_ensure_mlx_lm", AsyncMock(return_value=(True, ""))
        ):
            result = await MLXInstaller(models_dir=root).install(
                "qwen3-8b", install_config={"backend": "mlx"}, variant=MLX_VARIANT
            )

        port = result["runtime_location"]["port"]
        assert port not in {DEFAULT_PORT, 34567}
        assert calls[0][calls[0].index("--port") + 1] == str(port)

    async def test_concurrent_installs_do_not_claim_the_same_port(
        self, monkeypatch, tmp_path
    ):
        """CodeRabbit on #3351: two Store installs awaited together must not both
        pick 7837. Their servers cannot both bind it, and the loser's probe would
        still see the winner's model answering there -- an endpoint reported for
        a server that serves another model."""
        _fake_apple(monkeypatch)
        root = tmp_path / "models"
        ports: list[int] = []
        home = tmp_path / "home"

        async def _run_cmd(cmd, cwd=None, timeout=300):
            port = int(cmd[cmd.index("--port") + 1])
            model = Path(cmd[cmd.index("--model") + 1])
            ports.append(port)
            # The shipped script writes its plist before health-gating; yield
            # here so the other install would race past the port choice.
            await asyncio.sleep(0)
            _agent_plist(
                home, label=f"com.taos.mlx-server-{model.name}",
                model=str(model), port=port,
            )
            return 0, ""

        async def _hf_install(app_id, install_config=None, variant=None, **kwargs):
            return {"success": True, "target_dir": str(self._model(root, app_id))}

        fake_hf = MagicMock()
        fake_hf.return_value.install = AsyncMock(side_effect=_hf_install)
        monkeypatch.setattr(mlx_mod, "HFMultiInstaller", fake_hf)
        monkeypatch.setattr(mlx_mod, "run_cmd", _run_cmd)
        monkeypatch.setattr(mlx_mod, "mlx_server_is_running", lambda *a, **k: True)

        with patch.object(
            MLXInstaller, "_ensure_mlx_lm", AsyncMock(return_value=(True, ""))
        ):
            await asyncio.gather(
                MLXInstaller(models_dir=root).install(
                    "qwen2.5-3b", install_config={"backend": "mlx"}, variant=MLX_VARIANT
                ),
                MLXInstaller(models_dir=root).install(
                    "qwen3-4b", install_config={"backend": "mlx"}, variant=MLX_VARIANT
                ),
            )

        assert len(ports) == 2
        assert len(set(ports)) == 2, f"both installs claimed the same port: {ports}"
        assert min(ports) == DEFAULT_PORT

    async def test_an_unreadable_port_on_this_models_agent_does_not_move_it(
        self, monkeypatch, tmp_path
    ):
        """Kilo CRITICAL on #3351: this model's own agent wins even when its port
        cannot be read, so a re-install never allocates a fresh port under a
        server that is still running."""
        _fake_apple(monkeypatch)
        root = tmp_path / "models"
        model_a = self._model(root, "qwen2.5-3b")
        model_b = self._model(root, "qwen3-4b")
        agents = tmp_path / "home" / "Library" / "LaunchAgents"
        agents.mkdir(parents=True, exist_ok=True)
        # A's own agent pins A but carries no readable --port.
        (agents / "com.taos.mlx-server-qwen2.5-3b.plist").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<plist version="1.0"><dict>\n'
            "<key>ProgramArguments</key><array>"
            f"<string>--model</string><string>{model_a}</string>"
            "</array>\n</dict></plist>\n"
        )
        _agent_plist(
            tmp_path / "home", label="com.taos.mlx-server-qwen3-4b",
            model=str(model_b), port=34567,
        )
        calls = self._capture(monkeypatch)
        patcher, _ = _patch_hf_downloader(target_dir=str(model_a))
        with patcher, patch.object(
            MLXInstaller, "_ensure_mlx_lm", AsyncMock(return_value=(True, ""))
        ):
            result = await MLXInstaller(models_dir=root).install(
                "qwen2.5-3b", install_config={"backend": "mlx"}, variant=MLX_VARIANT
            )

        assert result["runtime_location"]["port"] == DEFAULT_PORT
        assert calls[0][calls[0].index("--port") + 1] == str(DEFAULT_PORT)

    async def test_agent_readers_parse_the_shipped_plist_shape(self, tmp_path):
        """The port decision reads the plists the shipped script writes."""
        model = tmp_path / "mlx" / "qwen2.5" / "qwen2.5-3b"
        plist = _agent_plist(
            tmp_path / "home", label="com.taos.mlx-server-qwen2.5-3b",
            model=str(model), port=34567,
        )
        assert mlx_mod.mlx_agent_model_dir(plist) == str(model)
        assert mlx_mod.mlx_agent_port(plist) == 34567
        assert mlx_mod.mlx_agent_plists() == [plist]

    async def test_a_model_without_an_agent_is_not_matched(self, tmp_path):
        plist = _agent_plist(
            tmp_path / "home", label="com.taos.mlx-server-other",
            model=str(tmp_path / "elsewhere"), port=34567,
        )
        assert mlx_mod.mlx_agent_model_dir(plist) == str(tmp_path / "elsewhere")
        assert mlx_mod.mlx_agent_model_dir(plist) != str(
            tmp_path / "mlx" / "qwen2.5" / "qwen2.5-3b"
        )
