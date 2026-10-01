"""Gates on what a real server install lands in the venv.

These are install-time properties: no test over ``tinyagentos/`` can see them,
because nothing in taOS imports the offending package — a licence governs
distribution, not import. The evidence lives in ``uv.lock`` and in
``scripts/install-server.sh``, so that is what these assert against. All offline.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "check_install_licences", REPO_ROOT / "scripts" / "check_install_licences.py"
)
assert _spec and _spec.loader
check_install_licences = importlib.util.module_from_spec(_spec)
sys.modules["check_install_licences"] = check_install_licences
_spec.loader.exec_module(check_install_licences)


# Packages that must never appear in the set a server install resolves, with the
# reason, so a future re-add has to argue with the reason rather than the name.
FORBIDDEN = {
    "litellm-enterprise": (
        "LicenseRef-Proprietary (BerriAI). Redistributing the taOS venv would "
        "redistribute proprietary code that the AGPL cannot cover."
    ),
}


def _install_set() -> dict[str, str]:
    return check_install_licences.resolve_install_set()


@pytest.mark.parametrize("forbidden,reason", sorted(FORBIDDEN.items()))
def test_server_install_set_excludes_forbidden_package(forbidden, reason):
    """`pip install -e .` must not land a non-redistributable package."""
    resolved = {check_install_licences.canonical(n): v for n, v in _install_set().items()}
    assert forbidden not in resolved, (
        f"{forbidden} {resolved.get(forbidden)} is in the server install set: {reason}"
    )


def test_installer_installs_nothing_pyproject_does_not_declare():
    """The installer must not pip-install packages the lockfile never resolved.

    An ad-hoc ``pip install <pkg>`` in install-server.sh puts a package on a
    production box that ``uv.lock`` has never seen, so a later upstream
    relicence or CVE reaches users without tripping any gate here.
    """
    undeclared = check_install_licences.undeclared_pip_installs()
    assert undeclared == [], (
        "install-server.sh pip-installs packages absent from pyproject.toml: "
        f"{undeclared}"
    )


def test_unreadable_licence_is_its_own_failing_finding():
    """A PyPI release with no expression/license/classifiers must fail the gate.

    ``licence_from_info`` returns ``"UNKNOWN"`` for a bare release ``info`` dict
    (no ``license_expression``, no ``license``, no ``License ::`` classifier).
    ``classify_licence`` must turn that into an ``"unknown-licence"`` finding —
    distinct wording from ``"blocked"`` — so main() counts it toward a non-zero
    exit instead of treating "we could not tell" as "this is clear".
    """
    bare_info: dict = {"license_expression": None, "license": "", "classifiers": []}
    licence = check_install_licences.licence_from_info(bare_info)
    assert licence == "UNKNOWN"
    assert check_install_licences.classify_licence(licence) == "unknown-licence"


def test_commons_clause_with_hyphen_is_flagged():
    """"MIT-0 WITH Commons-Clause" (hyphenated) must be flagged as blocked.

    A plain substring check for ``"commons clause"`` (with a space) misses the
    hyphenated spelling PyPI text commonly uses, letting a Commons-Clause
    package through the gate with no finding at all.
    """
    assert check_install_licences.classify_licence("MIT-0 WITH Commons-Clause") == "blocked"


def test_proprietary_style_substring_is_not_narrowed_to_word_boundaries():
    """The non-Commons-Clause patterns stay plain substrings on purpose.

    A gate erring toward a false positive costs a minute of by-hand triage; one
    erring toward a false negative ships a BLOCKER licence. "proprietary-ish"
    is a deliberately awkward superstring that must still trip the substring
    match, proving the fix did not quietly tighten these into word-bounded
    regexes as a side effect of the Commons Clause change.
    """
    assert check_install_licences.classify_licence("Proprietary-ish") == "blocked"
