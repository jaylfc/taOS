"""Gates on what a real server install lands in the venv.

These are install-time properties: no test over ``tinyagentos/`` can see them,
because nothing in taOS imports the offending package — a licence governs
distribution, not import. The evidence lives in ``uv.lock`` and in
``scripts/install-server.sh``, so that is what these assert against. All offline.
"""
from __future__ import annotations

import importlib.util
import re
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


def test_installer_pip_uses_constraints_file():
    """Every controller pip install must pin against the committed constraints file.

    ``scripts/install-server.sh`` runs ``pip install -e .[proxy]`` (or the
    extras-variant). Without ``-c scripts/install-constraints.txt``, pip may
    resolve newer versions inside the declared ranges than the locked graph
    the licence gate audits, so a relicense reaches users without tripping
    the gate.
    """
    installer = check_install_licences.REPO_ROOT / "scripts" / "install-server.sh"
    constraints_rel = "scripts/install-constraints.txt"
    for line in installer.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        # Match actual pip install invocations: the line must start with pip
        # (possibly via the venv path), not just contain the string inside a
        # log message or comment.
        match = re.search(r"^(?:\.?/\.venv/bin/)?pip(?:3)?\s+install\s+(.*)$", stripped)
        if not match:
            continue
        args = match.group(1)
        assert f"-c {constraints_rel}" in args, (
            f"pip install in install-server.sh lacks -c {constraints_rel}: {stripped}"
        )


def test_constraints_file_matches_lock():
    """``scripts/install-constraints.txt`` must match what ``uv export`` emits for the audited set.

    Regenerates the constraints from ``uv.lock`` for the default + proxy
    extras and fails if the committed file drifts.
    """
    import subprocess

    constraints_path = check_install_licences.REPO_ROOT / "scripts" / "install-constraints.txt"
    assert constraints_path.exists(), f"Constraints file not found: {constraints_path}"

    result = subprocess.run(
        [
            "uv", "export",
            "--no-hashes",
            "--format", "requirements-txt",
            "--package", "tinyagentos",
            "--extra", "proxy",
            "--no-dev",
        ],
        capture_output=True,
        text=True,
        cwd=check_install_licences.REPO_ROOT,
    )
    assert result.returncode == 0, f"uv export failed:\n{result.stderr}"

    fresh_lines = []
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped == "-e .":
            continue
        if " ; " in stripped:
            stripped = stripped.split(" ; ")[0]
        if "==" in stripped:
            fresh_lines.append(stripped)

    committed_raw = constraints_path.read_text(encoding="utf-8")
    expected = "\n".join(fresh_lines) + "\n"
    assert committed_raw == expected, (
        f"{constraints_path} is out of sync with uv.lock for the default+proxy set. "
        "Regenerate with: uv export --no-hashes --format requirements-txt "
        "--package tinyagentos --extra proxy --no-dev "
        "| grep -E '^[A-Za-z]' | grep -v '^#' | grep -v '^\\-e ' "
        "| sed 's/ ;.*//' > scripts/install-constraints.txt"
    )


def test_constraints_set_equals_audited_install_set():
    """The committed constraints file must pin exactly the packages the licence gate audits.

    A fresh install reads ``scripts/install-constraints.txt``, so its set of
    pinned packages must equal the set ``resolve_install_set`` returns from
    ``uv.lock`` for the same extras.
    """
    constraints_path = check_install_licences.REPO_ROOT / "scripts" / "install-constraints.txt"
    assert constraints_path.exists(), f"Constraints file not found: {constraints_path}"

    constraint_names = set()
    for line in constraints_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "==" in stripped:
            constraint_names.add(check_install_licences.canonical(stripped.split("==")[0]))

    audited = check_install_licences.resolve_install_set()
    audited_canonical = {check_install_licences.canonical(n) for n in audited}

    assert constraint_names == audited_canonical, (
        f"Constraints set ({len(constraint_names)}) differs from audited install set ({len(audited_canonical)}).\n"
        f"Only in constraints: {sorted(constraint_names - audited_canonical)}\n"
        f"Only in audited:   {sorted(audited_canonical - constraint_names)}"
    )
