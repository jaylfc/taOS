"""Tests for the core-dependency integrity guard (tsk-2nvear).

Guards against the 'importable-but-attribute-less' shape: a stale or empty
package directory (PEP 420 namespace package) that Python can import but
which lacks the public API the rest of the stack assumes.  The canonical
case is 'sniffio': anyio calls sniffio.current_async_library on every async
test; a partial sniffio raises AttributeError instead of ModuleNotFoundError,
producing 500+ identical tracebacks attributed to whichever PR happened to
run.

The helpers under test live in ``tests/conftest.py`` and are bound here by
absolute file path, never by the bare name ``conftest``: ``tests/`` is not a
package and ``tests/e2e/conftest.py`` exists, so ``from conftest import ...``
resolves to the e2e shim under ``pytest tests/`` and aborts the whole-suite
collection -- ``Interrupted: 1 error during collection``, exit 2, zero tests
run (card ``tsk-xplzqy``).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "tests_tinyagentos_conftest",
    Path(__file__).resolve().parent / "conftest.py",
)
_conftest = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_conftest)  # type: ignore[union-attr]

_CORE_DEP_CONTRACTS = _conftest._CORE_DEP_CONTRACTS
_check_core_deps = _conftest._check_core_deps
_verify_core_deps = _conftest._verify_core_deps


class TestSniffioContract:
    """RED test: the specific defect measured in tsk-2nvear.

    sniffio must carry current_async_library whenever it is importable.
    importorskip cannot catch this -- the module DOES import -- so the
    attribute is asserted explicitly.
    """

    def test_sniffio_has_current_async_library_when_importable(self):
        try:
            sniffio = importlib.import_module("sniffio")
        except ModuleNotFoundError:
            pytest.skip("sniffio not installed; anyio handles absence gracefully")
        assert hasattr(sniffio, "current_async_library"), (
            "sniffio is importable but lacks current_async_library "
            f"(file={getattr(sniffio, '__file__', None)!r}, "
            f"path={getattr(sniffio, '__path__', None)!r})"
        )


class TestCoreDepGuard:
    """The session-start guard must catch ANY half-present core dep,
    not just sniffio -- the fix must not be keyed on the string 'sniffio'
    alone."""

    def _install_stale_namespace(self, tmp_path, name):
        """Create a real regular package dir for *name* under *tmp_path*
        and prepend tmp_path to sys.path[0].  A regular package (with
        __init__.py) earlier on sys.path shadows any installed regular
        package, giving the guard something genuine to detect.  Returns
        the package dir path."""
        pkg_dir = tmp_path / name
        pkg_dir.mkdir()
        (pkg_dir / "__init__.py").write_text("")
        sys.path.insert(0, str(tmp_path))
        saved = {k: v for k, v in sys.modules.items()
                 if k == name or k.startswith(name + ".")}
        for key in list(saved):
            sys.modules.pop(key, None)
        importlib.invalidate_caches()
        self._saved_stale_modules = saved
        return pkg_dir

    def _remove_stale_namespace(self, tmp_path, name):
        """Undo _install_stale_namespace."""
        sys.path.remove(str(tmp_path))
        for key in list(sys.modules):
            if key == name or key.startswith(name + "."):
                sys.modules.pop(key, None)
        sys.modules.update(getattr(self, "_saved_stale_modules", {}))
        self._saved_stale_modules = {}

    def test_contracts_include_sniffio_with_current_async_library(self):
        assert "sniffio" in _CORE_DEP_CONTRACTS
        assert "current_async_library" in _CORE_DEP_CONTRACTS["sniffio"]

    def test_contracts_cover_more_than_sniffio(self):
        assert len(_CORE_DEP_CONTRACTS) > 1

    def test_guard_passes_on_healthy_environment(self):
        problems = _check_core_deps(_CORE_DEP_CONTRACTS)
        assert not problems, (
            "guard detected half-present deps: "
            + ", ".join(p[0] for p in problems)
        )

    def test_verify_core_deps_does_not_raise_on_healthy_env(self):
        _verify_core_deps()

    def test_guard_detects_stale_sniffio_namespace(self, tmp_path):
        """Direct reproduction of the tsk-2nvear defect: a stale sniffio/
        regular-package directory on sys.path makes the module importable
        but attribute-less."""
        pkg_dir = self._install_stale_namespace(tmp_path, "sniffio")
        try:
            problems = _check_core_deps({
                "sniffio": ("current_async_library", "AsyncLibraryNotFoundError"),
            })
            assert len(problems) == 1
            name, missing, mod = problems[0]
            assert name == "sniffio"
            assert "current_async_library" in missing
            assert mod.__file__ == str(pkg_dir / "__init__.py")
            assert mod.__path__ == [str(pkg_dir)]
        finally:
            self._remove_stale_namespace(tmp_path, "sniffio")

    def test_guard_detects_generic_half_present_module(self, tmp_path):
        """Any importable-but-attribute-less core dep is caught, not just
        sniffio."""
        pkg_dir = self._install_stale_namespace(tmp_path, "fake_half_present_pkg")
        try:
            problems = _check_core_deps({
                "fake_half_present_pkg": ("some_attribute",),
            })
            assert len(problems) == 1
            name, missing, mod = problems[0]
            assert name == "fake_half_present_pkg"
            assert missing == ("some_attribute",)
            assert mod.__file__ == str(pkg_dir / "__init__.py")
            assert mod.__path__ == [str(pkg_dir)]
        finally:
            self._remove_stale_namespace(tmp_path, "fake_half_present_pkg")

    def test_guard_skips_genuinely_absent_module(self):
        problems = _check_core_deps(
            {"definitely_not_a_real_module_xyz123": ("attr",)}
        )
        assert problems == []

    def test_verify_core_deps_raises_loudly_with_diagnostics(self, tmp_path):
        """When a half-present dep is found, _verify_core_deps raises
        RuntimeError with __file__ / __path__ / resolved package info."""
        self._install_stale_namespace(tmp_path, "another_stale_pkg")
        try:
            original = dict(_CORE_DEP_CONTRACTS)
            _CORE_DEP_CONTRACTS.clear()
            _CORE_DEP_CONTRACTS.update({
                "anyio": ("run", "create_task_group", "from_thread"),
                "another_stale_pkg": ("some_attribute",),
            })
            try:
                with pytest.raises(RuntimeError) as exc_info:
                    _verify_core_deps()
                msg = str(exc_info.value)
                assert "CORE DEP GUARD" in msg
                assert "another_stale_pkg" in msg
                assert "some_attribute" in msg
                assert "__file__" in msg
                assert "__path__" in msg
                assert "submodule_search_locations" in msg
                assert "Installed packages:" in msg
            finally:
                _CORE_DEP_CONTRACTS.clear()
                _CORE_DEP_CONTRACTS.update(original)
        finally:
            self._remove_stale_namespace(tmp_path, "another_stale_pkg")

    def test_diagnostic_contains_version_for_reported_module(self):
        """The diagnostic must print name==version for a module it reports
        on.  A raise-only assertion cannot catch a missing version -- the
        guard already raises today, which is why the output itself must be
        tested."""
        import importlib.metadata

        original = dict(_CORE_DEP_CONTRACTS)
        _CORE_DEP_CONTRACTS.clear()
        _CORE_DEP_CONTRACTS.update({
            "idna": ("_nonexistent_attr_for_test",),
        })
        try:
            with pytest.raises(RuntimeError) as exc_info:
                _verify_core_deps()
            msg = str(exc_info.value)
            expected_version = importlib.metadata.version("idna")
            assert f"idna=={expected_version}" in msg
        finally:
            _CORE_DEP_CONTRACTS.clear()
            _CORE_DEP_CONTRACTS.update(original)
