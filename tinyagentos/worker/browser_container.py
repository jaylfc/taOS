from __future__ import annotations

"""Neko Chromium container runner for the browser-worker.

Manages per-session Neko containers (WebRTC-streamed Chromium) on a capable
host node.  In ``mock=True`` mode all subprocess calls are skipped so the
module can be used in unit tests without a Docker daemon.
"""

import asyncio
import logging
import secrets
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_NEKO_IMAGE = "ghcr.io/m1k1o/neko/chromium:latest"
DEFAULT_NEKO_GPU_IMAGE = "ghcr.io/m1k1o/neko/nvidia-chromium:latest"
# Custom CDP-enabled image (option C foundation, #<PR>).
# Chromium >=148 from trixie-security + DeveloperToolsAvailability=0 +
# --remote-debugging-port=9222 bound to 127.0.0.1 + --enable-dev-shm-usage.
# Requires --shm-size=4g at container run time.
# Built for arm64 (RK3588 primary) + amd64.
DEFAULT_NEKO_CDP_IMAGE = "ghcr.io/jaylfc/taos-neko-cdp:latest"
# RK3588 uses the CDP image so agent automation is available out of the box
# on the Pi. The rkmpp HW-encode layer (#624) will extend this image once
# the GStreamer Rockchip packages are validated; for now software encode is
# the fallback (RK3588 hardware decode of page content still works via the
# kernel driver even without GStreamer rkmpp).
DEFAULT_NEKO_RK3588_IMAGE = DEFAULT_NEKO_CDP_IMAGE
NEKO_SCREEN = "1280x720@30"
NEKO_SCREEN_MOBILE = "800x1600@30"
NEKO_PROFILE_MOUNT = "/home/neko"

# Path to the mobile Chromium supervisord config, relative to the repo root.
# browser_container.py lives at tinyagentos/worker/browser_container.py so
# three parents up reaches the repo root.
_REPO_ROOT = Path(__file__).parent.parent.parent
MOBILE_CHROMIUM_CONF = (
    _REPO_ROOT / "app-catalog" / "streaming" / "neko-browser" / "mobile-chromium.conf"
)


@dataclass
class NekoImageSpec:
    image: str
    encode: str                        # rkmpp | nvenc | vaapi | software
    device_args: list[str] = field(default_factory=list)  # paths for --device
    gpu: bool = False                  # add `--gpus all`


def resolve_neko_image(hw_profile) -> NekoImageSpec:
    """Pick the Neko image + encode path + container devices for a host.

    Keyed off HardwareProfile. Software encode is the universal fallback, so an
    unknown/None profile still yields a working (CPU-encoded) browser.
    """
    soc = (getattr(getattr(hw_profile, "cpu", None), "soc", "") or "").lower()
    gpu = getattr(hw_profile, "gpu", None)
    gpu_type = (getattr(gpu, "type", "") or "").lower()
    cuda = bool(getattr(gpu, "cuda", False))
    vulkan = bool(getattr(gpu, "vulkan", False))

    if "rk3588" in soc or "rk3576" in soc:
        return NekoImageSpec(
            image=DEFAULT_NEKO_RK3588_IMAGE,
            encode="rkmpp",
            device_args=["/dev/mpp_service", "/dev/dri", "/dev/rga"],
            gpu=False,
        )
    if cuda:
        return NekoImageSpec(image=DEFAULT_NEKO_GPU_IMAGE, encode="nvenc", gpu=True)
    if gpu_type in ("intel", "amd") or vulkan:
        return NekoImageSpec(image=DEFAULT_NEKO_IMAGE, encode="vaapi", device_args=["/dev/dri"])
    return NekoImageSpec(image=DEFAULT_NEKO_IMAGE, encode="software")


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
    device_args: list[str] | None = None,
    mobile: bool = False,
    shm_size: str = "4g",
    cdp_host_port: int | None = None,
) -> list[str]:
    """Return the full ``docker run`` argv (starting with 'docker') for a Neko
    Chromium session, per the validated spike recipe.

    When ``mobile=True`` the container runs portrait (800x1600@30) and mounts
    the mobile Chromium supervisord config so Chromium presents a mobile UA,
    touch events, and a 3x device scale factor.

    ``shm_size`` controls ``--shm-size``. The default is ``"4g"`` — required
    by the CDP-enabled image (``DEFAULT_NEKO_CDP_IMAGE``) for stable page
    sessions; 4g is safe for all images and strictly better than the old 2g.

    ``cdp_host_port`` — when provided, publishes the container's CDP port 9222
    to **``127.0.0.1:{cdp_host_port}``** on the host.  The bind address is
    always ``127.0.0.1`` (loopback-only).  The CDP controller co-locates with
    the browser on the same host and drives it over this local binding; CDP is
    never exposed over ``0.0.0.0`` or the Tailscale interface.  Only pass this
    for the CDP-enabled image (``DEFAULT_NEKO_CDP_IMAGE``); stock/GPU images
    do not bind CDP and the arg is ignored.
    """
    if image is None:
        image = DEFAULT_NEKO_GPU_IMAGE if gpu else DEFAULT_NEKO_IMAGE

    screen = NEKO_SCREEN_MOBILE if mobile else NEKO_SCREEN

    args = [
        "docker", "run", "-d", "--rm",
        "--name", container_name,
        "-p", f"{http_port}:8080",
        "-p", f"{epr_lo}-{epr_hi}:{epr_lo}-{epr_hi}/udp",
        "-e", f"NEKO_DESKTOP_SCREEN={screen}",
        "-e", f"NEKO_MEMBER_MULTIUSER_USER_PASSWORD={user_pwd}",
        "-e", f"NEKO_MEMBER_MULTIUSER_ADMIN_PASSWORD={admin_pwd}",
        "-e", f"NEKO_WEBRTC_EPR={epr_lo}-{epr_hi}",
        "-e", f"NEKO_WEBRTC_NAT1TO1={node_ip}",
        f"--shm-size={shm_size}",
        "-v", f"{profile_volume}:{NEKO_PROFILE_MOUNT}",
    ]
    if cdp_host_port is not None:
        # Loopback-only publish: 127.0.0.1:{host_port} → container:9222.
        # NEVER use 0.0.0.0 — the CDP endpoint must not be reachable from
        # the network (Tailscale or otherwise).
        args += ["-p", f"127.0.0.1:{cdp_host_port}:9222"]
    if mobile:
        args += ["-v", f"{MOBILE_CHROMIUM_CONF}:/etc/neko/supervisord/chromium.conf:ro"]
    for dev in device_args or []:
        args += ["--device", dev]
    if gpu:
        args += ["--gpus", "all"]
    args.append(image)
    return args


def build_volume_export_args(volume: str) -> list[str]:
    """docker run that streams the volume's contents to stdout as a tar."""
    return [
        "docker", "run", "--rm", "-v", f"{volume}:/from", "alpine",
        "tar", "-C", "/from", "-cf", "-", ".",
    ]


def build_volume_import_args(volume: str) -> list[str]:
    """docker run that reads a tar from stdin into the (auto-created) volume."""
    return [
        "docker", "run", "--rm", "-i", "-v", f"{volume}:/to", "alpine",
        "tar", "-C", "/to", "-xf", "-",
    ]


class PortAllocator:
    """Hands out a unique HTTP port + UDP EPR range + CDP host port per session.

    In-process, monotonic; good enough for one host running a handful of
    sessions.

    The CDP host port (``cdp_base + slot``) is published to ``127.0.0.1``
    only — never ``0.0.0.0`` — and maps to the container's port 9222 when
    the CDP-enabled image is used.
    """

    def __init__(
        self,
        *,
        http_base: int = 8800,
        epr_base: int = 59000,
        epr_span: int = 10,
        cdp_base: int = 19200,
    ) -> None:
        self._http_base = http_base
        self._epr_base = epr_base
        self._epr_span = epr_span
        self._cdp_base = cdp_base
        self._next_slot = 0
        # slot → http_port (for release)
        self._active: dict[int, int] = {}
        # freed slots available for re-use
        self._free: list[int] = []

    def allocate(self) -> tuple[int, int, int, int]:
        """Return ``(http_port, epr_lo, epr_hi, cdp_host_port)`` for a new session."""
        if self._free:
            slot = self._free.pop()
        else:
            slot = self._next_slot
            self._next_slot += 1
        http_port = self._http_base + slot
        epr_lo = self._epr_base + slot * self._epr_span
        epr_hi = epr_lo + self._epr_span - 1
        cdp_host_port = self._cdp_base + slot
        self._active[slot] = http_port
        return http_port, epr_lo, epr_hi, cdp_host_port

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
        hw_profile=None,
    ) -> None:
        self.node_ip = node_ip
        self.mock = mock
        self.gpu = gpu
        self.hw_profile = hw_profile
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

    async def start(self, *, session_id: str, profile_volume: str, mobile: bool = False) -> dict:
        """Start a Neko container and return connection details.

        Returns a dict with keys: container_id, neko_url, cdp_url,
        http_port, epr_lo, epr_hi.

        When ``mobile=True`` the container runs portrait (800x1600@30) with a
        mobile Chromium UA + touch events so sites serve mobile layouts.
        """
        http_port, epr_lo, epr_hi, cdp_host_port = self._allocator.allocate()
        # Full session_id (a uuid hex) — avoids name collisions from a
        # truncated prefix; Docker accepts the length.
        container_name = f"taos-neko-{session_id}"
        user_pwd = secrets.token_urlsafe(16)
        admin_pwd = secrets.token_urlsafe(16)

        # cast=1 omitted intentionally: the spike found cast=1 is VIEW-ONLY
        # (full-bleed but no mouse/keyboard control).  Interactive sessions
        # need it absent.
        neko_url = f"http://{self.node_ip}:{http_port}/?usr=neko&pwd={user_pwd}"
        spec = resolve_neko_image(self.hw_profile)
        is_cdp_image = spec.image == DEFAULT_NEKO_CDP_IMAGE
        # CDP URL points to the HOST-side loopback port that maps to the
        # container's 9222 — never the container-internal address.
        # None for stock/GPU images which do not expose a CDP endpoint.
        cdp_url = (
            f"http://127.0.0.1:{cdp_host_port}"
            if is_cdp_image
            else None
        )

        if self.mock:
            container_id = f"mock-neko-{session_id[:8]}"
        else:
            image = spec.image
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
                gpu=spec.gpu,
                image=image,
                device_args=spec.device_args,
                mobile=mobile,
                cdp_host_port=cdp_host_port if is_cdp_image else None,
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
            "cdp_host_port": cdp_host_port if is_cdp_image else None,
            "http_port": http_port,
            "epr_lo": epr_lo,
            "epr_hi": epr_hi,
            "image": spec.image,
            "encode": spec.encode,
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
