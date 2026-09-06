"""Gaming / Fullscreen App Detection (taOSmd).

Detects when a user launches a fullscreen application (game, video editor,
etc.) on a worker machine tagged as a "gaming PC" or "interactive".
Triggers yield_resources() on the resource manager to throttle the worker.

Platform support:
  - Linux: checks /proc for known game engines, monitors X11/Wayland
    fullscreen state via xdotool/xprop
  - Windows: checks for fullscreen DirectX/Vulkan processes (future)
  - macOS: checks for fullscreen NSApplication state (future)

This runs as a lightweight poller on the worker side — not on the
controller/Pi. It's opt-in via worker config.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Known game engine process patterns (lowercase)
GAME_PROCESS_PATTERNS = [
    # Steam / Proton / Wine
    r"reaper.*steam",
    r"proton",
    r"wine.*\.exe",
    r"gamescope",
    r"mangohud",
    # Unreal Engine
    r"unreal",
    r"ue4-.*-shipping",
    r"ue5-.*-shipping",
    # Unity
    r"unity.*player",
    # Godot
    r"godot",
    # Common game launchers
    r"steam_app_\d+",
    r"lutris",
    r"heroic",
    # GPU-heavy creative apps (video editing, 3D)
    r"davinci.*resolve",
    r"blender",
    r"kdenlive",
    r"obs-studio",
    r"obs64",
]

# Compile patterns once
_GAME_PATTERNS = [re.compile(p, re.IGNORECASE) for p in GAME_PROCESS_PATTERNS]


def detect_game_processes() -> list[dict]:
    """Check running processes for known game engines / GPU-heavy apps.

    Returns list of {pid, name, matched_pattern} for detected games.
    """
    found = []
    try:
        # Read all process names from /proc
        for pid_dir in Path("/proc").iterdir():
            if not pid_dir.name.isdigit():
                continue
            try:
                comm = (pid_dir / "comm").read_text().strip()
                cmdline = (pid_dir / "cmdline").read_text().replace("\x00", " ").strip()
                check_str = f"{comm} {cmdline}".lower()

                for pattern in _GAME_PATTERNS:
                    if pattern.search(check_str):
                        found.append({
                            "pid": int(pid_dir.name),
                            "name": comm,
                            "matched_pattern": pattern.pattern,
                        })
                        break
            except (PermissionError, FileNotFoundError, ProcessLookupError):
                continue
    except Exception as e:
        logger.debug("Process scan failed: %s", e)
    return found


def detect_fullscreen_x11() -> bool:
    """Check if any window is fullscreen via xdotool (X11 only)."""
    try:
        result = subprocess.run(
            ["xdotool", "getactivewindow", "getwindowgeometry", "--shell"],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode != 0:
            return False

        # Parse window geometry
        width = height = 0
        for line in result.stdout.split("\n"):
            if line.startswith("WIDTH="):
                width = int(line.split("=")[1])
            elif line.startswith("HEIGHT="):
                height = int(line.split("=")[1])

        # Get screen resolution
        screen_result = subprocess.run(
            ["xdpyinfo"], capture_output=True, text=True, timeout=2,
        )
        if screen_result.returncode == 0:
            for line in screen_result.stdout.split("\n"):
                if "dimensions:" in line:
                    match = re.search(r"(\d+)x(\d+)", line)
                    if match:
                        screen_w = int(match.group(1))
                        screen_h = int(match.group(2))
                        if width >= screen_w * 0.95 and height >= screen_h * 0.95:
                            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return False


def detect_gpu_heavy_process(threshold_pct: int = 70) -> bool:
    """Check if any non-Ollama process is using significant GPU.

    Uses nvidia-smi to list processes and their GPU usage.
    Returns True if a non-memory-system process exceeds the threshold.
    """
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return False

        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 3:
                continue
            process_name = parts[1].lower()
            used_mb = int(float(parts[2]))

            # Skip Ollama and memory-system processes
            if any(skip in process_name for skip in ["ollama", "python", "rkllm"]):
                continue

            # If any other process is using significant VRAM, it's a game/app
            if used_mb > 500:  # >500MB VRAM = likely a game or heavy app
                return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return False


class GamingDetector:
    """Polls for gaming/fullscreen activity and triggers resource yielding."""

    def __init__(
        self,
        resource_manager=None,
        poll_interval: int = 10,
        cooldown: int = 600,
    ):
        self._rm = resource_manager
        self._poll_interval = poll_interval
        self._cooldown = cooldown  # Seconds after game exits before reclaiming
        self._game_active: bool = False
        self._game_exited_at: float | None = None
        self._last_detected: list[dict] = []

    @property
    def game_active(self) -> bool:
        return self._game_active

    async def check(self) -> dict:
        """Run one detection cycle. Returns status dict.

        Call this periodically (e.g., every 10 seconds) from the worker's
        main loop.
        """
        games = detect_game_processes()
        fullscreen = detect_fullscreen_x11()
        gpu_heavy = detect_gpu_heavy_process()

        detected = bool(games) or fullscreen or gpu_heavy
        now = time.time()

        if detected and not self._game_active:
            # Game just started — yield resources
            self._game_active = True
            self._game_exited_at = None
            self._last_detected = games
            logger.info("Gaming detected: %s — yielding resources",
                       [g["name"] for g in games] if games else "fullscreen/GPU")
            if self._rm:
                await self._rm.yield_resources()
            return {"status": "yielded", "reason": "gaming_detected", "games": games}

        elif not detected and self._game_active:
            # Game may have exited — start cooldown
            if self._game_exited_at is None:
                self._game_exited_at = now
                return {"status": "cooldown", "seconds_remaining": self._cooldown}

            elapsed = now - self._game_exited_at
            if elapsed >= self._cooldown:
                # Cooldown expired — reclaim
                self._game_active = False
                self._game_exited_at = None
                self._last_detected = []
                logger.info("Gaming ended %ds ago — reclaiming resources", int(elapsed))
                if self._rm:
                    await self._rm.reclaim_resources()
                return {"status": "reclaimed", "idle_seconds": int(elapsed)}
            else:
                return {"status": "cooldown", "seconds_remaining": int(self._cooldown - elapsed)}

        elif detected and self._game_active:
            # Still gaming — reset exit timer
            self._game_exited_at = None
            return {"status": "gaming", "games": games}

        else:
            # No game, not active — normal
            return {"status": "idle"}

    async def force_yield(self) -> dict:
        """Manually trigger yield (from system tray)."""
        self._game_active = True
        self._game_exited_at = None
        if self._rm:
            await self._rm.yield_resources()
        return {"status": "yielded", "reason": "manual"}

    async def force_reclaim(self) -> dict:
        """Manually reclaim (from system tray)."""
        self._game_active = False
        self._game_exited_at = None
        if self._rm:
            await self._rm.reclaim_resources()
        return {"status": "reclaimed", "reason": "manual"}
