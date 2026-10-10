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

Serving is a managed service (taOS #329 review): ``mlx_lm.server`` has no router
mode -- one process serves exactly ONE model -- so ``install()`` pins the model
it just downloaded in a user launchd agent (``scripts/install-mlx-server.sh``,
``~/Library/LaunchAgents/com.taos.mlx-server.plist``), health-gates it on
``GET /v1/models`` and only then reports ``endpoint`` / ``runtime_location``.
``mlx_server_is_running()`` is the probe both halves use, so "the install
reported an endpoint" and "the setup checklist sees a backend" cannot disagree.
Installing another MLX model re-points the agent at the newer one (the last
install wins); serving several at once needs per-model spawn in the LLM proxy,
which is a separate slice. Uninstalling the served model unloads the agent with
it -- launchd's ``KeepAlive`` would otherwise restart a server against a deleted
model directory forever.

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

import asyncio
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

# The serving half: the launchd agent is installed and started by the shipped
# script, which owns the plist (single source of truth) and health-gates itself.
# This side then re-probes with ``mlx_server_is_running()`` -- the same function
# the setup checklist calls -- before it may report an endpoint.
_SERVE_PROBE_ATTEMPTS = 5
_SERVE_PROBE_INTERVAL_S = 1.0
_DEFAULT_SERVE_TIMEOUT_S = 180

#: Exit code ``scripts/install-mlx-server.sh --uninstall`` uses when it left an
#: agent loaded on purpose, because that agent pins a different model. Anything
#: else non-zero is a failure.
_AGENT_LEFT_RUNNING_EXIT_CODE = 3


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


def mlx_serving_script() -> Path:
    """Path to the shipped launchd-agent installer for the MLX server.

    Resolved relative to this package the same way the default runtime venv is
    (``<install root>/scripts/install-mlx-server.sh``), so a source install and
    an installed tree both find it.
    """
    return Path(__file__).resolve().parents[2] / "scripts" / "install-mlx-server.sh"


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


def mlx_server_is_running(timeout: float = 1.0, port: int | None = None) -> bool:
    """True if a live mlx_lm.server answers the OpenAI-compatible API locally.

    ``mlx_lm.server`` serves ``/v1/models`` (the endpoint the `mlx` backend
    adapter and the OpenAI SDK both use), so that is the probe: a port that
    accepts a connection but answers nothing is not a running runtime. Verified
    against the pinned ``mlx-lm`` 0.31.3 wheel, whose server also answers
    ``/health`` with ``{"status": "ok"}``; ``/v1/models`` is the stronger check
    because it is the surface taOS actually calls. Mirrors
    ``llamacpp_installer.llamacpp_is_running()``.

    ``port`` defaults to ``TAOS_MLX_PORT`` / ``DEFAULT_PORT``; the installer
    passes the port it pinned the agent to.

    Callers should run this off the event loop (``asyncio.to_thread``) since it
    blocks on socket I/O.
    """
    port = _default_port() if port is None else port
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
        serve_timeout: int = _DEFAULT_SERVE_TIMEOUT_S,
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
        # Wall-clock cap for the serving step (the script health-gates for up
        # to 90 s of that on its own).
        self.serve_timeout = serve_timeout

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

    async def _serve_model(self, model_dir: Path) -> tuple[bool, str]:
        """Pin *model_dir* in the launchd agent and health-gate it.

        Returns ``(serving, error)``. ``serving`` is True only when
        ``GET /v1/models`` answered on this installer's port, i.e. exactly when
        ``install()`` may report an endpoint.

        The shipped script owns the plist and does the loading; it health-gates
        for up to 90 s itself, and this method then re-probes with
        ``mlx_server_is_running()``, the function the setup checklist uses, so
        the endpoint claim rests on the surface the `mlx` backend adapter
        actually talks to.
        """
        if sys.platform != "darwin":
            return False, f"MLX serving is macOS-only (this host is {sys.platform})"
        script = mlx_serving_script()
        if not script.exists():
            return False, f"the MLX serving script is missing at {script}"

        code, output = await run_cmd(
            [
                str(script),
                "--model", str(model_dir),
                "--venv", str(self._venv()),
                "--port", str(self.port),
            ],
            timeout=self.serve_timeout,
        )
        if code != 0:
            tail = output.strip().splitlines()
            detail = tail[-1] if tail else "no output"
            return False, f"starting the MLX server failed (exit {code}): {detail[:400]}"

        for _ in range(_SERVE_PROBE_ATTEMPTS):
            if await asyncio.to_thread(
                mlx_server_is_running, 1.0, self.port
            ):
                return True, ""
            await asyncio.sleep(_SERVE_PROBE_INTERVAL_S)
        return False, (
            f"the MLX server did not answer GET /v1/models on "
            f"http://127.0.0.1:{self.port} after the launchd agent started"
        )

    async def _unload_agent(self, model_dir: Path) -> tuple[str, str]:
        """Unload the serving agent when it pins *model_dir*.

        Returns ``(state, error)`` with ``state`` one of ``"unloaded"`` (the
        plist is gone, so nothing serves this model any more), ``"left-running"``
        (the agent pins a different model and is deliberately left alone) or
        ``"failed"``. The script owns that decision and reports it in its exit
        code (see the script's `--uninstall` contract).
        """
        script = mlx_serving_script()
        if not script.exists():
            return "failed", f"the MLX serving script is missing at {script}"
        code, output = await run_cmd(
            [
                str(script),
                "--uninstall",
                "--model", str(model_dir),
                "--venv", str(self._venv()),
            ],
            timeout=60,
        )
        if code == 0:
            return "unloaded", ""
        if code == _AGENT_LEFT_RUNNING_EXIT_CODE:
            return "left-running", ""
        return "failed", (output.strip() or f"exited {code}")[-400:]

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

        result["mlx_repo"] = repo
        result["mlx_python"] = str(self._venv_python())
        result["mlx_lm_version"] = MLX_LM_VERSION

        # Serving half (taOS #329 review): pin the model that just landed in
        # the launchd agent and report an endpoint only once it answers. Never
        # advertise a service nothing runs.
        if not repo:
            # The download_url fallback lands one file, and `mlx_lm.server`
            # loads a model *directory*: there is nothing to point the agent
            # at, so no endpoint is claimed for it.
            result["mlx_serving"] = False
            result["mlx_serving_note"] = (
                "a download_url variant saves a single file; mlx_lm.server "
                "serves an MLX model directory, so install an mlx_repo/hf_repo "
                "variant to serve this backend"
            )
            return result

        reported = Path(str(result.get("target_dir") or ""))
        # Keep the resolved path, not just the verdict: the serving agent must
        # be pinned to the same directory string uninstall later greps for in
        # the plist, and a symlinked models root (macOS /var -> /private/var, or
        # a symlinked TAOS_MODELS_ROOT) makes the two differ.
        target = self._contained(reported)
        if target is None:
            # Same boundary rule as the download and the delete below: a
            # server must not be pointed outside the mlx backend root.
            result["mlx_serving"] = False
            result["mlx_serving_error"] = (
                f"the downloader reported {result.get('target_dir')!r}, which is "
                "not inside the mlx backend root; not starting a server on it"
            )
            return result

        serving, serve_err = await self._serve_model(target)
        result["mlx_serving"] = serving
        if not serving:
            # The weights are on disk and reusable; what is NOT true is that
            # something serves them, so the endpoint stays out of the result.
            result["mlx_serving_error"] = serve_err
            logger.warning(
                "MLX model %s is installed at %s but its server is not up: %s",
                app_id,
                target,
                serve_err,
            )
            return result

        result["mlx_model_dir"] = str(target)
        result["endpoint"] = f"http://127.0.0.1:{self.port}/v1"
        result["runtime_location"] = {
            "host": "127.0.0.1",
            "port": self.port,
            "backend": BACKEND_ID,
        }
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
        result: dict[str, Any] = {"success": True, "deleted": 1, "target_dir": str(target)}
        if sys.platform == "darwin":
            # The launchd agent KeepAlive-restarts whatever model its plist
            # pins, so removing the served model without unloading it would
            # restart a server against a directory that no longer exists.
            state, unload_err = await self._unload_agent(target)
            result["mlx_agent_state"] = state
            # True only when this model's agent is actually gone: an agent left
            # running on purpose (it pins a different model) is not "unloaded".
            result["mlx_agent_unloaded"] = state == "unloaded"
            if state == "failed":
                result["mlx_agent_error"] = unload_err
                logger.warning(
                    "MLX model %s removed but its serving agent was not unloaded: %s",
                    app_id,
                    unload_err,
                )
        return result
