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
