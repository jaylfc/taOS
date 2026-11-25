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


def test_option_values_are_not_package_names(tmp_path):
    """--retries 10 --timeout 60 and other option values must not be read as packages.

    The undeclared_pip_installs function should skip option values like the "10" in
    "--retries 10" and "60" in "--timeout 60" instead of treating them as packages.
    """
    # Test file with option values that should be skipped
    fake_installer1 = tmp_path / "install.sh"
    fake_installer1.write_text("""#!/bin/bash
./.venv/bin/pip install --quiet --retries 10 --timeout 60 -c scripts/install-constraints.txt -e .
""")

    result = check_install_licences.undeclared_pip_installs(fake_installer1)
    assert result == [], (
        f"option values were read as package names: {result}"
    )

    # Positive control: a package name after --retries should be detected
    fake_installer2 = tmp_path / "install2.sh"
    fake_installer2.write_text("""#!/bin/bash
./.venv/bin/pip install --quiet --retries 10 notarealpkg
""")

    result = check_install_licences.undeclared_pip_installs(fake_installer2)
    assert result == ["notarealpkg"], (
        f"expected [\"notarealpkg\"], got {result}"
    )



def test_value_option_without_value_does_not_swallow_next_flag(tmp_path):
    fake_installer = tmp_path / "install.sh"
    fake_installer.write_text("""#!/bin/bash
./.venv/bin/pip install --quiet --retries --timeout 60 notarealpkg
""")
    result = check_install_licences.undeclared_pip_installs(fake_installer)
    assert result == ["notarealpkg"], (
        f"expected [\"notarealpkg\"], got {result}"
    )


def test_no_binary_value_is_not_a_package(tmp_path):
    """--no-binary :all: must not be read as a package name."""
    fake_installer = tmp_path / "install.sh"
    fake_installer.write_text("""#!/bin/bash
./.venv/bin/pip install --no-binary :all: notarealpkg
""")
    result = check_install_licences.undeclared_pip_installs(fake_installer)
    assert result == ["notarealpkg"], (
        f"expected [\"notarealpkg\"], got {result}"
    )


def test_only_binary_value_is_not_a_package(tmp_path):
    """--only-binary :all: must not be read as a package name."""
    fake_installer = tmp_path / "install.sh"
    fake_installer.write_text("""#!/bin/bash
./.venv/bin/pip install --only-binary :all: notarealpkg
""")
    result = check_install_licences.undeclared_pip_installs(fake_installer)
    assert result == ["notarealpkg"], (
        f"expected [\"notarealpkg\"], got {result}"
    )


def test_log_and_upgrade_strategy_values_are_not_packages(tmp_path):
    """--log <file> and --upgrade-strategy <mode> must not be read as packages.

    `--log build.log` consumes a filename and `--upgrade-strategy eager` consumes
    a mode; each is a value-taking option, so the token after it is the flag's
    value rather than an undeclared package. Without both flags present in
    ``_PIP_VALUE_OPTIONS`` the following token was mistaken for a package.
    """
    fake_installer = tmp_path / "install.sh"
    fake_installer.write_text("""#!/bin/bash
./.venv/bin/pip install --log build.log --upgrade-strategy eager notarealpkg
""")
    result = check_install_licences.undeclared_pip_installs(fake_installer)
    assert result == ["notarealpkg"], (
        f"expected [\"notarealpkg\"], got {result}"
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


def test_agpl_dependency_is_accepted():
    """An AGPL-3.0(-or-later) dependency is compatible with the shipped AGPL.

    taOS has been AGPL-3.0-or-later only since 2026-10-01, so a dependency that
    is itself AGPL-3.0-or-later (or plain AGPL-3.0) may be bundled. The old
    dual-licence gate blocked any "agpl" substring; that must no longer happen.
    """
    assert check_install_licences.classify_licence("AGPL-3.0-or-later") is None
    assert check_install_licences.classify_licence("AGPL-3.0") is None


def test_gpl_3_0_dependency_is_accepted():
    """GPL-3.0(-or-later) is compatible with AGPL-3.0 and must not be blocked."""
    assert check_install_licences.classify_licence("GPL-3.0-or-later") is None
    assert check_install_licences.classify_licence("GPL-3.0-only") is None


def test_gpl_2_0_only_is_blocked():
    """GPL-2.0-only cannot be combined into an AGPL-3.0 work and stays blocked."""
    assert check_install_licences.classify_licence("GPL-2.0-only") == "blocked"


def test_non_commercial_licence_is_blocked():
    """A non-commercial licence cannot be redistributed inside the shipped venv."""
    assert check_install_licences.classify_licence("NonCommercial") == "blocked"
    assert check_install_licences.classify_licence("Non-Commercial") == "blocked"


def test_proprietary_style_substring_is_not_narrowed_to_word_boundaries():
    """The non-Commons-Clause patterns stay plain substrings on purpose.

    A gate erring toward a false positive costs a minute of by-hand triage; one
    erring toward a false negative ships a BLOCKER licence. "proprietary-ish"
    is a deliberately awkward superstring that must still trip the substring
    match, proving the fix did not quietly tighten these into word-bounded
    regexes as a side effect of the Commons Clause change.
    """
    assert check_install_licences.classify_licence("Proprietary-ish") == "blocked"


def test_gpl_2_0_without_or_later_is_blocked():
    """GPL-2.0 (without "or-later") is incompatible with AGPL-3.0 and must be blocked.

    The plain SPDX id "GPL-2.0" historically means GPL-2.0-only unless explicitly
    qualified with "or-later". taOS ships AGPL-3.0-or-later and cannot combine
    with a GPL-2.0-only dependency.
    """
    assert check_install_licences.classify_licence("GPL-2.0") == "blocked"


def test_gplv2_is_blocked():
    """GPLv2 is a common alias for GPL-2.0-only and must be blocked.

    Many packages use the short "GPLv2" form instead of the full SPDX id.
    """
    assert check_install_licences.classify_licence("GPLv2") == "blocked"


def test_elastic_2_0_is_blocked():
    """Elastic-2.0 (Elastic License v2.0) is source-available, not open source, and must be blocked.

    The Elastic License v2.0 restricts commercial SaaS use and is not compatible
    with AGPL-3.0 redistribution.
    """
    assert check_install_licences.classify_licence("Elastic-2.0") == "blocked"


def test_bsl_1_1_is_blocked():
    """BSL-1.1 (Business Source License 1.1) is source-available with a time-delayed open source conversion and must be blocked.

    BSL imposes use restrictions (e.g., no competing SaaS) during the change
    period, making it incompatible with free redistribution under AGPL-3.0.
    """
    assert check_install_licences.classify_licence("BSL-1.1") == "blocked"


def test_cc_by_nc_4_0_is_blocked():
    """CC-BY-NC-4.0 (Creative Commons Attribution-NonCommercial 4.0) is non-commercial and must be blocked.

    The NC clause prohibits commercial use, which conflicts with taOS's AGPL-3.0
    redistribution model.
    """
    assert check_install_licences.classify_licence("CC-BY-NC-4.0") == "blocked"


def test_ssp_is_blocked():
    """SSPL (Server Side Public License) is a source-available license with SaaS restrictions and must be blocked.

    Both the full name "Server Side Public License" and the SPDX id "SSPL-1.0"
    must be recognized as blocked.
    """
    assert check_install_licences.classify_licence("Server Side Public License") == "blocked"
    assert check_install_licences.classify_licence("SSPL-1.0") == "blocked"


def test_gpl_2_0_or_later_is_allowed():
    """GPL-2.0-or-later is compatible with AGPL-3.0 and must be allowed.

    The "or-later" qualifier permits upgrading to GPL-3.0, which is compatible
    with AGPL-3.0.
    """
    assert check_install_licences.classify_licence("GPL-2.0-or-later") is None


def test_gpl_3_0_is_allowed():
    """GPL-3.0 (both -only and -or-later) is compatible with AGPL-3.0 and must be allowed."""
    assert check_install_licences.classify_licence("GPL-3.0-only") is None
    assert check_install_licences.classify_licence("GPL-3.0-or-later") is None
    assert check_install_licences.classify_licence("GPL-3.0") is None


def test_agpl_3_0_is_allowed():
    """AGPL-3.0 (both -only and -or-later) is the taOS ship license and must be allowed."""
    assert check_install_licences.classify_licence("AGPL-3.0-only") is None
    assert check_install_licences.classify_licence("AGPL-3.0-or-later") is None
    assert check_install_licences.classify_licence("AGPL-3.0") is None


def test_lgpl_is_allowed():
    """LGPL (all versions) is compatible with AGPL-3.0 and must be allowed."""
    assert check_install_licences.classify_licence("LGPL-2.0-only") is None
    assert check_install_licences.classify_licence("LGPL-2.0-or-later") is None
    assert check_install_licences.classify_licence("LGPL-2.1-only") is None
    assert check_install_licences.classify_licence("LGPL-2.1-or-later") is None
    assert check_install_licences.classify_licence("LGPL-3.0-only") is None
    assert check_install_licences.classify_licence("LGPL-3.0-or-later") is None


def test_mpl_is_allowed():
    """MPL-2.0 is compatible with AGPL-3.0 and must be allowed."""
    assert check_install_licences.classify_licence("MPL-2.0") is None


def test_permissive_licenses_are_allowed():
    """Standard permissive licenses (MIT, Apache-2.0, BSD variants) must be allowed."""
    assert check_install_licences.classify_licence("MIT") is None
    assert check_install_licences.classify_licence("Apache-2.0") is None
    assert check_install_licences.classify_licence("BSD-3-Clause") is None
    assert check_install_licences.classify_licence("BSD-2-Clause") is None
    assert check_install_licences.classify_licence("ISC") is None


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
            "--locked",
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
    assert result.returncode == 0, f"uv export --locked failed (uv.lock stale or uv missing): {result.stderr}"

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
        "Regenerate with: uv export --locked --no-hashes --format requirements-txt "
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
