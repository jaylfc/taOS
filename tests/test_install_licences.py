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


# ---------------------------------------------------------------------------
# Licence classification: real PyPI classifier strings, SPDX ids and
# expressions. taOS is AGPL-3.0-or-later only, so AGPL and GPL-3.0 are fine.
# Unknown strings must FAIL, never be skipped.
# ---------------------------------------------------------------------------

def _cls(s):
    return check_install_licences.classify_licence(s)


@pytest.mark.parametrize("licence", [
    "MIT", "MIT-0", "Apache-2.0", "BSD-3-Clause", "BSD-2-Clause", "ISC",
    "MPL-2.0", "LGPL-2.1-or-later", "LGPL-3.0-only", "GPL-3.0-only",
    "GPL-3.0-or-later", "GPL-2.0-or-later", "GPL-2.0+", "AGPL-3.0-only",
    "AGPL-3.0-or-later", "PSF-2.0", "Unlicense", "CC0-1.0", "0BSD", "Zlib",
    "BSL-1.0", "Artistic-2.0",
])
def test_real_spdx_ids_allowed(licence):
    assert _cls(licence) is None, licence


@pytest.mark.parametrize("classifier", [
    "License :: OSI Approved :: MIT License",
    "License :: OSI Approved :: Apache Software License",
    "License :: OSI Approved :: BSD License",
    "License :: OSI Approved :: ISC License (ISCL)",
    "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)",
    "License :: OSI Approved :: GNU Affero General Public License v3",
    "License :: OSI Approved :: GNU Affero General Public License v3 or later (AGPLv3+)",
    "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
    "License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)",
    "License :: OSI Approved :: GNU General Public License v2 or later (GPLv2+)",
    "License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)",
    "License :: OSI Approved :: GNU Library or Lesser General Public License (LGPL)",
    "License :: OSI Approved :: Python Software Foundation License",
    "License :: OSI Approved :: The Unlicense (Unlicense)",
    "License :: CC0 1.0 Universal (CC0 1.0) Public Domain Dedication",
    "License :: Public Domain",
])
def test_real_pypi_classifiers_allowed(classifier):
    assert _cls(classifier) is None, classifier


@pytest.mark.parametrize("licence", [
    "GPL-2.0", "GPL-2.0-only", "GPLv2",
    "License :: OSI Approved :: GNU General Public License v2 (GPLv2)",
    "SSPL-1.0", "Server Side Public License", "BUSL-1.1", "BSL-1.1",
    "Elastic-2.0", "Elastic License 2.0",
    "CC-BY-NC-4.0", "CC-BY-NC-SA-4.0", "CC-BY-ND-4.0", "CC-BY-NC-ND-4.0",
    "License :: Free for non-commercial use",
    "License :: Other/Proprietary License",
    "LicenseRef-Proprietary", "Proprietary", "Non-Commercial Use Only",
    "MIT WITH Commons-Clause", "Apache-2.0 AND CC-BY-NC-4.0",
])
def test_restrictive_classes_blocked(licence):
    assert _cls(licence) == "blocked", licence


@pytest.mark.parametrize("licence", [
    "TotallyMadeUp-9.9", "Some Bespoke Terms", "GNU General Public License",
    "", "EPL-2.0", "CC-BY-SA-4.0", "License :: Freely Distributable",
])
def test_unknown_strings_fail_not_skip(licence):
    assert _cls(licence) == "unknown-licence", licence


@pytest.mark.parametrize("expr,expected", [
    ("MIT OR Apache-2.0", None),
    ("MIT AND BSD-3-Clause", None),
    ("(MIT OR GPL-2.0-only) AND Apache-2.0", None),
    ("GPL-2.0-only OR MIT", None),                 # OR passes if one side allowed
    ("GPL-2.0-only OR SSPL-1.0", "blocked"),
    ("MIT AND GPL-2.0-only", "blocked"),           # any non-allowed AND operand blocks
    ("MIT AND CC-BY-NC-4.0", "blocked"),
    ("(MIT OR Apache-2.0) AND SSPL-1.0", "blocked"),
    ("MIT AND TotallyMadeUp-1.0", "unknown-licence"),
    ("MIT OR TotallyMadeUp-1.0", None),
    ("GPL-2.0-or-later WITH Classpath-exception-2.0", None),
    ("GPL-3.0-only WITH GCC-exception-3.1", None),
    ("Apache-2.0 WITH LLVM-exception", None),
    ("MIT WITH Some-Unknown-exception", "unknown-licence"),
    ("Artistic-1.0 OR GPL-2.0-or-later", None),
    ("MIT or Apache License 2.0", None),
])
def test_spdx_expressions(expr, expected):
    assert _cls(expr) == expected, expr


def test_multiple_classifiers_any_blocked_blocks():
    both = "License :: OSI Approved :: MIT License; License :: Other/Proprietary License"
    assert _cls(both) == "blocked"


def _write_lock(path, packages):
    lines = ['version = 1', '']
    lines += ['[[package]]', 'name = "tinyagentos"', 'version = "0"', 'dependencies = [']
    lines += [f'    {{ name = "{n}" }},' for n in packages]
    lines += [']', '']
    for n in packages:
        lines += ['[[package]]', f'name = "{n}"', 'version = "1.0"', '']
    path.write_text("\n".join(lines))


def test_main_end_to_end_on_fixture_environment(tmp_path, capsys):
    """Run main() through the real argv path against a fixture lock: blocked,
    unknown and AGPL packages, plus a first-party package that must be skipped
    (this is the path that raised NameError on FIRST_PARTY)."""
    lock = tmp_path / "uv.lock"
    _write_lock(lock, ["goodpkg", "agplpkg", "badpkg", "mysterypkg", "taosmd"])
    table = {
        "goodpkg": "MIT OR Apache-2.0",
        "agplpkg": "AGPL-3.0-or-later",
        "badpkg": "GPL-2.0-only",
        "mysterypkg": "Weird Custom Terms",
        "taosmd": "MIT WITH Commons-Clause",
    }
    rc = check_install_licences.main(
        ["--licences", "--lock", str(lock)],
        lookup=lambda name, version: table[name],
    )
    out = capsys.readouterr().out
    assert rc == 1
    assert "BLOCKER badpkg" in out
    assert "unknown-licence mysterypkg" in out
    assert "goodpkg" not in out and "agplpkg" not in out and "taosmd" not in out.replace("tinyagentos", "")


def test_main_end_to_end_clean_fixture_exits_zero(tmp_path, capsys):
    lock = tmp_path / "uv.lock"
    _write_lock(lock, ["a", "b", "taosmd"])
    table = {"a": "MIT", "b": "License :: OSI Approved :: GNU Affero General Public License v3", "taosmd": "MIT WITH Commons-Clause"}
    rc = check_install_licences.main(
        ["--licences", "--lock", str(lock)], lookup=lambda n, v: table[n],
    )
    assert rc == 0, capsys.readouterr().out
