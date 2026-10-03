"""MLX installer -- Apple Silicon models via the `mlx-lm` runtime (taOS #329).

MLX is Apple's array framework for Apple Silicon and `mlx-lm` is the LLM
runtime on top of it. It is what the taOS `mlx` backend adapter talks to
(``backend_adapters.py`` maps ``mlx`` to the OpenAI-compatible adapter, and
``mlx_lm.server`` speaks exactly that API).

Unlike the CUDA/ROCm backends there is no daemon to install and no
``active.gguf`` symlink to flip: `mlx_lm.load` / `mlx_lm.server` take either
an MLX HuggingFace repo id or a local model directory. So this installer:

1. Verifies the host is Apple Silicon. MLX exists only on ``darwin``/``arm64``;
   everywhere else we fail with a clear message instead of letting the generic
   download fallback drop a file the runtime can never load.
2. Makes sure the `mlx-lm` runtime is installed, in a venv of its own (see
   "Runtime supply chain" below).
3. Downloads the model into the shared layout at
   ``<models root>/mlx/<family>/<manifest_id>/`` by delegating to
   ``HFMultiInstaller`` (a repo of safetensors + config + tokenizer) or
   ``DownloadInstaller`` (a single file), so MLX weights sit next to every
   other backend's copies and the Models app can size/clean them uniformly.

Manifest variant fields:

    mlx_repo: mlx-community/Qwen2.5-3B-Instruct-4bit   # preferred
    hf_repo:  ...                                      # accepted fallback
    download_url: ...                                  # single-file fallback

``mlx_repo`` wins over ``hf_repo`` so one variant can point MLX at a
pre-quantised ``mlx-community`` repo while keeping its GGUF ``hf_repo`` for
the other backends. A variant with neither ``mlx_repo``/``hf_repo`` nor
``download_url`` is rejected rather than silently "succeeding" with nothing.

Runtime supply chain (taOS #329 review): an authenticated Store install must
not be able to choose what the serving runtime runs, so

- ``mlx-lm`` and ``mlx`` are pinned by ``MLX_LM_VERSION`` / ``MLX_VERSION``, and
  the whole transitive closure is pinned *and hashed* in the vendored locks
  ``mlx_lm_requirements_py311.txt`` / ``_py312`` / ``_py313`` (regenerate with
  ``scripts/gen-mlx-lock.py``);
- the runtime lives in its own venv, never in the interpreter the controller
  itself runs on;
- pip runs with ``--require-hashes --only-binary=:all:`` against that lock, so a
  package (or a wheel) whose digest is not listed fails the install instead of
  resolving off an index.

Serving is deliberately out of scope here: ``mlx_lm.server`` serves one model
per process, so the service belongs to the backend-activation slice, not to a
model install (compare llama.cpp: its launchd/systemd unit ships in
``scripts/install-llama-cpp.sh`` while ``llamacpp_installer.py`` holds the port
and the ``llamacpp_is_running()`` probe). Accordingly ``install()`` reports no
endpoint -- it must not claim one nothing serves -- and
``mlx_server_is_running()`` is the health probe the service slice and the setup
checklist will use.

Configuration via env vars:

- ``TAOS_MLX_VENV`` -- directory of the runtime venv (default:
  ``<install root>/apps/mlx-runtime/venv``).
- ``TAOS_MLX_PYTHON`` -- interpreter used to *create* that venv (default:
  ``sys.executable``).
- ``TAOS_MLX_PORT`` -- port the OpenAI-compatible MLX server is expected on
  (default ``7837``; reserved in ``port_allocator.RESERVED_PORTS``).
- ``TAOS_MODELS_ROOT`` -- shared model tree root (honoured by
  ``model_paths.models_root()``).
"""
from __future__ import annotations

import logging
import os
import platform
import shutil
import socket
import sys
import urllib.request
from pathlib import Path
from typing import Any

from tinyagentos.hardware import is_apple_silicon
from tinyagentos.installers.base import AppInstaller, run_cmd
from tinyagentos.installers.download_installer import DownloadInstaller
from tinyagentos.installers.hf_multi_installer import HFMultiInstaller
from tinyagentos.installers.model_paths import (
    family_from_manifest,
    models_root,
)

logger = logging.getLogger(__name__)

BACKEND_ID = "mlx"

# Pinned MLX serving runtime. Both pins are load-bearing: `mlx-lm` declares
# `mlx>=...` only under a `platform_system == "Darwin"` marker, which pip does
# not evaluate when it resolves for a foreign platform, so the locks (and this
# installer) carry `mlx` explicitly. Bump either pin and regenerate the locks
# with scripts/gen-mlx-lock.py.
MLX_LM_VERSION = "0.31.3"
MLX_VERSION = "0.32.3"

# The pip name (mlx-lm) differs from the import name (mlx_lm); the post-install
# check imports the module *and* compares the distribution version.
MLX_LM_PACKAGE = "mlx-lm"
_MLX_LM_MODULE = "mlx_lm"

#: CPython minors the vendored locks cover. taOS supports 3.11-3.13
#: (pyproject.toml), and a hash-pinned lock is per-interpreter because the
#: wheels are abi-tagged.
SUPPORTED_PYTHONS: tuple[str, ...] = ("3.11", "3.12", "3.13")

_REQUIREMENTS_PREFIX = "mlx_lm_requirements_py"

# taOS default port for the MLX OpenAI-compatible server (mlx_lm.server).
# Reserved next to 7833 (rkllama), 7834 (LiteLLM), 7835 (llama.cpp), 7836
# (hailo-ollama) and 7838 (LLM gateway agent listener) in
# installers/port_allocator.RESERVED_PORTS so a Store app host-port can never
# squat it.
DEFAULT_PORT = 7837


def _default_port() -> int:
    """Resolve the MLX server port from TAOS_MLX_PORT or DEFAULT_PORT."""
    raw = os.environ.get("TAOS_MLX_PORT", str(DEFAULT_PORT))
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "TAOS_MLX_PORT=%r is not an integer; using default %d",
            raw,
            DEFAULT_PORT,
        )
        return DEFAULT_PORT


def mlx_runtime_venv() -> Path:
    """Directory of the venv that holds the MLX serving runtime.

    Its own venv, not the controller's: the runtime is a pinned, hash-verified
    dependency set, and installing it beside taOS's own packages would let a
    Store install change what the controller process runs on. Mirrors
    ``PipInstaller``'s ``<install root>/apps/<app_id>/venv`` convention.
    """
    override = os.environ.get("TAOS_MLX_VENV", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "apps" / "mlx-runtime" / "venv"


def mlx_runtime_installed() -> bool:
    """True when the runtime venv holds an interpreter.

    A path check on purpose: callers (scheduler discovery at boot, the setup
    checklist) only need "is the runtime there", not a subprocess.
    """
    return (mlx_runtime_venv() / "bin" / "python").exists()


def mlx_server_is_running(timeout: float = 1.0) -> bool:
    """True if a live mlx_lm.server answers the OpenAI-compatible API locally.

    ``mlx_lm.server`` serves ``/v1/models`` (the endpoint the `mlx` backend
    adapter and the OpenAI SDK both use), so that is the probe: a port that
    accepts a connection but answers nothing is not a running runtime.
    Mirrors ``llamacpp_installer.llamacpp_is_running()``.

    Callers should run this off the event loop (``asyncio.to_thread``) since it
    blocks on socket I/O.
    """
    port = _default_port()
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            pass
    except OSError:
        return False
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/models")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def requirements_file(python_version: str) -> Path | None:
    """Vendored hash-pinned lock for a ``"3.12"``-style interpreter version."""
    if python_version not in SUPPORTED_PYTHONS:
        return None
    tag = python_version.replace(".", "")
    path = Path(__file__).with_name(f"{_REQUIREMENTS_PREFIX}{tag}.txt")
    return path if path.exists() else None


class MLXInstaller(AppInstaller):
    """Install MLX models for serving via `mlx-lm` on Apple Silicon."""

    def __init__(
        self,
        *,
        pip_python: str | None = None,
        models_dir: Path | str | None = None,
        venv_dir: Path | str | None = None,
        port: int | None = None,
        pip_timeout: int = 1800,
    ):
        # Interpreter used to create the runtime venv. Defaults to the
        # interpreter taOS itself runs under, so the venv always matches the
        # platform taOS was installed for.
        self.pip_python = pip_python or os.environ.get("TAOS_MLX_PYTHON") or sys.executable
        # Tests pass a tmp root; production leaves it None so model_paths'
        # models_root() (TAOS_MODELS_ROOT) decides.
        self._models_dir = Path(models_dir) if models_dir else None
        self._venv_dir = Path(venv_dir) if venv_dir else None
        self.port = port if port is not None else _default_port()
        self.pip_timeout = pip_timeout

    def _root(self) -> Path:
        return self._models_dir if self._models_dir is not None else models_root()

    def _venv(self) -> Path:
        return self._venv_dir if self._venv_dir is not None else mlx_runtime_venv()

    def _venv_python(self) -> Path:
        return self._venv() / "bin" / "python"

    def _target_dir(self, app_id: str) -> Path:
        """Install dir for *app_id* in the shared ``<root>/mlx/<family>/<id>`` layout."""
        return self._root() / BACKEND_ID / family_from_manifest(app_id) / app_id

    def _contained(self, target: Path) -> Path | None:
        """Resolved *target* when it stays inside the MLX backend root, else None.

        ``app_id`` arrives from a catalog manifest, but a model download writes
        through this path and uninstall deletes it recursively, so it is
        validated at the boundary rather than trusted.
        """
        backend_root = (self._root() / BACKEND_ID).resolve()
        resolved = target.resolve()
        return resolved if resolved.is_relative_to(backend_root) else None

    async def _runtime_version(self) -> str:
        """Version of `mlx-lm` installed in the runtime venv, or "".

        One call answers both halves of "is the runtime usable": the
        distribution metadata gives the version, and importing ``mlx_lm`` in
        the same interpreter proves the package actually loads there.
        """
        code, output = await run_cmd(
            [
                str(self._venv_python()),
                "-c",
                f"import importlib.metadata as m, {_MLX_LM_MODULE}; "
                f"print(m.version('{MLX_LM_PACKAGE}'))",
            ],
            timeout=120,
        )
        return output.strip() if code == 0 else ""

    async def _runtime_python_version(self) -> str:
        """``"3.12"``-style version of the runtime venv interpreter, or ""."""
        code, output = await run_cmd(
            [
                str(self._venv_python()),
                "-c",
                "import sys; print('%d.%d' % sys.version_info[:2])",
            ],
            timeout=60,
        )
        return output.strip() if code == 0 else ""

    async def _create_venv(self) -> tuple[bool, str]:
        venv_dir = self._venv()
        try:
            venv_dir.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return False, f"cannot create {venv_dir.parent}: {exc}"
        code, output = await run_cmd(
            [self.pip_python, "-m", "venv", str(venv_dir)],
            timeout=600,
        )
        if code != 0:
            return False, (
                f"creating the MLX runtime venv failed (exit {code}): "
                f"{output.strip()[-500:]}"
            )
        return True, ""

    async def _ensure_mlx_lm(self) -> tuple[bool, str]:
        """Make the pinned `mlx-lm` runtime available in its own venv.

        Returns ``(ok, error)``. Idempotent and offline once the pinned version
        is in place: an install that already has it never touches an index
        again, so re-installing a model cannot upgrade (or downgrade) the
        runtime under a running server.
        """
        if not self._venv_python().exists():
            ok, err = await self._create_venv()
            if not ok:
                return False, err

        installed = await self._runtime_version()
        if installed == MLX_LM_VERSION:
            return True, ""

        python_version = await self._runtime_python_version()
        if not python_version:
            return False, (
                f"the MLX runtime venv at {self._venv()} has no usable "
                f"interpreter (expected {self._venv_python()})"
            )
        lock = requirements_file(python_version)
        if lock is None:
            return False, (
                f"no hash-pinned MLX runtime lock for Python {python_version} "
                f"(supported: {', '.join(SUPPORTED_PYTHONS)}); reinstall taOS "
                "for this interpreter or add a lock with "
                "scripts/gen-mlx-lock.py"
            )

        # --require-hashes refuses any requirement without a digest, and
        # --only-binary keeps pip from falling back to an sdist the lock has no
        # hash for. The lock pins mlx-lm, mlx and the whole closure.
        code, output = await run_cmd(
            [
                str(self._venv_python()),
                "-m", "pip", "install",
                "--disable-pip-version-check",
                "--no-input",
                "--only-binary=:all:",
                "--require-hashes",
                "-r", str(lock),
            ],
            timeout=self.pip_timeout,
        )
        if code != 0:
            return False, (
                f"installing the pinned MLX runtime failed (exit {code}): "
                f"{output.strip()[-500:]}"
            )

        # A successful pip is not proof the runtime is usable (a broken wheel, a
        # stale egg-link, an externally-managed-environment shim that "succeeds"
        # without installing), so verify by importing it in the venv that will
        # serve and checking that the pinned version is the one that landed.
        verified = await self._runtime_version()
        if not verified:
            return False, (
                f"{MLX_LM_PACKAGE} installed but not importable by "
                f"{self._venv_python()}"
            )
        if verified != MLX_LM_VERSION:
            return False, (
                f"{MLX_LM_PACKAGE} {verified} is installed but "
                f"{MLX_LM_VERSION} is pinned; the runtime venv is not the one "
                "this installer manages"
            )
        return True, ""

    async def install(
        self,
        app_id: str,
        install_config: dict,
        variant: dict | None = None,
        **kwargs: Any,
    ) -> dict:
        if not variant:
            return {"success": False, "error": "MLX install requires a variant"}

        if not is_apple_silicon():
            return {
                "success": False,
                "error": (
                    "MLX requires Apple Silicon (macOS on arm64); this host is "
                    f"{sys.platform}/{platform.machine()}. Install a variant for a "
                    "backend this machine has (llama-cpp, ollama, ...)."
                ),
            }

        # First non-blank of mlx_repo / hf_repo wins. Stripping before the
        # choice matters: a whitespace-only mlx_repo is truthy, and treating it
        # as "set" would discard a perfectly good hf_repo and then reject the
        # install.
        repo = ""
        for key in ("mlx_repo", "hf_repo"):
            candidate = str(variant.get(key) or "").strip()
            if candidate:
                repo = candidate
                break
        if not repo and not variant.get("download_url"):
            return {
                "success": False,
                "error": (
                    f"variant {variant.get('id')!r} has neither mlx_repo/hf_repo "
                    "(a HuggingFace MLX repo) nor download_url to install from"
                ),
            }

        # Everything this installer writes must land inside the mlx backend
        # root -- the same guard uninstall applies before it deletes. Checked
        # before the runtime step so an escaping app_id cannot trigger a venv
        # creation or an install as a side effect of being rejected.
        target = self._target_dir(app_id)
        if self._contained(target) is None:
            return {
                "success": False,
                "error": f"refusing to install {app_id!r}: target {target} is outside the mlx root",
            }

        ok, err = await self._ensure_mlx_lm()
        if not ok:
            return {"success": False, "error": err}

        # The dispatcher injects "backend" into install_config; default it so a
        # direct call still writes into the shared mlx tree rather than the
        # huggingface default one.
        install_config = dict(install_config or {})
        install_config.setdefault("backend", BACKEND_ID)

        if repo:
            # MLX repos are ordinary HF directories (config.json, tokenizer,
            # *.safetensors), so reuse the multi-file downloader -- overriding
            # the repo so an explicit mlx_repo wins over the variant's hf_repo.
            dl_variant = dict(variant)
            dl_variant["hf_repo"] = repo
            result = await HFMultiInstaller(_root_override=self._models_dir).install(
                app_id,
                install_config=install_config,
                variant=dl_variant,
                **kwargs,
            )
        else:
            result = await DownloadInstaller(models_dir=self._models_dir).install(
                app_id,
                install_config=install_config,
                variant=variant,
                **kwargs,
            )

        if not result.get("success"):
            return result

        # No endpoint here: mlx_lm.server is not started by this installer (see
        # the module docstring), so reporting one would advertise a service that
        # does not exist. What is true and useful is where the runtime landed:
        # the interpreter that holds the pinned `mlx-lm`.
        result["mlx_repo"] = repo
        result["mlx_python"] = str(self._venv_python())
        result["mlx_lm_version"] = MLX_LM_VERSION
        return result

    async def uninstall(self, app_id: str, **kwargs: Any) -> dict:
        """Remove this manifest's own directory from the mlx backend root.

        Scoped to ``<root>/mlx/<family>/<manifest_id>`` -- a manifest installed
        on several backends keeps its copies for the others. The shared runtime
        venv is deliberately left alone.
        """
        backend_root = (self._root() / BACKEND_ID).resolve()
        target = self._contained(self._target_dir(app_id))
        # The recursive delete below must never escape the mlx backend root,
        # whatever the caller passes as app_id ("../..", an absolute path).
        if target is None:
            return {
                "success": False,
                "error": (
                    f"refusing to remove {self._target_dir(app_id)}: "
                    f"outside {backend_root}"
                ),
            }
        if not target.exists():
            return {"success": True, "deleted": 0, "target_dir": str(target)}
        try:
            shutil.rmtree(target)
        except OSError as exc:
            return {
                "success": False,
                "error": f"failed to remove {target}: {exc}",
                "target_dir": str(target),
            }
        return {"success": True, "deleted": 1, "target_dir": str(target)}
