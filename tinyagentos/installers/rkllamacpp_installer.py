"""rk-llama.cpp installer — downloads GGUF, points llama-server at it.

rk-llama.cpp is the second NPU backend on Orange Pi (alongside rkllama).
It runs anything in GGUF format on the RK3588 NPU via the rknpu2 ggml
backend. We use it for models the rkllm-toolkit doesn't yet support
(Gemma 4, Qwen 3.5+, etc).

Unlike rkllama (which has its own ``/api/pull`` download flow), llama-server
expects a model file path on disk. So this installer:

1. Downloads the GGUF from the manifest's variant.download_url
2. Places it at ``<install_dir>/models/<app_id>.gguf``
3. Updates the symlink ``<install_dir>/models/active.gguf`` → that file
4. Enables + restarts the ``rkllamacpp`` systemd unit so llama-server
   picks up the new model

One model is "active" at a time. Switching = installing a different
manifest. This matches how llama-server is designed and keeps the unit
configuration simple.

Configuration via env vars (matched to install-rk-llama-cpp.sh):

- ``TAOS_RKLLAMACPP_DIR`` — install directory (default: ``~/rk-llama.cpp``)
- ``TAOS_RKLLAMACPP_PORT`` — server port (default: ``8090``)

Both default to the values used by ``install-rk-llama-cpp.sh``; if you
override one, override both consistently.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from pathlib import Path
from typing import Any

import httpx

from tinyagentos.installers.base import AppInstaller, run_cmd

logger = logging.getLogger(__name__)


def _default_install_dir() -> Path:
    """Resolve install dir from TAOS_RKLLAMACPP_DIR or fallback to ~/rk-llama.cpp."""
    override = os.environ.get("TAOS_RKLLAMACPP_DIR")
    return Path(override) if override else Path.home() / "rk-llama.cpp"


def _default_port() -> int:
    """Resolve port from TAOS_RKLLAMACPP_PORT or fallback to 8090."""
    raw = os.environ.get("TAOS_RKLLAMACPP_PORT", "8090")
    try:
        return int(raw)
    except ValueError:
        return 8090


SERVICE_NAME = "rkllamacpp"


class RkLlamaCppInstaller(AppInstaller):
    """Install GGUF models for serving via the rk-llama.cpp llama-server."""

    def __init__(
        self,
        install_dir: Path | str | None = None,
        port: int | None = None,
        timeout: int = 1800,
    ):
        self.install_dir = Path(install_dir) if install_dir else _default_install_dir()
        self.models_dir = self.install_dir / "models"
        self.port = port if port is not None else _default_port()
        self.timeout = timeout

    async def install(
        self,
        app_id: str,
        install_config: dict,
        variant: dict | None = None,
        **_: Any,
    ) -> dict:
        if not variant:
            return {
                "success": False,
                "error": "rk-llama.cpp install requires a variant (with download_url)",
            }
        url = variant.get("download_url")
        if not url:
            return {
                "success": False,
                "error": f"variant {variant.get('id')!r} missing download_url",
            }

        # Verify the binary is in place. install-rk-llama-cpp.sh handles this
        # during the controller setup; if the binary is missing, the chain
        # install would have already run scripts/install-rk-llama-cpp.sh
        # via ScriptInstaller before we got here.
        binary = self.install_dir / "bin" / "llama-server"
        if not binary.exists():
            return {
                "success": False,
                "error": (
                    f"rk-llama.cpp binary not found at {binary}. "
                    "Run scripts/install-rk-llama-cpp.sh first."
                ),
            }

        self.models_dir.mkdir(parents=True, exist_ok=True)
        target = self.models_dir / f"{app_id}.gguf"
        active_link = self.models_dir / "active.gguf"

        if target.exists():
            logger.info("rk-llama.cpp install: %s already present, reusing", target)
        else:
            logger.info("rk-llama.cpp install: downloading %s -> %s", url, target)
            try:
                await self._download(url, target, variant.get("sha256"))
            except Exception as exc:  # noqa: BLE001
                if target.exists():
                    target.unlink()
                return {"success": False, "error": f"download failed: {exc}"}

        # Atomic active-symlink update.
        tmp_link = active_link.with_suffix(".gguf.new")
        if tmp_link.exists() or tmp_link.is_symlink():
            tmp_link.unlink()
        tmp_link.symlink_to(target.name)
        os.replace(tmp_link, active_link)

        # Enable + restart the service. Failure here means the model file
        # is on disk but the runtime is not actually serving it — we report
        # success: False so the dispatcher can persist the install correctly
        # and the user can fix the systemd state without thinking the
        # download silently failed.
        try:
            await self._systemctl("enable", SERVICE_NAME)
            await self._systemctl("restart", SERVICE_NAME)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "rk-llama.cpp install: systemctl enable/restart failed: %s", exc
            )
            return {
                "success": False,
                "error": (
                    f"model file written to {target} but systemctl failed: {exc}. "
                    "Service is not serving — check `journalctl -u rkllamacpp` and "
                    "restart manually before the model becomes usable."
                ),
                "model_path": str(target),
            }

        # Wait for /health. If the server doesn't come up the model isn't
        # actually usable, even though the file is in place.
        verified = await self._wait_for_server(timeout_s=120)
        if not verified:
            return {
                "success": False,
                "error": (
                    f"model file written and service restarted, but "
                    f"http://localhost:{self.port}/health did not return 200 within "
                    "120s. Check `journalctl -u rkllamacpp` — common causes: model "
                    "too large for available RAM, NPU driver missing, port conflict."
                ),
                "model_path": str(target),
                "service_running": False,
            }

        return {
            "success": True,
            "app_id": app_id,
            "model_path": str(target),
            "active": True,
            "service_running": True,
            "endpoint": f"http://localhost:{self.port}",
        }

    async def uninstall(self, app_id: str) -> dict:
        target = self.models_dir / f"{app_id}.gguf"
        active_link = self.models_dir / "active.gguf"

        was_active = (
            active_link.is_symlink()
            and active_link.readlink().name == target.name
        )

        if target.exists():
            target.unlink()

        if was_active:
            try:
                await self._systemctl("stop", SERVICE_NAME)
                await self._systemctl("disable", SERVICE_NAME)
            except Exception as exc:  # noqa: BLE001
                logger.warning("rk-llama.cpp uninstall: systemctl stop failed: %s", exc)
            if active_link.is_symlink():
                active_link.unlink()

        return {"success": True, "status": "uninstalled", "was_active": was_active}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _download(
        self, url: str, dest: Path, expected_sha256: str | None
    ) -> None:
        part = dest.with_suffix(dest.suffix + ".part")
        if part.exists():
            part.unlink()
        sha = hashlib.sha256()
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                with open(part, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=1 << 16):
                        f.write(chunk)
                        sha.update(chunk)
        if expected_sha256 and sha.hexdigest() != expected_sha256:
            part.unlink()
            raise ValueError(
                f"sha256 mismatch: expected {expected_sha256}, got {sha.hexdigest()}"
            )
        os.replace(part, dest)

    async def _systemctl(self, action: str, unit: str) -> None:
        rc, out = await run_cmd(["sudo", "systemctl", action, unit])
        if rc != 0:
            raise RuntimeError(f"systemctl {action} {unit} failed: {out.strip()}")

    async def _wait_for_server(self, timeout_s: int = 120) -> bool:
        url = f"http://localhost:{self.port}/health"
        deadline = asyncio.get_event_loop().time() + timeout_s
        async with httpx.AsyncClient(timeout=2) as client:
            while asyncio.get_event_loop().time() < deadline:
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        return True
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(2)
        return False
