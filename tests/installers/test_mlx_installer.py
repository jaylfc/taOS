"""Tests for the MLX (Apple Silicon) backend installer.

The macOS/MLX branch is never exercised by CI (Linux runners), so these tests
pin the four things that decide whether an MLX install is honest: the
Apple-Silicon gate, the pinned runtime install (its own venv, vendored
hash-pinned lock), the delegation of the weight download into the shared models
tree, and the fact that no endpoint is reported for a service nothing starts.

The new symbols are reached through their modules (``hardware_mod``,
``mlx_mod``) rather than imported by name, so this file also runs against the
pre-fix tree, where it shows exactly which behaviours the change is what makes
pass.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinyagentos import hardware as hardware_mod
from tinyagentos.installers import mlx_installer as mlx_mod
from tinyagentos.installers.mlx_installer import DEFAULT_PORT, MLXInstaller

MLX_VARIANT = {
    "id": "mlx-4bit",
    "hf_repo": "mlx-community/Qwen2.5-3B-Instruct-4bit",
    "size_mb": 1900,
}


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


def _patch_hf_downloader():
    """Patch the HF multi-file downloader used for repo-backed variants."""
    fake_cls = MagicMock()
    fake_cls.return_value.install = AsyncMock(
        return_value={"success": True, "target_dir": "/models/mlx/qwen2.5/qwen2.5-3b"}
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
        """The installer must not advertise a service nothing starts (taOS #329):
        mlx_lm.server is not launched by a model install."""
        _fake_apple(monkeypatch)
        venv = tmp_path / "rt"
        patcher, _ = _patch_hf_downloader()
        with patcher, patch.object(
            MLXInstaller, "_ensure_mlx_lm", AsyncMock(return_value=(True, ""))
        ):
            result = await MLXInstaller(models_dir=tmp_path, venv_dir=venv).install(
                "qwen2.5-3b", install_config={"backend": "mlx"}, variant=MLX_VARIANT
            )
        assert result["success"] is True
        assert "endpoint" not in result
        assert "runtime_location" not in result
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
