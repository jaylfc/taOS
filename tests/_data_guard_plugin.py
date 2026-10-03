"""Data-dir mutation guard plugin.

A previous generation of tests set a dead env name (one the app does not
read), so create_app() fell back to PROJECT_DIR / "data" and those tests
wrote stray databases and config files into the repo's live data folder.
This autouse function-scoped fixture snapshots the mtime of every file under
PROJECT_DIR/data before each test and fails teardown if any file was
created or modified during that test.
"""

import pytest
from tinyagentos.app import PROJECT_DIR


def _collect_data_mtimes() -> dict[str, tuple[float, int]]:
    data_dir = PROJECT_DIR / "data"
    snapshot: dict[str, tuple[float, int]] = {}
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


_DATA_MUTATIONS: list[str] = []
_XDIST_CONTROLLER_MUTATIONS: list[str] = []


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
            mutated.append(f"{path} (deleted)")
    if mutated:
        # Detect an xdist worker from the config, not PYTEST_XDIST_WORKER: a nested
        # pytester run inherits that env var but is not a worker.
        if hasattr(request.config, "workerinput"):
            _DATA_MUTATIONS.extend(mutated)
        else:
            raise RuntimeError(
                f"PROJECT_DIR/data was mutated during test {request.node.nodeid}. The following "
                f"files were created or modified: {', '.join(sorted(mutated))}. "
                "Tests must not write into the repo's data directory."
            )


@pytest.hookimpl(optionalhook=True)
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
                f"files were created or modified: {', '.join(sorted(set(_XDIST_CONTROLLER_MUTATIONS)))}. "
                "Tests must not write into the repo's data directory."
            )
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
        elif _DATA_MUTATIONS:
            raise RuntimeError(
                "PROJECT_DIR/data was mutated during the session. The following "
                f"files were created or modified: {', '.join(sorted(set(_DATA_MUTATIONS)))}. "
                "Tests must not write into the repo's data directory."
            )