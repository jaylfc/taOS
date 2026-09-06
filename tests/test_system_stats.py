"""Unit tests for tinyagentos/system_stats.py."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# read_rknpu_load
# ---------------------------------------------------------------------------


class TestReadRknpuLoad:
    @patch("tinyagentos.system_stats.Path")
    def test_averages_multiple_cores(self, mock_path_cls):
        mock_path = MagicMock()
        mock_path.read_text.return_value = "NPU load:  Core0:  12%, Core1:   0%, Core2:   0%,"
        mock_path_cls.return_value = mock_path
        from tinyagentos.system_stats import read_rknpu_load

        result = read_rknpu_load()
        assert result == pytest.approx(4.0)

    @patch("tinyagentos.system_stats.Path")
    def test_single_core(self, mock_path_cls):
        mock_path = MagicMock()
        mock_path.read_text.return_value = "Core0:  50%"
        mock_path_cls.return_value = mock_path
        from tinyagentos.system_stats import read_rknpu_load

        result = read_rknpu_load()
        assert result == pytest.approx(50.0)

    @patch("tinyagentos.system_stats.Path")
    def test_returns_none_when_no_files(self, mock_path_cls):
        mock_path = MagicMock()
        mock_path.read_text.side_effect = FileNotFoundError
        mock_path_cls.return_value = mock_path
        from tinyagentos.system_stats import read_rknpu_load

        assert read_rknpu_load() is None

    @patch("tinyagentos.system_stats.Path")
    def test_skips_permission_error(self, mock_path_cls):
        mock_path = MagicMock()
        mock_path.read_text.side_effect = PermissionError
        mock_path_cls.return_value = mock_path
        from tinyagentos.system_stats import read_rknpu_load

        assert read_rknpu_load() is None

    @patch("tinyagentos.system_stats.Path")
    def test_ignores_non_percent_tokens(self, mock_path_cls):
        mock_path = MagicMock()
        mock_path.read_text.return_value = "NPU load: Core0: abc%, Core1: 25%"
        mock_path_cls.return_value = mock_path
        from tinyagentos.system_stats import read_rknpu_load

        result = read_rknpu_load()
        assert result == pytest.approx(25.0)

    @patch("tinyagentos.system_stats.Path")
    def test_falls_through_to_second_path(self, mock_path_cls):
        first = MagicMock()
        first.read_text.side_effect = FileNotFoundError
        second = MagicMock()
        second.read_text.return_value = "Core0: 80%"
        mock_path_cls.side_effect = [first, second]
        from tinyagentos.system_stats import read_rknpu_load

        result = read_rknpu_load()
        assert result == pytest.approx(80.0)

    @patch("tinyagentos.system_stats.Path")
    def test_no_percent_tokens_returns_none(self, mock_path_cls):
        mock_path = MagicMock()
        mock_path.read_text.return_value = "no data here"
        mock_path_cls.return_value = mock_path
        from tinyagentos.system_stats import read_rknpu_load

        assert read_rknpu_load() is None


# ---------------------------------------------------------------------------
# read_nvidia_vram
# ---------------------------------------------------------------------------


class TestReadNvidiaVram:
    @patch("tinyagentos.system_stats.subprocess.run")
    @patch("tinyagentos.system_stats.shutil.which", return_value="/usr/bin/nvidia-smi")
    def test_returns_used_total(self, mock_which, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="5000, 8192\n"
        )
        from tinyagentos.system_stats import read_nvidia_vram

        result = read_nvidia_vram()
        assert result == (5000, 8192)

    @patch("tinyagentos.system_stats.shutil.which", return_value=None)
    def test_returns_none_when_no_binary(self, mock_which):
        from tinyagentos.system_stats import read_nvidia_vram

        assert read_nvidia_vram() is None

    @patch("tinyagentos.system_stats.subprocess.run")
    @patch("tinyagentos.system_stats.shutil.which", return_value="/usr/bin/nvidia-smi")
    def test_nonzero_returncode(self, mock_which, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="error"
        )
        from tinyagentos.system_stats import read_nvidia_vram

        assert read_nvidia_vram() is None

    @patch("tinyagentos.system_stats.subprocess.run")
    @patch("tinyagentos.system_stats.shutil.which", return_value="/usr/bin/nvidia-smi")
    def test_empty_stdout(self, mock_which, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=""
        )
        from tinyagentos.system_stats import read_nvidia_vram

        assert read_nvidia_vram() is None

    @patch("tinyagentos.system_stats.subprocess.run")
    @patch("tinyagentos.system_stats.shutil.which", return_value="/usr/bin/nvidia-smi")
    def test_subprocess_error(self, mock_which, mock_run):
        mock_run.side_effect = subprocess.SubprocessError("boom")
        from tinyagentos.system_stats import read_nvidia_vram

        assert read_nvidia_vram() is None

    @patch("tinyagentos.system_stats.subprocess.run")
    @patch("tinyagentos.system_stats.shutil.which", return_value="/usr/bin/nvidia-smi")
    def test_malformed_output(self, mock_which, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="not_a_number, 8192"
        )
        from tinyagentos.system_stats import read_nvidia_vram

        assert read_nvidia_vram() is None

    @patch("tinyagentos.system_stats.subprocess.run")
    @patch("tinyagentos.system_stats.shutil.which", return_value="/usr/bin/nvidia-smi")
    def test_extra_lines_ignored(self, mock_which, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="1000, 4096\n2000, 8192\n"
        )
        from tinyagentos.system_stats import read_nvidia_vram

        result = read_nvidia_vram()
        assert result == (1000, 4096)


# ---------------------------------------------------------------------------
# read_nvidia_gpu_load
# ---------------------------------------------------------------------------


class TestReadNvidiaGpuLoad:
    @patch("tinyagentos.system_stats.subprocess.run")
    @patch("tinyagentos.system_stats.shutil.which", return_value="/usr/bin/nvidia-smi")
    def test_returns_float(self, mock_which, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="42\n"
        )
        from tinyagentos.system_stats import read_nvidia_gpu_load

        result = read_nvidia_gpu_load()
        assert result == pytest.approx(42.0)

    @patch("tinyagentos.system_stats.shutil.which", return_value=None)
    def test_no_binary(self, mock_which):
        from tinyagentos.system_stats import read_nvidia_gpu_load

        assert read_nvidia_gpu_load() is None

    @patch("tinyagentos.system_stats.subprocess.run")
    @patch("tinyagentos.system_stats.shutil.which", return_value="/usr/bin/nvidia-smi")
    def test_nonzero_rc(self, mock_which, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="err"
        )
        from tinyagentos.system_stats import read_nvidia_gpu_load

        assert read_nvidia_gpu_load() is None

    @patch("tinyagentos.system_stats.subprocess.run")
    @patch("tinyagentos.system_stats.shutil.which", return_value="/usr/bin/nvidia-smi")
    def test_empty_output(self, mock_which, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="  "
        )
        from tinyagentos.system_stats import read_nvidia_gpu_load

        assert read_nvidia_gpu_load() is None

    @patch("tinyagentos.system_stats.subprocess.run")
    @patch("tinyagentos.system_stats.shutil.which", return_value="/usr/bin/nvidia-smi")
    def test_file_not_found_error(self, mock_which, mock_run):
        mock_run.side_effect = FileNotFoundError
        from tinyagentos.system_stats import read_nvidia_gpu_load

        assert read_nvidia_gpu_load() is None

    @patch("tinyagentos.system_stats.subprocess.run")
    @patch("tinyagentos.system_stats.shutil.which", return_value="/usr/bin/nvidia-smi")
    def test_non_numeric_output(self, mock_which, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="N/A\n"
        )
        from tinyagentos.system_stats import read_nvidia_gpu_load

        assert read_nvidia_gpu_load() is None


# ---------------------------------------------------------------------------
# get_npu_usage
# ---------------------------------------------------------------------------


class TestGetNpuUsage:
    @patch("tinyagentos.system_stats.read_rknpu_load", return_value=15.0)
    def test_rknpu_dispatch(self, mock_read):
        from tinyagentos.system_stats import get_npu_usage

        result = get_npu_usage("rknpu")
        assert result == pytest.approx(15.0)
        mock_read.assert_called_once()

    def test_unknown_type(self):
        from tinyagentos.system_stats import get_npu_usage

        assert get_npu_usage("unknown") is None

    def test_empty_string(self):
        from tinyagentos.system_stats import get_npu_usage

        assert get_npu_usage("") is None

    @patch("tinyagentos.system_stats.read_rknpu_load", return_value=None)
    def test_rknpu_returns_none(self, mock_read):
        from tinyagentos.system_stats import get_npu_usage

        assert get_npu_usage("rknpu") is None


# ---------------------------------------------------------------------------
# get_vram_usage
# ---------------------------------------------------------------------------


class TestGetVramUsage:
    @patch("tinyagentos.system_stats.read_nvidia_vram", return_value=(2048, 8192))
    def test_nvidia_percent(self, mock_read):
        from tinyagentos.system_stats import get_vram_usage

        pct, used, total = get_vram_usage("nvidia")
        assert used == 2048
        assert total == 8192
        assert pct == pytest.approx(25.0)

    @patch("tinyagentos.system_stats.read_nvidia_vram", return_value=(0, 0))
    def test_zero_total(self, mock_read):
        from tinyagentos.system_stats import get_vram_usage

        pct, used, total = get_vram_usage("nvidia")
        assert used == 0
        assert total == 0
        assert pct is None

    @patch("tinyagentos.system_stats.read_nvidia_vram", return_value=None)
    def test_nvidia_unavailable(self, mock_read):
        from tinyagentos.system_stats import get_vram_usage

        result = get_vram_usage("nvidia")
        assert result == (None, None, None)

    def test_unknown_gpu(self):
        from tinyagentos.system_stats import get_vram_usage

        assert get_vram_usage("amd") == (None, None, None)


# ---------------------------------------------------------------------------
# get_npu_per_core
# ---------------------------------------------------------------------------


class TestGetNpuPerCore:
    @patch("tinyagentos.system_stats.Path")
    def test_parses_cores(self, mock_path_cls):
        mock_path = MagicMock()
        mock_path.read_text.return_value = "NPU load:  Core0:  12%, Core1:  30%, Core2:   5%,"
        mock_path_cls.return_value = mock_path
        from tinyagentos.system_stats import get_npu_per_core

        result = get_npu_per_core()
        assert result == [
            {"core": 0, "load_percent": 12},
            {"core": 1, "load_percent": 30},
            {"core": 2, "load_percent": 5},
        ]

    @patch("tinyagentos.system_stats.Path")
    def test_returns_none_on_oserror(self, mock_path_cls):
        mock_path = MagicMock()
        mock_path.read_text.side_effect = OSError
        mock_path_cls.return_value = mock_path
        from tinyagentos.system_stats import get_npu_per_core

        assert get_npu_per_core() is None

    @patch("tinyagentos.system_stats.Path")
    def test_no_cores_returns_none(self, mock_path_cls):
        mock_path = MagicMock()
        mock_path.read_text.return_value = "no core data"
        mock_path_cls.return_value = mock_path
        from tinyagentos.system_stats import get_npu_per_core

        assert get_npu_per_core() is None

    @patch("tinyagentos.system_stats.Path")
    def test_permission_error(self, mock_path_cls):
        mock_path = MagicMock()
        mock_path.read_text.side_effect = PermissionError
        mock_path_cls.return_value = mock_path
        from tinyagentos.system_stats import get_npu_per_core

        assert get_npu_per_core() is None


# ---------------------------------------------------------------------------
# get_npu_frequency
# ---------------------------------------------------------------------------


class TestGetNpuFrequency:
    @patch("tinyagentos.system_stats.Path")
    def test_first_path_works(self, mock_path_cls):
        first = MagicMock()
        first.read_text.return_value = "600000000\n"
        second = MagicMock()
        second.read_text.return_value = "700000000\n"
        mock_path_cls.side_effect = [first, second]
        from tinyagentos.system_stats import get_npu_frequency

        result = get_npu_frequency()
        assert result == 600000000

    @patch("tinyagentos.system_stats.Path")
    def test_falls_through_to_second(self, mock_path_cls):
        first = MagicMock()
        first.read_text.side_effect = FileNotFoundError
        second = MagicMock()
        second.read_text.return_value = "700000000\n"
        mock_path_cls.side_effect = [first, second]
        from tinyagentos.system_stats import get_npu_frequency

        result = get_npu_frequency()
        assert result == 700000000

    @patch("tinyagentos.system_stats.Path")
    def test_both_fail(self, mock_path_cls):
        first = MagicMock()
        first.read_text.side_effect = FileNotFoundError
        second = MagicMock()
        second.read_text.side_effect = PermissionError
        mock_path_cls.side_effect = [first, second]
        from tinyagentos.system_stats import get_npu_frequency

        assert get_npu_frequency() is None

    @patch("tinyagentos.system_stats.Path")
    def test_invalid_value(self, mock_path_cls):
        first = MagicMock()
        first.read_text.return_value = "not_a_number"
        second = MagicMock()
        second.read_text.side_effect = FileNotFoundError
        mock_path_cls.side_effect = [first, second]
        from tinyagentos.system_stats import get_npu_frequency

        assert get_npu_frequency() is None


# ---------------------------------------------------------------------------
# get_cpu_per_core
# ---------------------------------------------------------------------------


class TestGetCpuPerCore:
    @patch("tinyagentos.system_stats.Path")
    @patch("tinyagentos.system_stats.psutil")
    def test_no_cpufreq_dir(self, mock_psutil, mock_path_cls):
        mock_psutil.cpu_percent.return_value = [5.0]

        base = MagicMock()
        base.exists.return_value = False
        mock_path_cls.return_value = base
        from tinyagentos.system_stats import get_cpu_per_core

        result = get_cpu_per_core()
        assert len(result) == 1
        assert result[0]["core"] == 0
        assert result[0]["load_percent"] == 5.0
        assert "freq_khz" not in result[0]

    @patch("tinyagentos.system_stats.Path")
    @patch("tinyagentos.system_stats.psutil")
    def test_cpufreq_read_error(self, mock_psutil, mock_path_cls):
        mock_psutil.cpu_percent.return_value = [5.0]

        base = MagicMock()
        base.exists.return_value = True
        child = MagicMock()
        child.read_text.side_effect = OSError
        base.__truediv__ = lambda self, name: child
        mock_path_cls.return_value = base
        from tinyagentos.system_stats import get_cpu_per_core

        result = get_cpu_per_core()
        assert len(result) == 1
        assert "freq_khz" not in result[0]


# ---------------------------------------------------------------------------
# get_thermal_zones
# ---------------------------------------------------------------------------


class TestGetThermalZones:
    def _clear_cache(self):
        from tinyagentos.system_stats import _thermal_zones

        _thermal_zones.cache_clear()

    def setup_method(self):
        self._clear_cache()

    def teardown_method(self):
        self._clear_cache()

    @patch("tinyagentos.system_stats.Path")
    def test_no_thermal_dir(self, mock_path_cls):
        base = MagicMock()
        base.exists.return_value = False
        mock_path_cls.return_value = base
        from tinyagentos.system_stats import get_thermal_zones

        assert get_thermal_zones() == []

    @patch("tinyagentos.system_stats.Path")
    def test_temp_read_error(self, mock_path_cls):
        zone_dir = MagicMock()
        zone_dir.name = "thermal_zone0"

        base = MagicMock()
        base.exists.return_value = True
        base.iterdir.return_value = [zone_dir]

        def zone_child(name):
            m = MagicMock()
            if name == "type":
                m.read_text.return_value = "cpu-thermal\n"
            elif name == "temp":
                m.read_text.side_effect = OSError
            return m

        zone_dir.__truediv__ = lambda self, name: zone_child(name)
        mock_path_cls.side_effect = lambda p: base if p == "/sys/class/thermal" else MagicMock()
        from tinyagentos.system_stats import get_thermal_zones

        result = get_thermal_zones()
        assert result == []


# ---------------------------------------------------------------------------
# get_gpu_load
# ---------------------------------------------------------------------------


class TestGetGpuLoad:
    @patch("tinyagentos.system_stats.Path")
    def test_panthor_format(self, mock_path_cls):
        panthor = MagicMock()
        panthor.read_text.return_value = "120@800000000\n"
        mali = MagicMock()
        mali.read_text.side_effect = FileNotFoundError

        def path_factory(p):
            if "fb000000.gpu" in str(p):
                return panthor
            return mali

        mock_path_cls.side_effect = path_factory
        from tinyagentos.system_stats import get_gpu_load

        result = get_gpu_load()
        assert result == {"load_percent": 120, "freq_hz": 800000000}

    @patch("tinyagentos.system_stats.Path")
    def test_mali_format(self, mock_path_cls):
        panthor = MagicMock()
        panthor.read_text.side_effect = FileNotFoundError
        mali = MagicMock()
        mali.read_text.return_value = "busy_time: 300\nidle_time: 700\n"

        def path_factory(p):
            if "mali0" in str(p):
                return mali
            return panthor

        mock_path_cls.side_effect = path_factory
        from tinyagentos.system_stats import get_gpu_load

        result = get_gpu_load()
        assert result == {"load_percent": 30, "freq_hz": None}

    @patch("tinyagentos.system_stats.Path")
    def test_mali_zero_total(self, mock_path_cls):
        panthor = MagicMock()
        panthor.read_text.side_effect = FileNotFoundError
        mali = MagicMock()
        mali.read_text.return_value = "busy_time: 0\nidle_time: 0\n"

        def path_factory(p):
            if "mali0" in str(p):
                return mali
            return panthor

        mock_path_cls.side_effect = path_factory
        from tinyagentos.system_stats import get_gpu_load

        assert get_gpu_load() is None

    @patch("tinyagentos.system_stats.Path")
    def test_no_gpu_data(self, mock_path_cls):
        mock_path = MagicMock()
        mock_path.read_text.side_effect = FileNotFoundError
        mock_path_cls.return_value = mock_path
        from tinyagentos.system_stats import get_gpu_load

        assert get_gpu_load() is None

    @patch("tinyagentos.system_stats.Path")
    def test_panthor_no_at_sign(self, mock_path_cls):
        panthor = MagicMock()
        panthor.read_text.return_value = "no_at_sign"
        mali = MagicMock()
        mali.read_text.side_effect = FileNotFoundError

        def path_factory(p):
            if "fb000000.gpu" in str(p):
                return panthor
            return mali

        mock_path_cls.side_effect = path_factory
        from tinyagentos.system_stats import get_gpu_load

        assert get_gpu_load() is None


# ---------------------------------------------------------------------------
# get_zram_stats
# ---------------------------------------------------------------------------


class TestGetZramStats:
    @patch("tinyagentos.system_stats.Path")
    def test_single_device(self, mock_path_cls):
        zram0 = MagicMock()
        zram0.name = "zram0"
        mm_stat = MagicMock()
        mm_stat.exists.return_value = True
        mm_stat.read_text.return_value = "1073741824 536870912 2097152 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0"

        zram0.__truediv__ = lambda self, name: mm_stat if name == "mm_stat" else MagicMock(
            exists=MagicMock(return_value=False)
        )

        base = MagicMock()
        base.exists.return_value = True
        base.glob.return_value = [zram0]

        mock_path_cls.side_effect = lambda p: base if str(p) == "/sys/block" else MagicMock()
        from tinyagentos.system_stats import get_zram_stats

        result = get_zram_stats()
        assert len(result) == 1
        assert result[0]["device"] == "zram0"
        assert result[0]["orig_mb"] == 1024
        assert result[0]["compr_mb"] == 512
        assert result[0]["used_mb"] == 2
        assert result[0]["ratio"] == pytest.approx(2.0)

    @patch("tinyagentos.system_stats.Path")
    def test_no_block_dir(self, mock_path_cls):
        base = MagicMock()
        base.exists.return_value = False
        mock_path_cls.return_value = base
        from tinyagentos.system_stats import get_zram_stats

        assert get_zram_stats() == []

    @patch("tinyagentos.system_stats.Path")
    def test_no_mm_stat(self, mock_path_cls):
        zram0 = MagicMock()
        zram0.name = "zram0"
        mm_stat = MagicMock()
        mm_stat.exists.return_value = False
        zram0.__truediv__ = lambda self, name: mm_stat

        base = MagicMock()
        base.exists.return_value = True
        base.glob.return_value = [zram0]

        mock_path_cls.side_effect = lambda p: base if str(p) == "/sys/block" else MagicMock()
        from tinyagentos.system_stats import get_zram_stats

        assert get_zram_stats() == []

    @patch("tinyagentos.system_stats.Path")
    def test_zero_compressed(self, mock_path_cls):
        zram0 = MagicMock()
        zram0.name = "zram0"
        mm_stat = MagicMock()
        mm_stat.exists.return_value = True
        mm_stat.read_text.return_value = "100 0 0 0 0"

        zram0.__truediv__ = lambda self, name: mm_stat if name == "mm_stat" else MagicMock(
            exists=MagicMock(return_value=False)
        )

        base = MagicMock()
        base.exists.return_value = True
        base.glob.return_value = [zram0]

        mock_path_cls.side_effect = lambda p: base if str(p) == "/sys/block" else MagicMock()
        from tinyagentos.system_stats import get_zram_stats

        result = get_zram_stats()
        assert len(result) == 1
        assert result[0]["ratio"] == 0

    @patch("tinyagentos.system_stats.Path")
    def test_too_few_fields(self, mock_path_cls):
        zram0 = MagicMock()
        zram0.name = "zram0"
        mm_stat = MagicMock()
        mm_stat.exists.return_value = True
        mm_stat.read_text.return_value = "100 200"

        zram0.__truediv__ = lambda self, name: mm_stat if name == "mm_stat" else MagicMock(
            exists=MagicMock(return_value=False)
        )

        base = MagicMock()
        base.exists.return_value = True
        base.glob.return_value = [zram0]

        mock_path_cls.side_effect = lambda p: base if str(p) == "/sys/block" else MagicMock()
        from tinyagentos.system_stats import get_zram_stats

        assert get_zram_stats() == []


# ---------------------------------------------------------------------------
# get_disk_io_rate
# ---------------------------------------------------------------------------


class TestGetDiskIoRate:
    def setup_method(self):
        from tinyagentos.system_stats import _DISK_IO_LAST

        _DISK_IO_LAST["ts"] = 0.0
        _DISK_IO_LAST["read"] = 0
        _DISK_IO_LAST["write"] = 0

    @patch("tinyagentos.system_stats.time")
    @patch("tinyagentos.system_stats.psutil")
    def test_first_call_returns_zero(self, mock_psutil, mock_time):
        mock_psutil.disk_io_counters.return_value = MagicMock(
            read_bytes=1000, write_bytes=2000
        )
        mock_time.time.return_value = 100.0
        from tinyagentos.system_stats import get_disk_io_rate

        result = get_disk_io_rate()
        assert result == {"read_bps": 0, "write_bps": 0}

    @patch("tinyagentos.system_stats.time")
    @patch("tinyagentos.system_stats.psutil")
    def test_second_call_computes_rate(self, mock_psutil, mock_time):
        io1 = MagicMock(read_bytes=1000, write_bytes=2000)
        io2 = MagicMock(read_bytes=101000, write_bytes=202000)
        mock_psutil.disk_io_counters.side_effect = [io1, io2]
        mock_time.time.side_effect = [100.0, 101.0]
        from tinyagentos.system_stats import get_disk_io_rate

        get_disk_io_rate()
        result = get_disk_io_rate()
        assert result["read_bps"] == 100000
        assert result["write_bps"] == 200000

    @patch("tinyagentos.system_stats.psutil")
    def test_none_counters(self, mock_psutil):
        mock_psutil.disk_io_counters.return_value = None
        from tinyagentos.system_stats import get_disk_io_rate

        result = get_disk_io_rate()
        assert result == {"read_bps": 0, "write_bps": 0}

    @patch("tinyagentos.system_stats.time")
    @patch("tinyagentos.system_stats.psutil")
    def test_negative_rate_clamped(self, mock_psutil, mock_time):
        io1 = MagicMock(read_bytes=100000, write_bytes=200000)
        io2 = MagicMock(read_bytes=50000, write_bytes=100000)
        mock_psutil.disk_io_counters.side_effect = [io1, io2]
        mock_time.time.side_effect = [100.0, 101.0]
        from tinyagentos.system_stats import get_disk_io_rate

        get_disk_io_rate()
        result = get_disk_io_rate()
        assert result["read_bps"] == 0
        assert result["write_bps"] == 0


# ---------------------------------------------------------------------------
# get_network_rates
# ---------------------------------------------------------------------------


class TestGetNetworkRates:
    def setup_method(self):
        from tinyagentos.system_stats import _NET_IO_LAST

        _NET_IO_LAST.clear()

    @patch("tinyagentos.system_stats.time")
    @patch("tinyagentos.system_stats.psutil")
    def test_filters_loopback(self, mock_psutil, mock_time):
        mock_time.time.return_value = 100.0
        lo = MagicMock(bytes_recv=999, bytes_sent=999)
        mock_psutil.net_io_counters.return_value = {"lo": lo}
        from tinyagentos.system_stats import get_network_rates

        result = get_network_rates()
        assert result == []

    @patch("tinyagentos.system_stats.time")
    @patch("tinyagentos.system_stats.psutil")
    def test_filters_docker_interfaces(self, mock_psutil, mock_time):
        mock_time.time.return_value = 100.0
        docker0 = MagicMock(bytes_recv=500, bytes_sent=500)
        br_fake = MagicMock(bytes_recv=600, bytes_sent=600)
        veth_fake = MagicMock(bytes_recv=700, bytes_sent=700)
        mock_psutil.net_io_counters.return_value = {
            "docker0": docker0,
            "br-abc123": br_fake,
            "veth123": veth_fake,
        }
        from tinyagentos.system_stats import get_network_rates

        result = get_network_rates()
        assert result == []

    @patch("tinyagentos.system_stats.time")
    @patch("tinyagentos.system_stats.psutil")
    def test_real_interface(self, mock_psutil, mock_time):
        mock_time.time.return_value = 100.0
        eth0 = MagicMock(bytes_recv=1000000, bytes_sent=2000000)
        mock_psutil.net_io_counters.return_value = {"eth0": eth0}
        from tinyagentos.system_stats import get_network_rates

        result = get_network_rates()
        assert len(result) == 1
        assert result[0]["name"] == "eth0"
        assert result[0]["rx_bps"] == 0
        assert result[0]["tx_bps"] == 0
        assert result[0]["rx_total"] == 1000000
        assert result[0]["tx_total"] == 2000000

    @patch("tinyagentos.system_stats.time")
    @patch("tinyagentos.system_stats.psutil")
    def test_rate_calculation(self, mock_psutil, mock_time):
        mock_time.time.side_effect = [100.0, 101.0]
        eth0_first = MagicMock(bytes_recv=0, bytes_sent=0)
        eth0_second = MagicMock(bytes_recv=1000, bytes_sent=2000)
        mock_psutil.net_io_counters.side_effect = [
            {"eth0": eth0_first},
            {"eth0": eth0_second},
        ]
        from tinyagentos.system_stats import get_network_rates

        get_network_rates()
        result = get_network_rates()
        assert len(result) == 1
        assert result[0]["rx_bps"] == 1000
        assert result[0]["tx_bps"] == 2000

    @patch("tinyagentos.system_stats.time")
    @patch("tinyagentos.system_stats.psutil")
    def test_virbr_filtered(self, mock_psutil, mock_time):
        mock_time.time.return_value = 100.0
        virbr0 = MagicMock(bytes_recv=100, bytes_sent=100)
        mock_psutil.net_io_counters.return_value = {"virbr0": virbr0}
        from tinyagentos.system_stats import get_network_rates

        assert get_network_rates() == []

    @patch("tinyagentos.system_stats.time")
    @patch("tinyagentos.system_stats.psutil")
    def test_dummy_filtered(self, mock_psutil, mock_time):
        mock_time.time.return_value = 100.0
        dummy0 = MagicMock(bytes_recv=100, bytes_sent=100)
        mock_psutil.net_io_counters.return_value = {"dummy0": dummy0}
        from tinyagentos.system_stats import get_network_rates

        assert get_network_rates() == []


# ---------------------------------------------------------------------------
# get_top_processes
# ---------------------------------------------------------------------------


class TestGetTopProcesses:
    @patch("tinyagentos.system_stats.psutil")
    def test_sorts_by_rss(self, mock_psutil):
        p1 = MagicMock()
        p1.info = {
            "pid": 1,
            "name": "big",
            "memory_info": MagicMock(rss=200 * 1024 * 1024),
            "cpu_percent": 10.0,
            "username": "root",
        }
        p2 = MagicMock()
        p2.info = {
            "pid": 2,
            "name": "small",
            "memory_info": MagicMock(rss=50 * 1024 * 1024),
            "cpu_percent": 5.0,
            "username": "root",
        }
        mock_psutil.process_iter.return_value = [p2, p1]
        from tinyagentos.system_stats import get_top_processes

        result = get_top_processes()
        assert len(result) == 2
        assert result[0]["name"] == "big"
        assert result[0]["rss_mb"] == 200
        assert result[1]["name"] == "small"
        assert result[1]["rss_mb"] == 50

    @patch("tinyagentos.system_stats.psutil")
    def test_respects_limit(self, mock_psutil):
        procs = []
        for i in range(20):
            p = MagicMock()
            p.info = {
                "pid": i,
                "name": f"proc{i}",
                "memory_info": MagicMock(rss=i * 1024 * 1024),
                "cpu_percent": 0.0,
                "username": "user",
            }
            procs.append(p)
        mock_psutil.process_iter.return_value = procs
        from tinyagentos.system_stats import get_top_processes

        result = get_top_processes(limit=5)
        assert len(result) == 5

    @patch("tinyagentos.system_stats.psutil")
    def test_handles_none_name(self, mock_psutil):
        p = MagicMock()
        p.info = {
            "pid": 1,
            "name": None,
            "memory_info": MagicMock(rss=1024 * 1024),
            "cpu_percent": 0.0,
            "username": None,
        }
        mock_psutil.process_iter.return_value = [p]
        from tinyagentos.system_stats import get_top_processes

        result = get_top_processes()
        assert result[0]["name"] == "?"
        assert result[0]["user"] == "?"

    @patch("tinyagentos.system_stats.psutil")
    def test_handles_none_cpu_percent(self, mock_psutil):
        p = MagicMock()
        p.info = {
            "pid": 1,
            "name": "test",
            "memory_info": MagicMock(rss=1024 * 1024),
            "cpu_percent": None,
            "username": "user",
        }
        mock_psutil.process_iter.return_value = [p]
        from tinyagentos.system_stats import get_top_processes

        result = get_top_processes()
        assert result[0]["cpu_percent"] == 0.0
