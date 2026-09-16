"""Guards against pyproject.toml and uv.lock version drift.

If you bump the `[project] version` in pyproject.toml, the `tinyagentos`
package version in uv.lock must be updated to the same release. This test
asserts that using PEP 440 normalization so pre-releases map correctly.

Also guards the three JS/Python version carriers that are NOT PEP 440
normalised: tinyagentos/__init__.py, desktop/package.json, and
desktop/package-lock.json (two root version fields). These are compared as
exact strings against pyproject.toml's literal version.
"""

import json
import re
import tomllib
from pathlib import Path

from packaging.version import Version

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
UV_LOCK = ROOT / "uv.lock"
INIT_PY = ROOT / "tinyagentos" / "__init__.py"
PACKAGE_JSON = ROOT / "desktop" / "package.json"
PACKAGE_LOCK_JSON = ROOT / "desktop" / "package-lock.json"


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


def _read_init_version():
    text = INIT_PY.read_text()
    m = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not m:
        raise AssertionError("__version__ not found in tinyagentos/__init__.py")
    return m.group(1)


def _read_package_json_version():
    data = json.loads(PACKAGE_JSON.read_text())
    return str(data["version"])


def _read_package_lock_versions():
    data = json.loads(PACKAGE_LOCK_JSON.read_text())
    top_level = str(data.get("version"))
    root_pkg = str(data.get("packages", {}).get("", {}).get("version"))
    return top_level, root_pkg


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


def test_pyproject_and_init_versions_match():
    pyproject_version = _read_pyproject_version()
    init_version = _read_init_version()
    # Exact string compare: __init__.py is not PEP 440 normalised.
    assert pyproject_version == init_version, (
        f"pyproject declares {pyproject_version!r} but tinyagentos/__init__.py "
        f"has {init_version!r}; these versions must match exactly."
    )


def test_pyproject_and_package_json_versions_match():
    pyproject_version = _read_pyproject_version()
    pkg_version = _read_package_json_version()
    # Exact string compare: package.json is not PEP 440 normalised.
    assert pyproject_version == pkg_version, (
        f"pyproject declares {pyproject_version!r} but desktop/package.json "
        f"has {pkg_version!r}; these versions must match exactly."
    )


def test_pyproject_and_package_lock_top_level_version_match():
    pyproject_version = _read_pyproject_version()
    top_level, _ = _read_package_lock_versions()
    # Exact string compare: package-lock.json top-level version is not PEP 440 normalised.
    assert pyproject_version == top_level, (
        f"pyproject declares {pyproject_version!r} but desktop/package-lock.json "
        f"top-level version has {top_level!r}; these versions must match exactly."
    )


def test_pyproject_and_package_lock_root_package_version_match():
    pyproject_version = _read_pyproject_version()
    _, root_pkg = _read_package_lock_versions()
    # Exact string compare: package-lock.json packages[""].version is not PEP 440 normalised.
    # The beta.52 train missed this field while the top-level was correct.
    assert pyproject_version == root_pkg, (
        f"pyproject declares {pyproject_version!r} but desktop/package-lock.json "
        f"packages[''].version has {root_pkg!r}; these versions must match exactly."
    )