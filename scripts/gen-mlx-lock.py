#!/usr/bin/env python3
"""Regenerate the vendored, hash-pinned MLX runtime locks (taOS #329).

`tinyagentos/installers/mlx_lm_requirements_py*.txt` are what MLXInstaller
feeds to ``pip install --require-hashes`` in its dedicated runtime venv. They
are checked in rather than resolved at install time so an authenticated Store
install cannot pull an unpinned (or silently substituted) package off an index.

What this script does, for each CPython minor taOS supports:

1. ``pip download`` the pinned closure (``mlx-lm`` plus ``mlx``, which pip
   skips for a foreign platform because its requirement carries a
   ``platform_system == "Darwin"`` marker) for macOS arm64 wheels.
2. Add the sha256 of *every* macOS-arm64 / pure-python wheel PyPI publishes for
   those exact versions -- pip picks the best wheel compatible with the machine
   it runs on, and ``--require-hashes`` rejects a file whose digest is absent,
   so listing only the wheel this host downloaded would break a Mac on another
   macOS release.
3. Write the lock next to the installer and fail if a wheel it just resolved is
   not covered by a listed hash.

Usage: ``python3 scripts/gen-mlx-lock.py`` (needs network access to PyPI).
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tinyagentos.installers.mlx_installer import (  # noqa: E402
    MLX_LM_VERSION,
    MLX_VERSION,
)

#: CPython minors taOS ships for, as (python-version, wheel abi tag).
PYTHONS: tuple[tuple[str, str], ...] = (
    ("3.11", "cp311"),
    ("3.12", "cp312"),
    ("3.13", "cp313"),
)
PLATFORM = "macosx_14_0_arm64"

HEADER = """\
# Vendored, hash-pinned MLX serving runtime for macOS arm64 on CPython {py}.
#
# Installed by MLXInstaller into its own venv with:
#   pip install --require-hashes --only-binary=:all: -r <this file>
#
# Regenerate with: python3 scripts/gen-mlx-lock.py
#
# Every requirement is pinned and hashed, so `--require-hashes` refuses a file
# it has no digest for -- a transitive dependency left floating would fail the
# install instead of silently resolving off the network. Only macOS arm64 and
# pure-python wheels are listed: mlx publishes no wheel for a non-Darwin
# platform, and {platform} or later is compatible with every newer macOS.
# Runtime pins: mlx-lm=={lm}, mlx=={mlx}.
"""


def _wheel_name_parts(filename: str) -> list[str] | None:
    if not filename.endswith(".whl"):
        return None
    parts = filename[:-4].split("-")
    return parts if len(parts) >= 5 else None


def _is_macos_or_pure_wheel(filename: str) -> bool:
    """True for a wheel pip may legally select on macOS arm64 (or anywhere)."""
    parts = _wheel_name_parts(filename)
    if parts is None:
        return False
    for tag in parts[-1].split("."):
        if tag == "any" or "universal2" in tag:
            return True
        if "macosx" in tag and "arm64" in tag:
            return True
    return False


def _package_of(filename: str) -> tuple[str, str]:
    parts = _wheel_name_parts(filename)
    if parts is None:
        raise ValueError(f"not a wheel filename: {filename}")
    return parts[0].replace("_", "-").lower(), parts[1]


def _release_hashes(pkg: str, version: str, cache: dict) -> list[str]:
    """sha256 of every macOS-arm64 / pure wheel PyPI publishes for *pkg*==*version*."""
    key = (pkg, version)
    if key not in cache:
        url = f"https://pypi.org/pypi/{pkg}/{version}/json"
        with urllib.request.urlopen(url, timeout=60) as resp:
            data = json.load(resp)
        cache[key] = sorted(
            f["digests"]["sha256"]
            for f in data["urls"]
            if _is_macos_or_pure_wheel(f["filename"])
        )
    return cache[key]


def _download_closure(py: str, abi: str, dest: pathlib.Path) -> list[pathlib.Path]:
    requirements = dest / "mlx_lm_runtime.in"
    requirements.write_text(f"mlx-lm=={MLX_LM_VERSION}\nmlx=={MLX_VERSION}\n")
    try:
        proc = subprocess.run(
            [
                sys.executable, "-m", "pip", "download",
                "--only-binary=:all:",
                "--platform", PLATFORM,
                "--python-version", py,
                "--implementation", "cp",
                "--abi", abi,
                "-r", str(requirements),
                "-d", str(dest),
            ],
            capture_output=True,
            text=True,
            timeout=1800,
        )
    except subprocess.TimeoutExpired:
        raise SystemExit(f"pip download timed out for py{py} (index stalled?)")
    if proc.returncode != 0:
        print(proc.stdout[-2000:], proc.stderr[-2000:], file=sys.stderr)
        raise SystemExit(f"pip download failed for py{py}")
    return sorted(dest.glob("*.whl"))


def main() -> int:
    cache: dict = {}
    for py, abi in PYTHONS:
        with tempfile.TemporaryDirectory() as tmp:
            wheels = _download_closure(py, abi, pathlib.Path(tmp))
            pins: dict[str, str] = {}
            for wheel in wheels:
                pkg, version = _package_of(wheel.name)
                pins[pkg] = version
            missing = {"mlx-lm", "mlx"} - set(pins)
            if missing:
                print(f"py{py}: closure missing {sorted(missing)}", file=sys.stderr)
                return 1
            lines = [HEADER.format(
                py=py,
                platform=PLATFORM,
                lm=pins["mlx-lm"],
                mlx=pins["mlx"],
            )]
            for pkg in sorted(pins):
                hashes = _release_hashes(pkg, pins[pkg], cache)
                if not hashes:
                    print(
                        f"py{py}: no macOS/pure wheel published for "
                        f"{pkg}=={pins[pkg]}",
                        file=sys.stderr,
                    )
                    return 1
                lines.append(f"{pkg}=={pins[pkg]} \\")
                for idx, sha in enumerate(hashes):
                    cont = " \\" if idx < len(hashes) - 1 else ""
                    lines.append(f"    --hash=sha256:{sha}{cont}")
            target = REPO / "tinyagentos" / "installers" / (
                f"mlx_lm_requirements_py{py.replace('.', '')}.txt"
            )
            target.write_text("\n".join(lines) + "\n")
            print(f"wrote {target.relative_to(REPO)} ({len(pins)} packages)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
