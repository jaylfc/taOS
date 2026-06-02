from __future__ import annotations

"""Neko Chromium container runner for the browser-worker.

Manages per-session Neko containers (WebRTC-streamed Chromium) on a capable
host node.  In ``mock=True`` mode all subprocess calls are skipped so the
module can be used in unit tests without a Docker daemon.
"""

import asyncio
import logging
import secrets

logger = logging.getLogger(__name__)

DEFAULT_NEKO_IMAGE = "ghcr.io/m1k1o/neko/chromium:latest"
DEFAULT_NEKO_GPU_IMAGE = "ghcr.io/m1k1o/neko/nvidia-chromium:latest"
NEKO_SCREEN = "1280x720@30"
NEKO_PROFILE_MOUNT = "/home/neko"


class BrowserContainerError(Exception):
    """Raised when a Docker operation for a Neko container fails."""


def build_neko_run_args(
    *,
    container_name: str,
    profile_volume: str,
    node_ip: str,
    http_port: int,
    epr_lo: int,
    epr_hi: int,
    user_pwd: str,
    admin_pwd: str,
    gpu: bool = False,
    image: str | None = None,
) -> list[str]:
    """Return the full ``docker run`` argv (starting with 'docker') for a Neko
    Chromium session, per the validated spike recipe."""
    if image is None:
        image = DEFAULT_NEKO_GPU_IMAGE if gpu else DEFAULT_NEKO_IMAGE

    args = [
        "docker", "run", "-d", "--rm",
        "--name", container_name,
        "-p", f"{http_port}:8080",
        "-p", f"{epr_lo}-{epr_hi}:{epr_lo}-{epr_hi}/udp",
        "-e", f"NEKO_DESKTOP_SCREEN={NEKO_SCREEN}",
        "-e", f"NEKO_MEMBER_MULTIUSER_USER_PASSWORD={user_pwd}",
        "-e", f"NEKO_MEMBER_MULTIUSER_ADMIN_PASSWORD={admin_pwd}",
        "-e", f"NEKO_WEBRTC_EPR={epr_lo}-{epr_hi}",
        "-e", f"NEKO_WEBRTC_NAT1TO1={node_ip}",
        "--shm-size=2g",
        "-v", f"{profile_volume}:{NEKO_PROFILE_MOUNT}",
    ]
    if gpu:
        args += ["--gpus", "all"]
    args.append(image)
    return args


class PortAllocator:
    """Hands out a unique HTTP port + a small UDP EPR range per session.

    In-process, monotonic; good enough for one host running a handful of
    sessions.
    """

    def __init__(
        self,
        *,
        http_base: int = 8800,
        epr_base: int = 59000,
        epr_span: int = 10,
    ) -> None:
        self._http_base = http_base
        self._epr_base = epr_base
        self._epr_span = epr_span
        self._next_slot = 0
        # slot → http_port (for release)
        self._active: dict[int, int] = {}
        # freed slots available for re-use
        self._free: list[int] = []

    def allocate(self) -> tuple[int, int, int]:
        """Return ``(http_port, epr_lo, epr_hi)`` for a new session."""
        if self._free:
            slot = self._free.pop()
        else:
            slot = self._next_slot
            self._next_slot += 1
        http_port = self._http_base + slot
        epr_lo = self._epr_base + slot * self._epr_span
        epr_hi = epr_lo + self._epr_span - 1
        self._active[slot] = http_port
        return http_port, epr_lo, epr_hi

    def release(self, http_port: int) -> None:
        """Return the slot corresponding to ``http_port`` to the free pool."""
        for slot, port in list(self._active.items()):
            if port == http_port:
                del self._active[slot]
                self._free.append(slot)
                return


class BrowserContainerRunner:
    """Start and stop per-session Neko Chromium containers."""

    def __init__(
        self,
        *,
        node_ip: str,
        mock: bool = False,
        gpu: bool = False,
        allocator: PortAllocator | None = None,
    ) -> None:
        self.node_ip = node_ip
        self.mock = mock
        self.gpu = gpu
        self._allocator = allocator or PortAllocator()

    async def _ensure_image(self, image: str) -> None:
        """Pull ``image`` if it is not already present locally."""
        if self.mock:
            return
        inspect_proc = await asyncio.create_subprocess_exec(
            "docker", "image", "inspect", image,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await inspect_proc.communicate()
        if inspect_proc.returncode == 0:
            return
        logger.info("pulling Neko image %s", image)
        pull_proc = await asyncio.create_subprocess_exec(
            "docker", "pull", image,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await pull_proc.communicate()
        if pull_proc.returncode != 0:
            detail = stderr.decode().strip()
            raise BrowserContainerError(f"docker pull {image} failed: {detail}")

    async def start(self, *, session_id: str, profile_volume: str) -> dict:
        """Start a Neko container and return connection details.

        Returns a dict with keys: container_id, neko_url, cdp_url,
        http_port, epr_lo, epr_hi.
        """
        http_port, epr_lo, epr_hi = self._allocator.allocate()
        # Full session_id (a uuid hex) — avoids name collisions from a
        # truncated prefix; Docker accepts the length.
        container_name = f"taos-neko-{session_id}"
        user_pwd = secrets.token_urlsafe(16)
        admin_pwd = secrets.token_urlsafe(16)

        # cast=1 omitted intentionally: the spike found cast=1 is VIEW-ONLY
        # (full-bleed but no mouse/keyboard control).  Interactive sessions
        # need it absent.
        neko_url = f"http://{self.node_ip}:{http_port}/?usr=neko&pwd={user_pwd}"
        cdp_url = None  # CDP not exposed by this image in Phase 1 (deferred to Phase 2)

        if self.mock:
            container_id = f"mock-neko-{session_id[:8]}"
        else:
            image = DEFAULT_NEKO_GPU_IMAGE if self.gpu else DEFAULT_NEKO_IMAGE
            await self._ensure_image(image)
            argv = build_neko_run_args(
                container_name=container_name,
                profile_volume=profile_volume,
                node_ip=self.node_ip,
                http_port=http_port,
                epr_lo=epr_lo,
                epr_hi=epr_hi,
                user_pwd=user_pwd,
                admin_pwd=admin_pwd,
                gpu=self.gpu,
                image=image,
            )
            try:
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()
                if proc.returncode != 0:
                    self._allocator.release(http_port)
                    detail = stderr.decode().strip()
                    logger.warning(
                        "docker run failed for session %s (rc=%s): %s",
                        session_id, proc.returncode, detail,
                    )
                    raise BrowserContainerError(
                        f"docker run failed (rc={proc.returncode}): {detail}"
                    )
                container_id = stdout.decode().strip()
            except BrowserContainerError:
                raise
            except Exception as exc:
                self._allocator.release(http_port)
                logger.warning("docker run failed for session %s: %s", session_id, exc)
                raise BrowserContainerError(str(exc)) from exc

        return {
            "container_id": container_id,
            "neko_url": neko_url,
            "cdp_url": cdp_url,
            "http_port": http_port,
            "epr_lo": epr_lo,
            "epr_hi": epr_hi,
        }

    async def stop(self, *, container_id: str, http_port: int | None = None) -> dict:
        """Stop a Neko container (keeps the profile volume) and release ports."""
        if not self.mock:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "docker", "stop", container_id,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.communicate()
            except Exception as exc:
                logger.warning("docker stop failed for %s: %s", container_id, exc)
        if http_port is not None:
            self._allocator.release(http_port)
        return {"ok": True}
