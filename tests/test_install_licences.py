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
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

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
        "LicenseRef-Proprietary (BerriAI). Redistributing the taOS venv under "
        "the commercial licence would redistribute proprietary code."
    ),
}


def _install_set() -> dict[str, str]:
    return check_install_licences.resolve_install_set()


@pytest.mark.parametrize("forbidden,reason", sorted(FORBIDDEN.items()))
def test_server_install_set_excludes_forbidden_package(forbidden, reason):
    """`pip install -e .[proxy]` must not land a non-redistributable package."""
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


def test_proxy_extra_pins_litellm_to_the_minor_it_mirrors():
    """The inlined proxy subset mirrors one litellm minor, so cap litellm to it.

    ``pip install -e .[proxy]`` resolves fresh — it does not read uv.lock — so an
    uncapped ``litellm>=…`` lets the installer pull a newer minor whose proxy
    extra has grown requirements this list does not carry. That is not
    hypothetical: litellm 1.99.0 added ``hiredis`` and made ``expression`` an
    eager import, and a venv built from the 1.94 subset dies at startup with
    ``ModuleNotFoundError: No module named 'expression'``.
    """
    with open(REPO_ROOT / "pyproject.toml", "rb") as fh:
        doc = tomllib.load(fh)
    proxy = doc["project"]["optional-dependencies"]["proxy"]
    litellm_req = next(
        (r for r in proxy if check_install_licences.canonical(re.split(r"[<>=!~\[;\s]", r.strip(), maxsplit=1)[0]) == "litellm"),
        None,
    )
    assert litellm_req is not None, "the proxy extra must depend on litellm"
    assert "<" in litellm_req, (
        "litellm must carry an upper bound so the installer's fresh pip resolve "
        f"cannot outrun the inlined proxy subset (got {litellm_req!r})"
    )
    assert "[proxy]" not in litellm_req, (
        "litellm[proxy] pulls litellm-enterprise; the subset is inlined below it "
        f"instead (got {litellm_req!r})"
    )
    assert check_install_licences.litellm_cap_pins_mirrored_minor(litellm_req), (
        "litellm's specifier must pin exactly the mirrored minor (>=1.94.2,<1.95), "
        f"not merely carry *some* upper bound (got {litellm_req!r})"
    )


def _parse_snapshot(snapshot_path: Path) -> dict[str, tuple[Version, bool] | None]:
    """Return {canonical_name: (version, inclusive)} from the snapshot.

    ``inclusive`` is ``True`` for ``<=`` / ``==`` and ``False`` for ``<``.
    ``==X`` is treated as ``<=X`` for comparison purposes.
    """
    result: dict[str, tuple[Version, bool] | None] = {}
    for line in snapshot_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        req = Requirement(line)
        canonical_name = check_install_licences.canonical(req.name)
        upper_specs = [s for s in req.specifier if s.operator in ("<", "<=", "==")]
        if not upper_specs:
            result[canonical_name] = None
            continue
        exact = [s for s in upper_specs if s.operator == "=="]
        if exact:
            result[canonical_name] = (Version(exact[0].version), True)
            continue
        min_version = min(Version(s.version) for s in upper_specs)
        min_specs = [s for s in upper_specs if Version(s.version) == min_version]
        has_exclusive_at_min = any(s.operator == "<" for s in min_specs)
        inclusive = not has_exclusive_at_min
        result[canonical_name] = (min_version, inclusive)
    return result


def _effective_upper_bound(req_str: str) -> tuple[Version, bool] | None:
    """Return (version, inclusive) effective upper bound, or None if unconstrained.

    ``inclusive`` is ``True`` for ``<=`` / ``==`` and ``False`` for ``<``.
    ``==X`` is treated as ``<=X`` for comparison purposes.
    """
    req = Requirement(req_str)
    upper_specs = [s for s in req.specifier if s.operator in ("<", "<=", "==")]
    if not upper_specs:
        return None
    exact = [s for s in upper_specs if s.operator == "=="]
    if exact:
        return (Version(exact[0].version), True)
    min_version = min(Version(s.version) for s in upper_specs)
    min_specs = [s for s in upper_specs if Version(s.version) == min_version]
    has_exclusive_at_min = any(s.operator == "<" for s in min_specs)
    inclusive = not has_exclusive_at_min
    return (min_version, inclusive)


def test_effective_upper_bound__lt_vs_lte_fails():
    """<15 ceiling must be rejected when <=15 is looser (inclusive True)."""
    py_bound = _effective_upper_bound("rich<=15")       # (15, True)
    snap_bound = _effective_upper_bound("rich<15")     # (15, False)
    # (15, True) > (15, False) is True -> py is less restrictive -> should fail
    assert py_bound > snap_bound, (
        f"Expected <='15' to be looser than <'15', got {py_bound} > {snap_bound}"
    )


def test_effective_upper_bound__lt_vs_eq_fails():
    """==15 ceiling must be rejected when compared to <15 (== treated as <=)."""
    py_bound = _effective_upper_bound("rich==15")       # (15, True)
    snap_bound = _effective_upper_bound("rich<15")     # (15, False)
    # (15, True) > (15, False) is True -> py is less restrictive -> should fail
    assert py_bound > snap_bound, (
        f"Expected =='15' to be looser than <'15', got {py_bound} > {snap_bound}"
    )


def test_effective_upper_bound__lt_same_passes():
    """<15 ceiling must pass when snapshot also has <15 (equal restrictiveness)."""
    py_bound = _effective_upper_bound("rich<15")       # (15, False)
    snap_bound = _effective_upper_bound("rich<15")    # (15, False)
    # (15, False) > (15, False) is False -> equal -> passes
    assert not (py_bound > snap_bound), (
        f"Expected <'15' to equal <'15', but {py_bound} > {snap_bound} was True"
    )


def test_proxy_extra_ceilings_do_not_outrun_the_mirrored_snapshot():
    """Ceilings of inlined proxy siblings must not exceed the mirrored snapshot.

    ``pip install -e ".[proxy]"`` resolves fresh — it does not read uv.lock — so
    every inlined sibling carries its own ceiling. Widening or removing that
    ceiling lets a fresh install pull a version litellm 1.94.2 was not released
    against (e.g. mcp 2.x, rich 15, websockets 17, gunicorn 26 from #3083).
    A CVE floor bump (cryptography>=49 -> >=50) is legitimate: the ceiling is
    unchanged. Re-mirroring onto a new litellm minor is a deliberate change that
    must update both the inlined list and this snapshot together.
    """
    snapshot_path = REPO_ROOT / "tests" / "data" / "litellm-proxy-extra-1.94.2.txt"
    snapshot = _parse_snapshot(snapshot_path)

    with open(REPO_ROOT / "pyproject.toml", "rb") as fh:
        doc = tomllib.load(fh)
    proxy = doc["project"]["optional-dependencies"]["proxy"]

    current: dict[str, str | None] = {}
    for req_str in proxy:
        req_str = req_str.strip()
        if not req_str or req_str.startswith("#"):
            continue
        req = Requirement(req_str)
        canonical_name = check_install_licences.canonical(req.name)
        if canonical_name == "litellm":
            continue
        current[canonical_name] = _effective_upper_bound(req_str)

    failures: list[str] = []

    # Every pyproject entry must exist in the snapshot (re-mirror if not).
    for name in current:
        if name not in snapshot:
            failures.append(
                f"{name} is in pyproject.toml's proxy extra but absent from the "
                f"mirrored snapshot ({snapshot_path.name}) — re-mirror from "
                f"litellm's proxy extra and update the snapshot"
            )

    # Every snapshot entry must still be present in pyproject with a ceiling
    # that has not been widened or removed.
    for name, snap_upper in snapshot.items():
        if name not in current:
            failures.append(
                f"{name} was in the mirrored snapshot but is now absent from "
                f"pyproject.toml's proxy extra — its ceiling has been removed"
            )
            continue
        py_upper = current[name]
        if snap_upper is None:
            # Snapshot has no ceiling; any ceiling (or none) in pyproject is fine.
            continue
        if py_upper is None:
            failures.append(
                f"{name} lost its upper bound (snapshot had "
                f"<{snap_upper[0]} inclusive={snap_upper[1]}, "
                f"pyproject.toml now has no ceiling)"
            )
        elif py_upper > snap_upper:
            failures.append(
                f"{name} ceiling widened from "
                f"<{snap_upper[0]} (incl={snap_upper[1]}) to "
                f"<{py_upper[0]} (incl={py_upper[1]}) "
                f"(snapshot pins the mirrored litellm 1.94.2 set)"
            )

    assert not failures, "\n".join(failures)


def test_litellm_cap_helper_rejects_a_ceiling_wider_than_the_mirrored_minor():
    """A loose ceiling like ``<2`` must not satisfy the cap check.

    ``"<" in litellm_req`` (the original assertion) is true for ``litellm>=1.94.2,<2``
    just as it is for the correct ``litellm>=1.94.2,<1.95`` — it cannot tell a real
    cap from a decoy one, so a fresh pip resolve could still outrun the inlined
    proxy subset while this test stayed green.
    """
    assert not check_install_licences.litellm_cap_pins_mirrored_minor(
        "litellm>=1.94.2,<2"
    ), "a <2 ceiling is far wider than the <1.95 the inlined subset mirrors"


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
