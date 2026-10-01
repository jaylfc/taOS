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

import tinyagentos.app as _app_mod
_app_mod.PROJECT_DIR = Path({pytester_path!r})

_DATA_MUTATIONS: list[str] = []
_XDIST_CONTROLLER_MUTATIONS: list[str] = []
_XDIST = bool(os.environ.get("PYTEST_XDIST_WORKER"))


def _collect_data_mtimes() -> dict[str, tuple[float, int]]:
    data_dir = _app_mod.PROJECT_DIR / "data"
    snapshot: dict[str, tuple[float, int]] = {{}}
    if not data_dir.is_dir():
        return snapshot
    for path in data_dir.rglob("*"):
        if path.is_file():
            try:
                st = path.stat()
                snapshot[str(path)] = (st.st_mtime_ns, st.st_size)
            except OSError:
                pass
    return snapshot


@pytest.fixture(autouse=True, scope="function")
def _guard_data_dir_mutation(request):
    before = _collect_data_mtimes()
    yield
    after = _collect_data_mtimes()
    mutated = []
    for path, mt in after.items():
        if path not in before or mt != before[path]:
            mutated.append(path)
    for path in before:
        if path not in after:
            mutated.append(f"{{path}} (deleted)")
    if mutated:
        if _XDIST:
            _DATA_MUTATIONS.extend(mutated)
        else:
            raise RuntimeError(
                f"PROJECT_DIR/data was mutated during test {{request.node.nodeid}}. The following "
                f"files were created or modified: {{', '.join(sorted(mutated))}}. "
                "Tests must not write into the repo's data directory."
            )


def pytest_testnodedown(node, error):
    _XDIST_CONTROLLER_MUTATIONS.extend(node.workeroutput.get("data_mutations", []))


def pytest_sessionfinish(session, exitstatus):
    if hasattr(session.config, "workeroutput"):
        if _DATA_MUTATIONS:
            session.config.workeroutput["data_mutations"] = list(set(_DATA_MUTATIONS))
    else:
        if _XDIST_CONTROLLER_MUTATIONS:
            print(
                "PROJECT_DIR/data was mutated during the xdist session. The following "
                f"files were created or modified: {{', '.join(sorted(set(_XDIST_CONTROLLER_MUTATIONS)))}}. "
                "Tests must not write into the repo's data directory."
            )
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
        elif _DATA_MUTATIONS:
            raise RuntimeError(
                "PROJECT_DIR/data was mutated during the session. The following "
                f"files were created or modified: {{', '.join(sorted(set(_DATA_MUTATIONS)))}}. "
                "Tests must not write into the repo's data directory."
            )
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
