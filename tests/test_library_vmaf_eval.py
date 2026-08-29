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


def _fake_ffmpeg_dir(tmp_path: Path) -> Path:
    """Create a fake ffmpeg binary that emits a fixed VMAF score."""
    d = tmp_path / "bin"
    d.mkdir()
    fake = d / "ffmpeg"
    fake.write_text("#!/bin/bash\necho 'VMAF score: 92.123456' >&2\nexit 0\n")
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
