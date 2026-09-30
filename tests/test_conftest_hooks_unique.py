"""Guard: ensure tests/conftest.py defines each pytest_* hook exactly once.

A previous PR added a second `pytest_sessionfinish` which shadowed the
litellm leak guard at line 60. This test catches that regression.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest


CONFTEST_PATH = Path(__file__).resolve().parent / "conftest.py"


def _collect_module_level_pytest_hooks(source: str) -> dict[str, int]:
    """Return a mapping of hook name -> count of module-level defs."""
    tree = ast.parse(source)
    counts: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("pytest_"):
            counts[node.name] = counts.get(node.name, 0) + 1
    return counts


class TestConftestHooksUnique:
    def test_conftest_defines_each_pytest_hook_once(self):
        source = CONFTEST_PATH.read_text(encoding="utf-8")
        counts = _collect_module_level_pytest_hooks(source)
        duplicates = {name: count for name, count in counts.items() if count > 1}
        assert not duplicates, (
            f"tests/conftest.py defines the following pytest hooks more than once: {duplicates}. "
            "Each hook must be defined exactly once; combine logic into a single definition."
        )

    def test_sessionfinish_still_reports_a_litellm_leak(self, monkeypatch):
        """The litellm leak guard must still run and report leaks.

        We import conftest as a module, monkeypatch psutil.Process.children to
        return a fake litellm child, call pytest_sessionfinish, and assert the
        leak is reported (raises RuntimeError with the expected message).
        """
        # Import conftest as a module
        spec = importlib.util.spec_from_file_location("tests_conftest", CONFTEST_PATH)
        assert spec is not None and spec.loader is not None
        conftest_module = importlib.util.module_from_spec(spec)
        sys.modules["tests_conftest"] = conftest_module
        spec.loader.exec_module(conftest_module)

        # Create a fake child process that looks like litellm
        class FakeChild:
            pid = 12345

            def cmdline(self):
                return ["python", "-m", "litellm", "proxy"]

        class FakeProcess:
            def children(self, recursive=True):
                return [FakeChild()]

        # Monkeypatch psutil.Process to return our fake process
        import psutil

        original_process = psutil.Process

        def fake_process_init(pid=None):
            if pid == original_process().pid:
                return FakeProcess()
            return original_process(pid)

        monkeypatch.setattr(psutil, "Process", fake_process_init)

        # Create a stub session object
        class StubSession:
            pass

        session = StubSession()

        # Call pytest_sessionfinish - it should raise RuntimeError for the leak
        with pytest.raises(RuntimeError) as exc_info:
            conftest_module.pytest_sessionfinish(session, 0)

        assert "LITELLM PROCESS LEAK" in str(exc_info.value)
        assert "pid 12345" in str(exc_info.value)
        assert "litellm" in str(exc_info.value).lower()