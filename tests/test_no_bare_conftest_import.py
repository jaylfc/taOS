"""Regression guard for card ``tsk-xplzqy``.

A bare ``from conftest import ...`` / ``import conftest`` is resolved by
``sys.path`` order, not by which file the author meant. When ``tests/e2e/``
is collected alongside ``tests/``, that name binds to
``tests/e2e/conftest.py`` -- a session-scoped shim that defines none of the
shared helpers -- so the import raises ``ImportError`` and pytest prints
``Interrupted: 1 error during collection`` and runs ZERO tests (exit 2).

This is the exact defect that used to break ``pytest tests/`` on an unmodified
tree, while CI stayed green because CI runs ``pytest tests/ --ignore=tests/e2e``.
The regression lives here, outside ``tests/conftest.py``, so it cannot be
silenced by editing the very file the anti-pattern targets.

It fails at the granularity of the defect -- the bare ``conftest`` name
binding -- not on a mere syntax error. Mirrors::

    grep -rn "^from conftest import|^import conftest" tests/
"""
from __future__ import annotations

import re
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
_SELF = Path(__file__).resolve()
_FORBIDDEN = re.compile(r"^\s*(?:from\s+conftest\s+import\b|import\s+conftest\b)")


def test_no_test_module_binds_bare_conftest() -> None:
    violations: list[str] = []
    for path in sorted(_TESTS.rglob("*.py")):
        if path.resolve() == _SELF:
            continue
        text = path.read_text()
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _FORBIDDEN.match(line):
                violations.append(f"{path}:{lineno}: {line.strip()!r}")
    assert not violations, (
        "tsk-xplzqy: no module under tests/ may bind the bare name "
        "`conftest` -- it resolves to the wrong conftest.py under `pytest "
        "tests/` and aborts the whole-suite collection. Import shared helpers "
        "from a uniquely-named module instead. Violations:\n  "
        + "\n  ".join(violations)
    )
