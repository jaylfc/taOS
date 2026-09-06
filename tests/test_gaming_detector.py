"""Tests for the gaming detector: process scanning, fullscreen detection,
GPU monitoring, and the GamingDetector state machine."""

from __future__ import annotations

import subprocess

import pytest

from tinyagentos.scheduling import gaming_detector as gd
from tinyagentos.scheduling.gaming_detector import GamingDetector


# ---- helpers -----------------------------------------------------------------

class _FakeProcEntry:
    """Mimics a /proc/<pid> directory with comm and cmdline files."""

    def __init__(self, pid: int, comm: str, cmdline: str = ""):
        self._pid = pid
        self._comm = comm
        self._cmdline = cmdline

    @property
    def name(self) -> str:
        return str(self._pid)

    def isdigit(self) -> bool:
        return True

    def __truediv__(self, other: str) -> "_FakeProcFile":
        if other == "comm":
            return _FakeProcFile(self._comm)
        if other == "cmdline":
            return _FakeProcFile(self._cmdline)
        raise FileNotFoundError(str(other))


class _FakeProcFile:
    def __init__(self, content: str):
        self._content = content

    def read_text(self) -> str:
        return self._content


def _make_proc(entries: list[_FakeProcEntry]):
    """Return a fake Path whose iterdir yields the given entries."""

    class _FakeProcPath:
        def iterdir(self):
            return iter(entries)

    return _FakeProcPath()


# ---- detect_game_processes ----------------------------------------------------

class TestDetectGameProcesses:
    def test_detects_steam_game(self, monkeypatch):
        entries = [
            _FakeProcEntry(1, "systemd"),
            _FakeProcEntry(42, "reaper-steam", "/usr/bin/reaper steam://run/123"),
        ]
        monkeypatch.setattr("tinyagentos.scheduling.gaming_detector.Path", lambda p: _make_proc(entries))

        result = gd.detect_game_processes()
        assert len(result) == 1
        assert result[0]["pid"] == 42
        assert result[0]["name"] == "reaper-steam"

    def test_detects_proton_process(self, monkeypatch):
        entries = [
            _FakeProcEntry(100, "proton", "/home/user/.steam/proton run game.exe"),
        ]
        monkeypatch.setattr("tinyagentos.scheduling.gaming_detector.Path", lambda p: _make_proc(entries))

        result = gd.detect_game_processes()
        assert len(result) == 1
        assert result[0]["matched_pattern"] == "proton"

    def test_detects_unreal_shipping(self, monkeypatch):
        entries = [
            _FakeProcEntry(200, "ue5-game-shipping", "/opt/game/Binaries/Linux/ue5-game-shipping"),
        ]
        monkeypatch.setattr("tinyagentos.scheduling.gaming_detector.Path", lambda p: _make_proc(entries))

        result = gd.detect_game_processes()
        assert len(result) == 1
        assert "ue5" in result[0]["matched_pattern"]

    def test_ignores_non_game_processes(self, monkeypatch):
        entries = [
            _FakeProcEntry(1, "systemd"),
            _FakeProcEntry(2, "kthreadd"),
            _FakeProcEntry(50, "nginx", "nginx: worker process"),
        ]
        monkeypatch.setattr("tinyagentos.scheduling.gaming_detector.Path", lambda p: _make_proc(entries))

        result = gd.detect_game_processes()
        assert result == []

    def test_skips_permission_errors(self, monkeypatch):
        class _DeniedEntry:
            name = "999"
            def isdigit(self):
                return True
            def __truediv__(self, other):
                raise PermissionError("denied")

        monkeypatch.setattr("tinyagentos.scheduling.gaming_detector.Path", lambda p: _make_proc([_DeniedEntry()]))

        result = gd.detect_game_processes()
        assert result == []

    def test_skips_non_numeric_dirs(self, monkeypatch):
        class _NonNumeric:
            name = "sys"
            def isdigit(self):
                return False

        monkeypatch.setattr("tinyagentos.scheduling.gaming_detector.Path", lambda p: _make_proc([_NonNumeric()]))

        result = gd.detect_game_processes()
        assert result == []

    def test_returns_empty_on_proc_read_failure(self, monkeypatch):
        class _BadProc:
            def iterdir(self):
                raise OSError("no /proc")

        monkeypatch.setattr("tinyagentos.scheduling.gaming_detector.Path", lambda p: _BadProc())

        result = gd.detect_game_processes()
        assert result == []

    def test_detects_blender(self, monkeypatch):
        entries = [
            _FakeProcEntry(300, "blender", "/usr/bin/blender scene.blend"),
        ]
        monkeypatch.setattr("tinyagentos.scheduling.gaming_detector.Path", lambda p: _make_proc(entries))

        result = gd.detect_game_processes()
        assert len(result) == 1
        assert result[0]["name"] == "blender"


# ---- detect_fullscreen_x11 ---------------------------------------------------

class TestDetectFullscreenX11:
    def test_fullscreen_detected(self, monkeypatch):
        xdotool_out = "X=0\nY=0\nWIDTH=1920\nHEIGHT=1080\nSCREEN=0\n"
        xdpyinfo_out = "dimensions:    1920x1080 pixels (508x286 millimeters)"

        def fake_run(cmd, **kwargs):
            if "xdotool" in cmd:
                return subprocess.CompletedProcess(cmd, 0, xdotool_out, "")
            if "xdpyinfo" in cmd:
                return subprocess.CompletedProcess(cmd, 0, xdpyinfo_out, "")
            return subprocess.CompletedProcess(cmd, 1, "", "")

        monkeypatch.setattr(gd.subprocess, "run", fake_run)

        assert gd.detect_fullscreen_x11() is True

    def test_not_fullscreen_smaller_window(self, monkeypatch):
        xdotool_out = "X=100\nY=100\nWIDTH=800\nHEIGHT=600\nSCREEN=0\n"
        xdpyinfo_out = "dimensions:    1920x1080 pixels (508x286 millimeters)"

        def fake_run(cmd, **kwargs):
            if "xdotool" in cmd:
                return subprocess.CompletedProcess(cmd, 0, xdotool_out, "")
            if "xdpyinfo" in cmd:
                return subprocess.CompletedProcess(cmd, 0, xdpyinfo_out, "")
            return subprocess.CompletedProcess(cmd, 1, "", "")

        monkeypatch.setattr(gd.subprocess, "run", fake_run)

        assert gd.detect_fullscreen_x11() is False

    def test_xdotool_not_installed(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            raise FileNotFoundError("xdotool not found")

        monkeypatch.setattr(gd.subprocess, "run", fake_run)

        assert gd.detect_fullscreen_x11() is False

    def test_xdotool_returns_nonzero(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, "", "error")

        monkeypatch.setattr(gd.subprocess, "run", fake_run)

        assert gd.detect_fullscreen_x11() is False

    def test_xdotool_timeout(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, timeout=2)

        monkeypatch.setattr(gd.subprocess, "run", fake_run)

        assert gd.detect_fullscreen_x11() is False

    def test_near_fullscreen_at_95_percent(self, monkeypatch):
        xdotool_out = "X=0\nY=0\nWIDTH=1824\nHEIGHT=1026\nSCREEN=0\n"
        xdpyinfo_out = "dimensions:    1920x1080 pixels (508x286 millimeters)"

        def fake_run(cmd, **kwargs):
            if "xdotool" in cmd:
                return subprocess.CompletedProcess(cmd, 0, xdotool_out, "")
            if "xdpyinfo" in cmd:
                return subprocess.CompletedProcess(cmd, 0, xdpyinfo_out, "")
            return subprocess.CompletedProcess(cmd, 1, "", "")

        monkeypatch.setattr(gd.subprocess, "run", fake_run)

        assert gd.detect_fullscreen_x11() is True


# ---- detect_gpu_heavy_process ------------------------------------------------

class TestDetectGpuHeavyProcess:
    def test_detects_heavy_gpu_process(self, monkeypatch):
        csv_out = "1234, chrome, 1200\n"

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, csv_out, "")

        monkeypatch.setattr(gd.subprocess, "run", fake_run)

        assert gd.detect_gpu_heavy_process() is True

    def test_ignores_ollama_process(self, monkeypatch):
        csv_out = "1234, ollama, 8000\n"

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, csv_out, "")

        monkeypatch.setattr(gd.subprocess, "run", fake_run)

        assert gd.detect_gpu_heavy_process() is False

    def test_ignores_python_process(self, monkeypatch):
        csv_out = "1234, python, 2000\n"

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, csv_out, "")

        monkeypatch.setattr(gd.subprocess, "run", fake_run)

        assert gd.detect_gpu_heavy_process() is False

    def test_ignores_rkllm_process(self, monkeypatch):
        csv_out = "1234, rkllm-server, 3000\n"

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, csv_out, "")

        monkeypatch.setattr(gd.subprocess, "run", fake_run)

        assert gd.detect_gpu_heavy_process() is False

    def test_low_memory_usage_not_detected(self, monkeypatch):
        csv_out = "1234, chrome, 100\n"

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, csv_out, "")

        monkeypatch.setattr(gd.subprocess, "run", fake_run)

        assert gd.detect_gpu_heavy_process() is False

    def test_nvidia_smi_not_installed(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            raise FileNotFoundError("nvidia-smi not found")

        monkeypatch.setattr(gd.subprocess, "run", fake_run)

        assert gd.detect_gpu_heavy_process() is False

    def test_nvidia_smi_timeout(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, timeout=5)

        monkeypatch.setattr(gd.subprocess, "run", fake_run)

        assert gd.detect_gpu_heavy_process() is False

    def test_nvidia_smi_returns_nonzero(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, "", "NVML error")

        monkeypatch.setattr(gd.subprocess, "run", fake_run)

        assert gd.detect_gpu_heavy_process() is False

    def test_empty_output(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(gd.subprocess, "run", fake_run)

        assert gd.detect_gpu_heavy_process() is False

    def test_malformed_csv_line_skipped(self, monkeypatch):
        csv_out = "only_one_field\n"

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, csv_out, "")

        monkeypatch.setattr(gd.subprocess, "run", fake_run)

        assert gd.detect_gpu_heavy_process() is False


# ---- GamingDetector.check() state machine ------------------------------------

class _FakeRM:
    def __init__(self):
        self.yielded = False
        self.reclaimed = False

    async def yield_resources(self):
        self.yielded = True

    async def reclaim_resources(self):
        self.reclaimed = True


class TestGamingDetectorCheck:
    @pytest.mark.asyncio
    async def test_idle_when_no_games(self, monkeypatch):
        monkeypatch.setattr(gd, "detect_game_processes", lambda: [])
        monkeypatch.setattr(gd, "detect_fullscreen_x11", lambda: False)
        monkeypatch.setattr(gd, "detect_gpu_heavy_process", lambda: False)

        det = GamingDetector(resource_manager=_FakeRM())
        result = await det.check()
        assert result["status"] == "idle"
        assert det.game_active is False

    @pytest.mark.asyncio
    async def test_yields_on_new_game_detected(self, monkeypatch):
        games = [{"pid": 42, "name": "proton", "matched_pattern": "proton"}]
        monkeypatch.setattr(gd, "detect_game_processes", lambda: games)
        monkeypatch.setattr(gd, "detect_fullscreen_x11", lambda: False)
        monkeypatch.setattr(gd, "detect_gpu_heavy_process", lambda: False)

        rm = _FakeRM()
        det = GamingDetector(resource_manager=rm)
        result = await det.check()
        assert result["status"] == "yielded"
        assert result["reason"] == "gaming_detected"
        assert result["games"] == games
        assert det.game_active is True
        assert rm.yielded is True

    @pytest.mark.asyncio
    async def test_yields_on_fullscreen_detected(self, monkeypatch):
        monkeypatch.setattr(gd, "detect_game_processes", lambda: [])
        monkeypatch.setattr(gd, "detect_fullscreen_x11", lambda: True)
        monkeypatch.setattr(gd, "detect_gpu_heavy_process", lambda: False)

        rm = _FakeRM()
        det = GamingDetector(resource_manager=rm)
        result = await det.check()
        assert result["status"] == "yielded"
        assert result["reason"] == "gaming_detected"
        assert rm.yielded is True

    @pytest.mark.asyncio
    async def test_yields_on_gpu_heavy_detected(self, monkeypatch):
        monkeypatch.setattr(gd, "detect_game_processes", lambda: [])
        monkeypatch.setattr(gd, "detect_fullscreen_x11", lambda: False)
        monkeypatch.setattr(gd, "detect_gpu_heavy_process", lambda: True)

        rm = _FakeRM()
        det = GamingDetector(resource_manager=rm)
        result = await det.check()
        assert result["status"] == "yielded"
        assert rm.yielded is True

    @pytest.mark.asyncio
    async def test_no_yield_without_resource_manager(self, monkeypatch):
        monkeypatch.setattr(gd, "detect_game_processes", lambda: [{"pid": 1, "name": "proton", "matched_pattern": "proton"}])
        monkeypatch.setattr(gd, "detect_fullscreen_x11", lambda: False)
        monkeypatch.setattr(gd, "detect_gpu_heavy_process", lambda: False)

        det = GamingDetector(resource_manager=None)
        result = await det.check()
        assert result["status"] == "yielded"
        assert det.game_active is True

    @pytest.mark.asyncio
    async def test_cooldown_when_game_exits(self, monkeypatch):
        call_count = 0

        def fake_detect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [{"pid": 1, "name": "proton", "matched_pattern": "proton"}]
            return []

        monkeypatch.setattr(gd, "detect_game_processes", fake_detect)
        monkeypatch.setattr(gd, "detect_fullscreen_x11", lambda: False)
        monkeypatch.setattr(gd, "detect_gpu_heavy_process", lambda: False)

        rm = _FakeRM()
        det = GamingDetector(resource_manager=rm, cooldown=60)
        result1 = await det.check()
        assert result1["status"] == "yielded"
        assert det.game_active is True

        result2 = await det.check()
        assert result2["status"] == "cooldown"
        assert result2["seconds_remaining"] == 60
        assert det.game_active is True

    @pytest.mark.asyncio
    async def test_reclaim_after_cooldown_expires(self, monkeypatch):
        call_count = 0

        def fake_detect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [{"pid": 1, "name": "proton", "matched_pattern": "proton"}]
            return []

        monkeypatch.setattr(gd, "detect_game_processes", fake_detect)
        monkeypatch.setattr(gd, "detect_fullscreen_x11", lambda: False)
        monkeypatch.setattr(gd, "detect_gpu_heavy_process", lambda: False)

        fake_times = [1000.0, 1001.0, 1062.0]
        monkeypatch.setattr(gd.time, "time", lambda: fake_times.pop(0))

        rm = _FakeRM()
        det = GamingDetector(resource_manager=rm, cooldown=60)
        result1 = await det.check()
        assert result1["status"] == "yielded"

        result2 = await det.check()
        assert result2["status"] == "cooldown"

        result3 = await det.check()
        assert result3["status"] == "reclaimed"
        assert result3["idle_seconds"] == 61
        assert det.game_active is False
        assert rm.reclaimed is True

    @pytest.mark.asyncio
    async def test_still_gaming_when_detected_while_active(self, monkeypatch):
        monkeypatch.setattr(gd, "detect_game_processes", lambda: [{"pid": 1, "name": "proton", "matched_pattern": "proton"}])
        monkeypatch.setattr(gd, "detect_fullscreen_x11", lambda: False)
        monkeypatch.setattr(gd, "detect_gpu_heavy_process", lambda: False)

        det = GamingDetector(resource_manager=_FakeRM())
        await det.check()
        assert det.game_active is True

        result = await det.check()
        assert result["status"] == "gaming"
        assert det.game_active is True

    @pytest.mark.asyncio
    async def test_cooldown_resets_if_game_returns(self, monkeypatch):
        call_count = 0

        def fake_detect():
            nonlocal call_count
            call_count += 1
            if call_count in (1, 3):
                return [{"pid": 1, "name": "proton", "matched_pattern": "proton"}]
            return []

        monkeypatch.setattr(gd, "detect_game_processes", fake_detect)
        monkeypatch.setattr(gd, "detect_fullscreen_x11", lambda: False)
        monkeypatch.setattr(gd, "detect_gpu_heavy_process", lambda: False)

        det = GamingDetector(resource_manager=_FakeRM(), cooldown=60)
        result1 = await det.check()
        assert result1["status"] == "yielded"

        result2 = await det.check()
        assert result2["status"] == "cooldown"

        result3 = await det.check()
        assert result3["status"] == "gaming"
        assert det._game_exited_at is None

    @pytest.mark.asyncio
    async def test_no_reclaim_without_resource_manager(self, monkeypatch):
        call_count = 0

        def fake_detect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [{"pid": 1, "name": "proton", "matched_pattern": "proton"}]
            return []

        monkeypatch.setattr(gd, "detect_game_processes", fake_detect)
        monkeypatch.setattr(gd, "detect_fullscreen_x11", lambda: False)
        monkeypatch.setattr(gd, "detect_gpu_heavy_process", lambda: False)

        fake_times = [0.0, 1.0, 62.0]
        monkeypatch.setattr(gd.time, "time", lambda: fake_times.pop(0))

        det = GamingDetector(resource_manager=None, cooldown=60)
        await det.check()
        await det.check()
        result = await det.check()
        assert result["status"] == "reclaimed"
        assert det.game_active is False


# ---- GamingDetector.force_yield / force_reclaim -------------------------------

class TestGamingDetectorForce:
    @pytest.mark.asyncio
    async def test_force_yield(self, monkeypatch):
        monkeypatch.setattr(gd, "detect_game_processes", lambda: [])
        monkeypatch.setattr(gd, "detect_fullscreen_x11", lambda: False)
        monkeypatch.setattr(gd, "detect_gpu_heavy_process", lambda: False)

        rm = _FakeRM()
        det = GamingDetector(resource_manager=rm)
        result = await det.force_yield()
        assert result["status"] == "yielded"
        assert result["reason"] == "manual"
        assert det.game_active is True
        assert rm.yielded is True

    @pytest.mark.asyncio
    async def test_force_reclaim(self, monkeypatch):
        monkeypatch.setattr(gd, "detect_game_processes", lambda: [])
        monkeypatch.setattr(gd, "detect_fullscreen_x11", lambda: False)
        monkeypatch.setattr(gd, "detect_gpu_heavy_process", lambda: False)

        rm = _FakeRM()
        det = GamingDetector(resource_manager=rm)
        await det.force_yield()
        assert det.game_active is True

        result = await det.force_reclaim()
        assert result["status"] == "reclaimed"
        assert result["reason"] == "manual"
        assert det.game_active is False
        assert rm.reclaimed is True

    @pytest.mark.asyncio
    async def test_force_yield_without_resource_manager(self):
        det = GamingDetector(resource_manager=None)
        result = await det.force_yield()
        assert result["status"] == "yielded"
        assert det.game_active is True

    @pytest.mark.asyncio
    async def test_force_reclaim_without_resource_manager(self):
        det = GamingDetector(resource_manager=None)
        result = await det.force_reclaim()
        assert result["status"] == "reclaimed"
        assert det.game_active is False


# ---- GamingDetector constructor defaults -------------------------------------

class TestGamingDetectorInit:
    def test_default_values(self):
        det = GamingDetector()
        assert det._poll_interval == 10
        assert det._cooldown == 600
        assert det.game_active is False
        assert det._game_exited_at is None
        assert det._last_detected == []

    def test_custom_values(self):
        rm = _FakeRM()
        det = GamingDetector(resource_manager=rm, poll_interval=5, cooldown=300)
        assert det._rm is rm
        assert det._poll_interval == 5
        assert det._cooldown == 300
