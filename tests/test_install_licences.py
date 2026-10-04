"""Gates on what a real server install lands in the venv.

These are install-time properties: no test over ``tinyagentos/`` can see them,
because nothing in taOS imports the offending package — a licence governs
distribution, not import. The evidence lives in ``uv.lock`` and in
``scripts/install-server.sh``, so that is what these assert against. All offline.
"""
from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
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

CONSTRAINTS_FILE = REPO_ROOT / "constraints.txt"
INSTALLER = REPO_ROOT / "scripts" / "install-server.sh"

# Minimum uv version that supports --all-extras and --format requirements-txt
MIN_UV_VERSION = "0.4.0"


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


def _parse_constraints(text: str) -> dict[str, tuple[str, str]]:
    """Parse constraints text into {canonical_name: (version, marker)}.

    Ignores comments, blank lines, the -e . editable line, uv's "Resolved N packages"
    header line, and uv's header comments. Returns a dict mapping canonical package
    name to (version, marker) where marker is the environment marker string
    (empty if none).
    """
    result: dict[str, tuple[str, str]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-e ") or line.startswith("Resolved "):
            continue
        # Split on ; to separate requirement from marker
        if ";" in line:
            req_part, marker_part = line.split(";", 1)
            marker = marker_part.strip()
        else:
            req_part = line
            marker = ""
        # Parse name==version
        if "==" in req_part:
            name_part, ver_part = req_part.split("==", 1)
            name = check_install_licences.canonical(name_part.strip())
            version = ver_part.split()[0].strip()
            result[name] = (version, marker)
    return result


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


def test_installer_pip_commands_use_constraints_file():
    """Every pip install in install-server.sh must pass -c constraints.txt.

    The licence gate audits uv.lock, but the installer runs pip which can pick
    newer versions inside declared ranges. Pinning via constraints.txt ensures
    the installed set matches the audited set exactly.
    """
    content = INSTALLER.read_text()
    # Reuse the pip-install detection from check_install_licences.py
    pip_installs: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        match = re.search(r"pip(?:3)?\s+install\s+(.*)$", stripped)
        if match:
            pip_installs.append(line.strip())
    assert pip_installs, "No pip install commands found in installer"
    for cmd in pip_installs:
        # Check for constraints file usage: either explicit "constraints.txt",
        # a variable reference like "$_constraints_file", or an absolute path
        # ending in "constraints.txt"
        assert ("-c constraints.txt" in cmd
                or "-c $_constraints_file" in cmd
                or '-c "$_constraints_file"' in cmd
                or re.search(r'-c\s+\S*constraints\.txt', cmd)), (
            f"pip install command missing -c constraints.txt: {cmd}"
        )


def test_constraints_file_matches_uv_lock():
    """constraints.txt must match a fresh export from uv.lock.

    Drift gate — same shape as the routes-doc gate. If uv.lock changes,
    constraints.txt must be regenerated.
    """
    assert CONSTRAINTS_FILE.exists(), (
        "constraints.txt not found — generate with: "
        "uv export --no-hashes --format requirements-txt --all-extras > constraints.txt"
    )

    # Guard: uv binary must be present and recent enough
    uv_path = shutil.which("uv")
    assert uv_path is not None, "uv binary not found on PATH — cannot run drift gate"

    # Check uv version meets minimum
    result = subprocess.run(
        ["uv", "--version"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, f"uv --version failed: {result.stderr}"
    # uv --version outputs like "uv 0.4.30 (abc123 2024-01-01)"
    version_match = re.search(r"uv\s+(\d+\.\d+\.\d+)", result.stdout)
    assert version_match, f"Could not parse uv version from: {result.stdout}"
    uv_version = version_match.group(1)
    # Compare versions properly (tuple of ints)
    uv_version_tuple = tuple(map(int, uv_version.split(".")))
    min_version_tuple = tuple(map(int, MIN_UV_VERSION.split(".")))
    assert uv_version_tuple >= min_version_tuple, (
        f"uv version {uv_version} < {MIN_UV_VERSION} — upgrade uv to run drift gate"
    )

    committed = CONSTRAINTS_FILE.read_text(encoding="utf-8")
    committed_parsed = _parse_constraints(committed)

    result = subprocess.run(
        [
            "uv",
            "export",
            "--no-hashes",
            "--format",
            "requirements-txt",
            "--all-extras",
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, f"uv export failed:\n{result.stderr}"

    fresh = result.stdout
    fresh_parsed = _parse_constraints(fresh)

    # Compare parsed sets — order and uv header/comments don't matter
    assert fresh_parsed == committed_parsed, (
        "constraints.txt is out of sync with uv.lock. "
        "Regenerate with: uv export --no-hashes --format requirements-txt --all-extras > constraints.txt"
    )


def test_fresh_install_constraint_set_equals_audited_set():
    """A fresh install with constraints.txt must resolve the same set as the licence gate audits.

    This uses pip's --dry-run --report to resolve the exact versions that would be
    installed under constraints.txt, and compares the resolved set against the
    audited set from uv.lock in both directions (equality, not just subset).
    """
    # The audited set from uv.lock (what the licence gate checks)
    audited = _install_set()
    audited_canonical = {check_install_licences.canonical(k): v for k, v in audited.items()}

    # Use pip's --dry-run --report to resolve what would be installed
    # We need a temporary venv for this
    import tempfile
    import json

    with tempfile.TemporaryDirectory() as tmpdir:
        venv_dir = Path(tmpdir) / "test_venv"
        subprocess.run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            check=True,
            capture_output=True,
        )
        pip_bin = venv_dir / "bin" / "pip"

        # First upgrade pip to support --report
        subprocess.run(
            [str(pip_bin), "install", "--quiet", "--upgrade", "pip"],
            check=True,
            capture_output=True,
        )

        # Create a clean constraints file for pip (strip uv's "Resolved N packages" line and -e .)
        constraints_text = CONSTRAINTS_FILE.read_text(encoding="utf-8")
        clean_constraints = "\n".join(
            line for line in constraints_text.splitlines()
            if not line.strip().startswith("Resolved ") and not line.strip().startswith("-e ")
        )
        clean_constraints_file = Path(tmpdir) / "clean_constraints.txt"
        clean_constraints_file.write_text(clean_constraints, encoding="utf-8")

        # Run dry-run with constraints and capture report
        report_file = Path(tmpdir) / "report.json"
        result = subprocess.run(
            [
                str(pip_bin),
                "install",
                "--dry-run",
                "--report", str(report_file),
                "--quiet",
                "-c", str(clean_constraints_file),
                "-e", ".",
            ],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        assert result.returncode == 0, f"pip dry-run failed: {result.stderr}"

# Parse the JSON report
        report = json.loads(report_file.read_text(encoding="utf-8"))
        # pip 26+ uses "install" key (not "installs")
        installs = report.get("install", report.get("installs", []))
        # Debug: print report structure if installs is empty
        if not installs:
            print(f"DEBUG: Report keys: {list(report.keys())}", file=sys.stderr)
            print(f"DEBUG: Full report: {json.dumps(report, indent=2)}", file=sys.stderr)
        resolved = {}
        for install in installs:
            name = check_install_licences.canonical(install["metadata"]["name"])
            version = install["metadata"]["version"]
            resolved[name] = version

    # Parse constraints for platform marker filtering
    constraints_text = CONSTRAINTS_FILE.read_text(encoding="utf-8")
    committed_parsed = _parse_constraints(constraints_text)

    # The resolved set must equal the audited set (both directions)
    # Filter: remove the project itself (tinyagentos) from resolved
    resolved.pop("tinyagentos", None)
    # Filter audited set: exclude packages with platform markers that don't match current platform
    # Use the parsed constraints which include markers
    expected = {}
    for name, version in audited_canonical.items():
        if name in committed_parsed:
            _, marker = committed_parsed[name]
            if marker:
                try:
                    from packaging.markers import Marker
                    if not Marker(marker).evaluate():
                        continue  # Skip packages not for this platform
                except Exception:
                    pass  # If marker eval fails, include the package
        expected[name] = version

    assert resolved == expected, (
        f"Resolved set != audited set (platform-filtered). "
        f"In resolved not expected: {set(resolved) - set(expected)}. "
        f"In expected not resolved: {set(expected) - set(resolved)}. "
        f"Version mismatches: { {k: (resolved[k], expected[k]) for k in set(resolved) & set(expected) if resolved[k] != expected[k]} }"
    )