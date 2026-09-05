"""The `skip_if_no_embed_backend` escape hatch exists, works, and is used by NOTHING.

`tests/conftest.py` offers ``@pytest.mark.skip_if_no_embed_backend`` so that a
test which genuinely cannot run without an embedding backend (a reachable qmd
service, or a local ONNX runtime) skips instead of failing on a box that has
neither.

The hatch is guarded the same way `csrf_bypass` is, and for the same reason: a
skip marker nobody watches is a way to turn a red test green without fixing it.
The assertion here is the strong one -- the list of tests carrying the marker is
**EMPTY** -- because no test in this repository needs a backend. Every test that
looked like it did (`test_embed_returns_vector`, `test_embed_returns_onnx_model`,
`test_embed_without_npu_uses_cpu`, and the seven `test_routes_taosmd.py` setup
tests) drives a fully mocked object: an `AsyncMock(spec=httpx.AsyncClient)`, a
hand-built `_snapshot`, or a patched `_run_setup`. Measured on a box with no
`onnx` package and `TAOSMD_URL` pointed at a host that does not exist, all ten
passed.

If you are here because this test went red: marking the test is almost certainly
the wrong fix. Ask first whether the test actually reaches a backend. If it
mocks one, it does not need the marker, and adding it silently removes the test
from every CI row -- which is coverage loss disguised as a green suite.
"""
from __future__ import annotations

import re
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
CONFTEST = TESTS_DIR / "conftest.py"

# Matches `@pytest.mark.skip_if_no_embed_backend` and the `pytestmark = ...`
# form, but NOT conftest's `EMBED_BACKEND_MARKER = "skip_if_no_embed_backend"`
# string constant, nor a function whose name merely contains the words.
_MARKER_USE = re.compile(r"\bmark\.skip_if_no_embed_backend\b")

# `def pytest_configure(` at column 0 -- a module-level definition. Nested or
# indented definitions are not hook registrations and do not collide.
_PYTEST_CONFIGURE_DEF = re.compile(r"^def pytest_configure\(", re.MULTILINE)


def _modules_using_the_marker(root: Path = TESTS_DIR) -> list[str]:
    """Every test module that opts out of running, by source inspection.

    Source inspection rather than a pytest hook because the marker's whole
    effect is to make the test not run: a collection-time query would have to
    see an item the marker is designed to remove from the report.

    BOTH default discovery patterns are scanned. `pyproject.toml` sets no
    `python_files`, so pytest collects `test_*.py` AND `*_test.py`; scanning
    only the first would let a module named `something_test.py` carry the
    marker, skip its whole file in CI, and stay invisible here -- the guard
    would pass vacuously, which is the exact failure it exists to prevent.
    """
    found = []
    candidates = set(root.rglob("test_*.py")) | set(root.rglob("*_test.py"))
    for path in sorted(candidates):
        if path.resolve() == Path(__file__).resolve():
            continue
        if _MARKER_USE.search(path.read_text(encoding="utf-8", errors="replace")):
            found.append(str(path.relative_to(root)))
    return found


def test_no_test_module_skips_itself_for_a_missing_embed_backend():
    using = _modules_using_the_marker()

    assert using == [], (
        "These modules skip tests with @pytest.mark.skip_if_no_embed_backend:\n  "
        + "\n  ".join(using)
        + "\n\nThe list is meant to stay EMPTY. Check what the marked test "
        "actually calls: if it drives a mock (AsyncMock, a hand-built "
        "_snapshot, a patched _run_setup) it does not need a backend, and the "
        "marker only deletes it from CI."
    )


def test_the_scanner_can_actually_see_a_marker(tmp_path):
    """Guard the guard.

    A scanner that matches nothing reports an empty debt list on a tree full of
    markers -- passing for the same reason it would pass on a clean tree. Point
    it at a directory that DOES contain one and require a hit.
    """
    (tmp_path / "test_marked.py").write_text(
        "import pytest\npytestmark = pytest.mark.skip_if_no_embed_backend\n"
    )
    (tmp_path / "test_named_only.py").write_text(
        "def test_skip_if_no_embed_backend_is_only_a_name():\n    pass\n"
    )

    assert _modules_using_the_marker(tmp_path) == ["test_marked.py"]


def test_the_scanner_sees_both_pytest_filename_patterns(tmp_path):
    """The scanner's file set must match what pytest actually collects.

    A scanner covering only `test_*.py` is one level coarser than the thing it
    guards: a marker in `something_test.py` would skip that module in CI and
    this guard would still report an empty debt list. The sibling test above
    cannot catch that -- it only ever writes `test_*.py` files, so it passes
    identically with either glob.
    """
    (tmp_path / "legacy_test.py").write_text(
        "import pytest\npytestmark = pytest.mark.skip_if_no_embed_backend\n"
    )

    assert _modules_using_the_marker(tmp_path) == ["legacy_test.py"]


def test_conftest_defines_pytest_configure_exactly_once():
    """Two `def pytest_configure` in one module is last-wins, not additive.

    Python rebinds the name, so the earlier body never runs and pytest only
    ever calls the later one. Nothing fails: the dead half looks registered,
    and the next edit to it is a silent no-op in CI.
    """
    hits = _PYTEST_CONFIGURE_DEF.findall(CONFTEST.read_text(encoding="utf-8"))

    assert len(hits) == 1, (
        f"tests/conftest.py defines pytest_configure {len(hits)} times; only "
        "the last one runs. Merge the bodies into a single hook."
    )


def test_the_marker_gate_is_wired_and_probes_a_real_capability():
    """The hatch is unused, so prove here that it is not merely broken.

    An opt-out nobody exercises rots into a no-op, and a no-op opt-out reads as
    "nothing needs it" for exactly the wrong reason. Two properties:

      * the probe answers True on a box that can embed. `onnxruntime` -- the
        package that actually runs an ONNX model -- is a dependency of this
        repo, so an importable `onnxruntime` must satisfy the probe. Probing
        for `onnx` (the model-format library, which taOS does not depend on)
        answers False on a perfectly capable box and skips the marked tests.
      * the gate that consumes it is installed as a collection hook.
    """
    import importlib.util

    import conftest  # tests/conftest.py, on sys.path via rootdir

    assert hasattr(conftest, "pytest_collection_modifyitems"), (
        "conftest lost the collection hook that applies the skip -- the marker "
        "is inert and every test carrying it would run unguarded"
    )

    if importlib.util.find_spec("onnxruntime") is None:
        return  # nothing to assert: this box genuinely has no ONNX runtime

    assert conftest._is_embed_backend_available() is True, (
        "onnxruntime is importable but the probe says no embed backend is "
        "available -- the marked tests skip on a box that can embed"
    )


class _FakeItem:
    """Minimal stand-in for a collected pytest item.

    The gate only ever asks an item two things: does it carry the marker, and
    take this skip. Driving those directly proves the gate's behaviour on a box
    whose real probe answers the opposite way -- which is every box that has
    `onnxruntime` installed, i.e. every CI row.
    """

    def __init__(self, marked: bool):
        self._marked = marked
        self.added = []

    def get_closest_marker(self, name):
        return object() if (self._marked and name == "skip_if_no_embed_backend") else None

    def add_marker(self, marker):
        self.added.append(marker)


def test_the_gate_skips_a_marked_item_only_when_no_backend_can_serve_it(monkeypatch):
    """Both directions.

    Asserting only that a marked item is skipped when the probe says "no
    backend" passes identically for a gate that skips everything. The
    unmarked item and the backend-present case are the halves that fail on
    an over-broad gate.
    """
    import conftest

    marked, plain = _FakeItem(marked=True), _FakeItem(marked=False)
    monkeypatch.setattr(conftest, "_is_embed_backend_available", lambda: False)
    conftest.pytest_collection_modifyitems([marked, plain])

    assert len(marked.added) == 1, "a marked item was not skipped with no backend present"
    assert marked.added[0].name == "skip"
    assert marked.added[0].kwargs["reason"] == conftest._EMBED_BACKEND_SKIP_REASON
    assert plain.added == [], "the gate skipped an item that never asked for the marker"

    marked_ok = _FakeItem(marked=True)
    monkeypatch.setattr(conftest, "_is_embed_backend_available", lambda: True)
    conftest.pytest_collection_modifyitems([marked_ok])

    assert marked_ok.added == [], (
        "a marked item was skipped on a box that CAN embed -- the acceptance "
        "criterion is that marked tests still execute on a configured box"
    )
