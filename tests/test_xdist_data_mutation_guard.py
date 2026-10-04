"""xdist-aware data mutation guard.

Uses pytester to verify that a test which mutates PROJECT_DIR/data causes the
run to fail both under xdist and in serial.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest_plugins = ["pytester"]


def _make_guard_conftest(pytester_path: str, src: str) -> str:
    return f"""
import os
import sys
import pytest
from pathlib import Path

sys.path.insert(0, {src!r})
sys.path.insert(0, {src + '/tests'!r})

import tinyagentos.app as _app_mod
# The guard reads _app_mod.PROJECT_DIR at call time, so this override is what
# points it at the pytester dir. The inner run is a subprocess, but restore the
# original on unconfigure anyway so the override can never outlive this run.
_ORIG_PROJECT_DIR = _app_mod.PROJECT_DIR
_app_mod.PROJECT_DIR = Path({pytester_path!r})


def pytest_unconfigure(config):
    _app_mod.PROJECT_DIR = _ORIG_PROJECT_DIR


pytest_plugins = ["_data_guard_plugin"]
"""


def _make_mutating_test() -> str:
    return """
from pathlib import Path
from tinyagentos.app import PROJECT_DIR

def test_writes_to_data_dir():
    data_dir = PROJECT_DIR / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "zz_stray.txt").write_text("stray")
"""


def test_xdist_mutation_guard_fails_run(tmp_path, pytester):
    """Under xdist, a test that mutates PROJECT_DIR/data must fail the run."""
    src = str(Path(__file__).resolve().parent.parent)
    pytester.makepyfile(conftest=_make_guard_conftest(str(pytester.path), src))
    pytester.makepyfile(test_stray=_make_mutating_test())

    result = pytester.runpytest_subprocess("-n", "2", "-p", "xdist")
    result.stdout.fnmatch_lines(["*PROJECT_DIR/data was mutated*"])
    assert result.ret != 0


def test_serial_mutation_guard_fails_run(tmp_path, pytester):
    """Serial run: a test that mutates PROJECT_DIR/data must also fail."""
    src = str(Path(__file__).resolve().parent.parent)
    pytester.makepyfile(conftest=_make_guard_conftest(str(pytester.path), src))
    pytester.makepyfile(test_stray=_make_mutating_test())

    result = pytester.runpytest_subprocess()
    result.stdout.fnmatch_lines(["*PROJECT_DIR/data was mutated*"])
    assert result.ret != 0


def test_serial_without_xdist_does_not_internalerror(tmp_path, pytester):
    """Serial run with xdist disabled must not INTERNALERROR on the unknown hook."""
    src = str(Path(__file__).resolve().parent.parent)
    conftest_path = Path(__file__).resolve().parent / "conftest.py"
    conftest_content = conftest_path.read_text()
    conftest_with_syspath = (
        f"import sys\n"
        f"sys.path.insert(0, {src!r})\n"
        f"sys.path.insert(0, {src + '/tests'!r})\n"
        + conftest_content
    )
    pytester.makepyfile(conftest=conftest_with_syspath)
    pytester.makepyfile(test_pass="""
def test_trivial():
    assert True
""")

    result = pytester.runpytest_subprocess("-p", "no:xdist")
    assert result.ret == 0
    combined = result.stdout.str() + result.stderr.str()
    assert "unknown hook" not in combined
