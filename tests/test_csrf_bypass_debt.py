"""The `csrf_bypass` escape hatch exists, works, and is used by NOTHING.

`tests/conftest.py` runs every test against the real `verify_csrf` and offers
``@pytest.mark.csrf_bypass`` as an explicit opt-out.  An escape hatch that
nobody watches becomes the default again: the mechanism this replaced was a
path-substring carve-out that silently covered 787 of 788 test files.

So this module asserts the stronger property.  Not "the bypass list matches a
frozen set of names" -- a frozenset has to be edited to grow, but editing it is
a one-line change nobody reviews as a privilege grant.  The assertion here is
that the list is **EMPTY**: no test anywhere opts out.  Every module that
builds its own signed-in client sends `X-CSRF-Token` the way the SPA does (see
`tests/taos_test_csrf.py`), so none of them needs the hatch.

If you are here because this test went red: adding a marker is almost certainly
the wrong fix.  A red under real CSRF means a test reaches a route in a way the
real caller cannot.  Give that test's client `event_hooks=csrf_event_hooks()`
instead.  Marking it is a privilege grant to the test, and the point of #2081
is that such a grant hides real defects.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent

# Matches `@pytest.mark.csrf_bypass` and `pytestmark = pytest.mark.csrf_bypass`,
# but NOT a function whose *name* merely contains "csrf_bypass" (there is one:
# `test_app_csrf_wiring.py::test_this_module_is_outside_any_csrf_bypass`) and
# not conftest's `CSRF_BYPASS_MARKER = "csrf_bypass"` string constant.
_MARKER_USE = re.compile(r"\bmark\.csrf_bypass\b")


def _modules_using_the_bypass(root: Path = TESTS_DIR) -> list[str]:
    """Every test module that opts out of real CSRF, by source inspection.

    Source inspection rather than a pytest hook because a marker on a test that
    is skipped, deselected, or collected only in another shard is still debt --
    and a collection-time query would not see it.

    This module excludes itself: `test_the_bypass_marker_still_works` below is
    deliberately marked, so counting it would make the guard permanently red on
    its own proof.
    """
    found = []
    # Only test modules: a marker is what pytest reads off a collected test, so
    # `conftest.py` -- which DOCUMENTS the marker in its comments and docstrings
    # -- is not debt, and matching it would make this guard permanently red on
    # the very text that explains it.
    #
    # BOTH default patterns, not just `test_*.py`. pyproject.toml sets no
    # `python_files`, so pytest's default discovery collects `test_*.py` AND
    # `*_test.py`. Scanning only the first would let a module named
    # `something_test.py` carry the marker, be collected and run with CSRF
    # switched off, and stay invisible here -- the guard would then pass
    # vacuously, which is the failure mode it exists to prevent. It is also the
    # exact defect this PR removed from the bypass itself: behaviour that hangs
    # on what a file is NAMED.
    candidates = set(root.rglob("test_*.py")) | set(root.rglob("*_test.py"))
    for path in sorted(candidates):
        if path.resolve() == Path(__file__).resolve():
            continue
        if _MARKER_USE.search(path.read_text(encoding="utf-8", errors="replace")):
            found.append(str(path.relative_to(root)))
    return found


def test_no_test_module_opts_out_of_csrf():
    using = _modules_using_the_bypass()

    assert using == [], (
        "These modules opt out of real CSRF with @pytest.mark.csrf_bypass:\n  "
        + "\n  ".join(using)
        + "\n\nThe bypass list is meant to stay EMPTY. A test that 403s under "
        "real CSRF is reaching a route in a way the real caller cannot; give "
        "its client `event_hooks=csrf_event_hooks()` (tests/taos_test_csrf.py) "
        "so it sends X-CSRF-Token like the SPA, rather than switching the "
        "check off."
    )


def test_the_scanner_can_actually_see_a_marker(tmp_path):
    """Guard the guard.

    A scanner that matches nothing would report an empty debt list on a tree
    full of markers -- passing for the same reason it would pass on a clean
    tree.  Point it at a directory that DOES contain one and require a hit.
    """
    (tmp_path / "test_marked.py").write_text(
        "import pytest\npytestmark = pytest.mark.csrf_bypass\n"
    )
    (tmp_path / "test_named_only.py").write_text(
        "def test_this_module_is_outside_any_csrf_bypass():\n    pass\n"
    )

    assert _modules_using_the_bypass(tmp_path) == ["test_marked.py"]


def test_the_scanner_sees_both_pytest_filename_patterns(tmp_path):
    """The scanner's file set must match what pytest actually collects.

    `pyproject.toml` sets no `python_files`, so pytest collects `test_*.py` AND
    `*_test.py`.  A scanner covering only the first is one level coarser than
    the thing it guards: a marker in `something_test.py` would run with CSRF
    switched off and this guard would still report an empty debt list.

    The sibling test above CANNOT catch that -- it only ever writes
    `test_*.py` files, so it passes identically with either glob.  This one
    fails on the narrow glob and passes on the correct one.
    """
    (tmp_path / "legacy_test.py").write_text(
        "import pytest\npytestmark = pytest.mark.csrf_bypass\n"
    )

    assert _modules_using_the_bypass(tmp_path) == ["legacy_test.py"]


@pytest.mark.csrf_bypass
def test_the_bypass_marker_still_works():
    """The hatch is unused, so prove here that it is not merely broken.

    An opt-out nobody exercises can rot into a no-op -- and a no-op opt-out
    reads as "nothing needs it" for exactly the wrong reason.  Under the marker
    the installed `verify_csrf` must be conftest's stub, not the real one.
    """
    from tinyagentos.middleware import csrf

    assert csrf.verify_csrf.__module__ != "tinyagentos.middleware.csrf", (
        "the csrf_bypass marker did not replace verify_csrf -- the documented "
        "escape hatch is broken"
    )
