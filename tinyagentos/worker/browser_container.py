from __future__ import annotations

"""Neko Chromium container runner for the browser-worker.

Manages per-session Neko containers (WebRTC-streamed Chromium) on a capable
host node.  In ``mock=True`` mode all subprocess calls are skipped so the
module can be used in unit tests without a Docker daemon.
"""

import asyncio
import logging
import secrets
import shutil
import socket
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


def _detect_tailscale_ip() -> str | None:
    """Return the Tailscale IPv4 address of this host, or None if unavailable.

    Tries ``tailscale ip -4`` first (most reliable).  Falls back to looking
    for the ``tailscale0`` interface via netifaces when the CLI is absent.
    Returns None when Tailscale is not installed or not running -- callers
    treat None as "no Tailscale" and omit the extra NAT1TO1 entry.
    """
    if shutil.which("tailscale"):
        try:
            result = subprocess.run(
                ["tailscale", "ip", "-4"],
                capture_output=True, text=True, timeout=2,
            )
            ip = result.stdout.strip()
            if result.returncode == 0 and ip:
                return ip
        except Exception:
            pass

    try:
        import netifaces  # type: ignore[import-untyped]
        addrs = netifaces.ifaddresses("tailscale0")
        entries = addrs.get(netifaces.AF_INET, [])
        if entries:
            return entries[0].get("addr")
    except Exception:
        pass

    return None


def _resolve_connecting_ip(host_header: str) -> str | None:
    """Resolve the connecting host from a Host header value to an IPv4 string.

    Strips any port suffix.  If the result is already an IP literal it is
    returned as-is.  If it is a hostname, socket.gethostbyname resolves it.
    Returns None on any failure so callers can fall back to the LAN node_ip.
    """
    hostname = host_header.split(":")[0].strip() if host_header else ""
    if not hostname:
        return None
    try:
        return socket.gethostbyname(hostname)
    except Exception:
        return None


def build_nat1to1(node_ip: str, connecting_host_ip: str | None = None) -> str:
    """Return the single IP to use for NEKO_WEBRTC_NAT1TO1.

    neko/pion requires exactly ONE IP.  When ``connecting_host_ip`` is
    provided (resolved from the client's Host/X-Forwarded-Host header at
    session-create time) it is used directly, so WebRTC candidates match the
    address the user actually connected from (LAN, Tailscale, etc.).

    Falls back to ``node_ip`` when no connecting IP is available.
    Never emits a comma-separated value.
    """
    if connecting_host_ip:
        return connecting_host_ip
    return node_ip

DEFAULT_NEKO_IMAGE = "ghcr.io/m1k1o/neko/chromium:latest"
DEFAULT_NEKO_GPU_IMAGE = "ghcr.io/m1k1o/neko/nvidia-chromium:latest"
# Custom CDP-enabled image (option C foundation, #<PR>).
# Chromium >=148 from trixie-security + DeveloperToolsAvailability=0 +
# --remote-debugging-port=9222 bound to 127.0.0.1 + --enable-dev-shm-usage.
# Requires --shm-size=4g at container run time.
# Built for arm64 (RK3588 primary) + amd64.
DEFAULT_NEKO_CDP_IMAGE = "ghcr.io/jaylfc/taos-neko-cdp:latest"
# RK3588 uses a dedicated image built on top of the CDP image (so it keeps
# Chromium >=148 + CDP) plus the Rockchip MPP + gstreamer-rockchip mpph264enc
# plugin for VPU H.264 encode (#624). Validated live on the Pi: mpph264enc
# encodes 720p on the VPU when /dev/mpp_service, /dev/dri and /dev/rga are
# passed (resolve_neko_image supplies all three). rk3588 hosts always resolve
# to this image; there is no automatic resolver-level fallback to the CDP image.
DEFAULT_NEKO_RK3588_IMAGE = "ghcr.io/jaylfc/taos-neko-rk3588:latest"
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
    nat1to1_ip: str | None = None,
) -> list[str]:
    """Return the full ``docker run`` argv (starting with 'docker') for a Neko
    Chromium session, per the validated spike recipe.

    When ``mobile=True`` the container runs portrait (800x1600@30) and mounts
    the mobile Chromium supervisord config so Chromium presents a mobile UA,
    touch events, and a 3x device scale factor.

    ``shm_size`` controls ``--shm-size``. The default is ``"4g"`` -- required
    by the CDP-enabled image (``DEFAULT_NEKO_CDP_IMAGE``) for stable page
    sessions; 4g is safe for all images and strictly better than the old 2g.

    ``nat1to1_ip`` overrides the NEKO_WEBRTC_NAT1TO1 value.  Pass the IP the
    connecting client used (from the Host header) so pion receives the single
    IP it expects.  Falls back to ``node_ip`` when not provided.
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
        "-e", f"NEKO_WEBRTC_NAT1TO1={build_nat1to1(node_ip, nat1to1_ip)}",
        f"--shm-size={shm_size}",
        "-v", f"{profile_volume}:{NEKO_PROFILE_MOUNT}",
    ]
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

    async def start(self, *, session_id: str, profile_volume: str, mobile: bool = False,
                   nat1to1_ip: str | None = None) -> dict:
        """Start a Neko container and return connection details.

        Returns a dict with keys: container_id, neko_url, cdp_url,
        http_port, epr_lo, epr_hi.

        When ``mobile=True`` the container runs portrait (800x1600@30) with a
        mobile Chromium UA + touch events so sites serve mobile layouts.

        ``nat1to1_ip`` is the single IP to advertise for WebRTC NAT1TO1 -- it
        should be the IP the client connected from (resolved from the request
        Host header).  Falls back to ``self.node_ip`` when not provided.
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
        spec = resolve_neko_image(self.hw_profile)
        # CDP is available when the image is the CDP-enabled custom build.
        # The port is bound to 127.0.0.1 inside the container; the launcher
        # accesses it via `docker exec` / a loopback port binding on the host.
        # None for images that don't expose CDP (stock Neko, GPU image).
        # The RK3588 image is built FROM the CDP image, so it also exposes CDP.
        cdp_url = (
            "http://127.0.0.1:9222"
            if spec.image in (DEFAULT_NEKO_CDP_IMAGE, DEFAULT_NEKO_RK3588_IMAGE)
            else None
        )

        if self.mock:
            container_id = f"mock-neko-{session_id[:8]}"
        else:
            image = spec.image
            await self._ensure_image(image)
            # nat1to1_ip is resolved at the route layer (the blocking DNS
            # lookup is offloaded there), so build_neko_run_args is now pure
            # string assembly and can be called directly.
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
                nat1to1_ip=nat1to1_ip,
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
