"""Tests for scripts/library-vmaf-eval.sh and library-vmaf-eval.ps1."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BASH_SCRIPT = REPO_ROOT / "scripts" / "library-vmaf-eval.sh"
PS1_SCRIPT = REPO_ROOT / "scripts" / "library-vmaf-eval.ps1"
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures"


def _is_ps1_available() -> bool:
    return shutil.which("pwsh") or shutil.which("powershell")


PS1_AVAILABLE = _is_ps1_available()




def _fake_ffmpeg_dir(tmp_path: Path, exit_code: int = 0, score_output: bool = True) -> Path:
    """Create a fake ffmpeg binary.
    - exit_code: process exit code (0 or non-zero)
    - score_output: if True, emits 'VMAF score: 92.123456'; if False, emits nothing relevant
    """
    d = tmp_path / "bin"
    d.mkdir()
    if exit_code == 0 and score_output:
        fake = d / "ffmpeg"
        fake.write_text("#!/bin/bash\necho 'VMAF score: 92.123456' >&2\nexit 0\n")
    elif exit_code == 0 and not score_output:
        fake = d / "ffmpeg"
        fake.write_text("#!/bin/bash\nexit 0\n")
    else:
        fake = d / "ffmpeg"
        fake.write_text(f"#!/bin/bash\nexit {exit_code}\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    return d


def test_bash_script_exists():
    assert BASH_SCRIPT.exists(), f"{BASH_SCRIPT} missing"
    assert os.access(BASH_SCRIPT, os.X_OK), f"{BASH_SCRIPT} not executable"


def test_ps1_script_exists():
    assert PS1_SCRIPT.exists(), f"{PS1_SCRIPT} missing"
    assert PS1_SCRIPT.stat().st_size > 0, f"{PS1_SCRIPT} is empty"


def test_bash_script_produces_csv(tmp_path: Path):
    config = tmp_path / "vmaf-eval-config.json"
    config.write_text(json.dumps({
        "pairs": [
            {
                "video": "clip1",
                "source": str(FIXTURE_DIR / "clip1.mp4"),
                "variant": str(FIXTURE_DIR / "clip1_low.mp4"),
            },
            {
                "video": "clip2",
                "source": str(FIXTURE_DIR / "clip2.mp4"),
                "variant": str(FIXTURE_DIR / "clip2_low.mp4"),
            },
        ]
    }))

    fake_ffmpeg = _fake_ffmpeg_dir(tmp_path)
    env = os.environ.copy()
    env["PATH"] = str(fake_ffmpeg) + os.pathsep + env.get("PATH", "")

    result = subprocess.run(
        ["bash", str(BASH_SCRIPT), str(config)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"script failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    lines = [l for l in result.stdout.strip().splitlines() if l]
    assert lines[0] == "video,variant,vmaf_mean,bytes_source,bytes_variant,saving_pct"
    assert len(lines) == 3, f"expected 3 lines, got {len(lines)}: {lines}"

    rows = {}
    for line in lines[1:]:
        parts = line.split(",")
        assert len(parts) == 6, f"expected 6 columns in {line}"
        video, variant, vmaf, bs, bv, saving = parts
        rows[video] = {
            "variant": variant,
            "vmaf_mean": float(vmaf),
            "bytes_source": int(bs),
            "bytes_variant": int(bv),
            "saving_pct": float(saving),
        }

    for name in ("clip1", "clip2"):
        assert name in rows, f"missing row for {name}"
        r = rows[name]
        assert r["vmaf_mean"] == 92.123456
        assert r["bytes_source"] > 0
        assert r["bytes_variant"] > 0
        assert r["bytes_variant"] <= r["bytes_source"]
        assert 0 <= r["saving_pct"] <= 100


def test_bash_script_exits_nonzero_on_ffmpeg_failure(tmp_path: Path):
    """Fake ffmpeg exits NON-ZERO -> script exits non-zero, NO data row for that pair."""
    config = tmp_path / "vmaf-eval-config.json"
    config.write_text(json.dumps({
        "pairs": [
            {
                "video": "clip1",
                "source": str(FIXTURE_DIR / "clip1.mp4"),
                "variant": str(FIXTURE_DIR / "clip1_low.mp4"),
            },
            {
                "video": "clip2",
                "source": str(FIXTURE_DIR / "clip2.mp4"),
                "variant": str(FIXTURE_DIR / "clip2_low.mp4"),
            },
        ]
    }))

    fake_ffmpeg = _fake_ffmpeg_dir(tmp_path, exit_code=1, score_output=False)
    env = os.environ.copy()
    env["PATH"] = str(fake_ffmpeg) + os.pathsep + env.get("PATH", "")

    result = subprocess.run(
        ["bash", str(BASH_SCRIPT), str(config)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert result.returncode != 0, (
        f"expected non-zero exit, got 0:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    lines = [l for l in result.stdout.strip().splitlines() if l]
    assert lines[0] == "video,variant,vmaf_mean,bytes_source,bytes_variant,saving_pct"
    # No data row for any pair when ffmpeg fails
    data_lines = [l for l in lines[1:] if l]
    assert len(data_lines) == 0, f"expected no data rows, got {data_lines}"


def test_bash_script_exits_nonzero_on_no_vmaf_score(tmp_path: Path):
    """Fake ffmpeg exits ZERO but no VMAF score: script exits non-zero, emits ERROR rows."""
    config = tmp_path / "vmaf-eval-config.json"
    config.write_text(json.dumps({
        "pairs": [
            {
                "video": "clip1",
                "source": str(FIXTURE_DIR / "clip1.mp4"),
                "variant": str(FIXTURE_DIR / "clip1_low.mp4"),
            },
            {
                "video": "clip2",
                "source": str(FIXTURE_DIR / "clip2.mp4"),
                "variant": str(FIXTURE_DIR / "clip2_low.mp4"),
            },
        ]
    }))

    fake_ffmpeg = _fake_ffmpeg_dir(tmp_path, exit_code=0, score_output=False)
    env = os.environ.copy()
    env["PATH"] = str(fake_ffmpeg) + os.pathsep + env.get("PATH", "")

    result = subprocess.run(
        ["bash", str(BASH_SCRIPT), str(config)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert result.returncode != 0, (
        f"expected non-zero exit, got 0:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    lines = [l for l in result.stdout.strip().splitlines() if l]
    assert lines[0] == "video,variant,vmaf_mean,bytes_source,bytes_variant,saving_pct"
    # ERROR rows are emitted for pairs with no VMAF score; script exits non-zero
    data_lines = [l for l in lines[1:] if l]
    assert len(data_lines) == 2, f"expected 2 data rows, got {data_lines}"
    for line in data_lines:
        parts = line.split(",")
        assert len(parts) == 6, f"expected 6 columns in {line}"
        video, variant, vmaf_mean, bs, bv, saving = parts
        assert vmaf_mean == "ERROR", f"vmaf_mean should be 'ERROR', got '{vmaf_mean}'"
        assert saving == "ERROR", f"saving_pct should be 'ERROR', got '{saving}'"
        assert int(bs) > 0, f"bytes_source should be > 0, got {bs}"
        assert int(bv) > 0, f"bytes_variant should be > 0, got {bv}"
        assert int(bv) <= int(bs), f"bytes_variant should be <= bytes_source, got {bv} > {bs}"


def test_bash_script_partial_run_one_fails_one_succeeds(tmp_path: Path):
    """One pair fails and another succeeds: successful pair's real score reported, script exits non-zero."""
    config = tmp_path / "vmaf-eval-config.json"
    config.write_text(json.dumps({
        "pairs": [
            {
                "video": "clip1",
                "source": str(FIXTURE_DIR / "clip1.mp4"),
                "variant": str(FIXTURE_DIR / "clip1_low.mp4"),
            },
            {
                "video": "clip2",
                "source": str(FIXTURE_DIR / "clip2.mp4"),
                "variant": str(FIXTURE_DIR / "clip2_low.mp4"),
            },
        ],
    }))

    # Fake ffmpeg: succeeds for clip1, fails for clip2
    # Based on the source path (argv $3), decide exit code
    fake_ffmpeg = tmp_path / "bin"
    fake_ffmpeg.mkdir()
    fake = fake_ffmpeg / "ffmpeg"
    fake.write_text(
        '#!/bin/bash\n'
        'if [[ "$3" == *"/clip1.mp4" ]]; then\n'
        '  echo "VMAF score: 92.123456" >&2\n'
        '  exit 0\n'
        'else\n'
        '  exit 1\n'
        'fi\n'
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)

    env = os.environ.copy()
    env["PATH"] = str(fake_ffmpeg) + os.pathsep + env.get("PATH", "")

    result = subprocess.run(
        ["bash", str(BASH_SCRIPT), str(config)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert result.returncode != 0, (
        f"expected non-zero exit, got 0:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    lines = [l for l in result.stdout.strip().splitlines() if l]
    assert lines[0] == "video,variant,vmaf_mean,bytes_source,bytes_variant,saving_pct"
    # clip1 should have its real score reported
    data_lines = [l for l in lines[1:] if l]
    assert len(data_lines) == 1, f"expected 1 data row, got {data_lines}"
    clip1_line = [l for l in data_lines if "clip1" in l][0]
    parts = clip1_line.split(",")
    assert parts[2] != "0", f"vmaf_mean should not be '0'"
    assert parts[2] == "92.123456", f"expected 92.123456, got {parts[2]}"

def test_ps1_script_produces_csv(tmp_path: Path):
    """Happy-path test for PowerShell script (if CI runner can execute it)."""
    if not PS1_AVAILABLE:
        pytest.skip("PowerShell not available")
    config = tmp_path / "vmaf-eval-config.json"
    config.write_text(json.dumps({
        "pairs": [
            {
                "video": "clip1",
                "source": str(FIXTURE_DIR / "clip1.mp4"),
                "variant": str(FIXTURE_DIR / "clip1_low.mp4"),
            },
        ]
    }))
    fake_ffmpeg = _fake_ffmpeg_dir(tmp_path)
    env = os.environ.copy()
    env["PATH"] = str(fake_ffmpeg) + os.pathsep + env.get("PATH", "")
    result = subprocess.run(
        ["pwsh", "-File", str(PS1_SCRIPT), str(config)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"script failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    lines = [l for l in result.stdout.strip().splitlines() if l]
    assert lines[0] == "video,variant,vmaf_mean,bytes_source,bytes_variant,saving_pct"
    assert len(lines) == 2, f"expected 2 lines, got {len(lines)}: {lines}"
    parts = lines[1].split(",")
    assert len(parts) == 6
    assert parts[2] == "92.123456"


def test_ps1_script_exits_nonzero_on_ffmpeg_failure(tmp_path: Path):
    """Fake ffmpeg exits NON-ZERO for PS1 script -> script exits non-zero, NO data row."""
    if not PS1_AVAILABLE:
        pytest.skip("PowerShell not available")
    config = tmp_path / "vmaf-eval-config.json"
    config.write_text(json.dumps({
        "pairs": [
            {
                "video": "clip1",
                "source": str(FIXTURE_DIR / "clip1.mp4"),
                "variant": str(FIXTURE_DIR / "clip1_low.mp4"),
            },
        ]
    }))
    fake_ffmpeg = _fake_ffmpeg_dir(tmp_path, exit_code=1, score_output=False)
    env = os.environ.copy()
    env["PATH"] = str(fake_ffmpeg) + os.pathsep + env.get("PATH", "")
    result = subprocess.run(
        ["pwsh", "-File", str(PS1_SCRIPT), str(config)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert result.returncode != 0, (
        f"expected non-zero exit, got 0:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    lines = [l for l in result.stdout.strip().splitlines() if l]
    assert lines[0] == "video,variant,vmaf_mean,bytes_source,bytes_variant,saving_pct"
    data_lines = [l for l in lines[1:] if l]
    assert len(data_lines) == 0, f"expected no data rows, got {data_lines}"


def test_ps1_script_exits_nonzero_on_no_vmaf_score(tmp_path: Path):
    """Fake ffmpeg exits ZERO but no VMAF score: script exits non-zero, emits ERROR rows."""
    if not PS1_AVAILABLE:
        pytest.skip("PowerShell not available")
    config = tmp_path / "vmaf-eval-config.json"
    config.write_text(json.dumps({
        "pairs": [
            {
                "video": "clip1",
                "source": str(FIXTURE_DIR / "clip1.mp4"),
                "variant": str(FIXTURE_DIR / "clip1_low.mp4"),
            },
        ]
    }))
    fake_ffmpeg = _fake_ffmpeg_dir(tmp_path, exit_code=0, score_output=False)
    env = os.environ.copy()
    env["PATH"] = str(fake_ffmpeg) + os.pathsep + env.get("PATH", "")
    result = subprocess.run(
        ["pwsh", "-File", str(PS1_SCRIPT), str(config)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert result.returncode != 0, (
        f"expected non-zero exit, got 0:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    lines = [l for l in result.stdout.strip().splitlines() if l]
    assert lines[0] == "video,variant,vmaf_mean,bytes_source,bytes_variant,saving_pct"
    # ERROR rows are emitted for pairs with no VMAF score; script exits non-zero
    data_lines = [l for l in lines[1:] if l]
    assert len(data_lines) == 1, f"expected 1 data row, got {data_lines}"
    parts = data_lines[0].split(",")
    assert len(parts) == 6, f"expected 6 columns in {data_lines[0]}"
    video, variant, vmaf_mean, bs, bv, saving = parts
    assert vmaf_mean == "ERROR", f"vmaf_mean should be 'ERROR', got '{vmaf_mean}'"
    assert saving == "ERROR", f"saving_pct should be 'ERROR', got '{saving}'"
    assert int(bs) > 0, f"bytes_source should be > 0, got {bs}"
    assert int(bv) > 0, f"bytes_variant should be > 0, got {bv}"
