"""Guards against pyproject.toml and uv.lock version drift.

If you bump the `[project] version` in pyproject.toml, the `tinyagentos`
package version in uv.lock must be updated to the same release. This test
asserts that using PEP 440 normalization so pre-releases map correctly.
"""

import re
import tomllib
from pathlib import Path

from packaging.version import Version

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
UV_LOCK = ROOT / "uv.lock"


def _read_pyproject_version():
    with PYPROJECT.open("rb") as f:
        data = tomllib.load(f)
    return str(data["project"]["version"])


def _read_lock_version():
    text = UV_LOCK.read_text()
    in_tinyagentos = False
    for line in text.splitlines():
        m = re.match(r'name\s*=\s*"tinyagentos"', line.strip())
        if m:
            in_tinyagentos = True
            continue
        if in_tinyagentos:
            m = re.match(r'version\s*=\s*"([^"]+)"', line.strip())
            if m:
                return m.group(1)
            if line.strip().startswith("[[package]]"):
                break
    raise AssertionError("tinyagentos entry not found in uv.lock")


def test_pyproject_and_lock_versions_match():
    pyproject_version = _read_pyproject_version()
    lock_version = _read_lock_version()
    # Full PEP 440 equality, not just .release: comparing only the release
    # segments would treat 1.0.0 and 1.0.0rc1 as matching and silently miss
    # pre-release/dev drift. Version() equality still normalizes trailing zeros
    # (1.0 == 1.0.0) but includes the pre/post/dev segments.
    assert Version(pyproject_version) == Version(lock_version), (
        f"pyproject declares {pyproject_version!r} but uv.lock pins tinyagentos "
        f"at {lock_version!r}; these versions must match."
    )
