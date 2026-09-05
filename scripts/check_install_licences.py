#!/usr/bin/env python3
"""Licence gate for the set of packages a real server install actually lands.

Two problems this exists to catch, both of them install-time properties that no
unit test over ``tinyagentos/`` can see:

1. **A non-redistributable package in the shipped venv.** A licence governs
   *distribution*, not import, so "nothing imports it" is not a defence: a
   commercial taOS licensee redistributing the venv redistributes whatever is in
   it. ``litellm-enterprise`` (``LicenseRef-Proprietary``, BerriAI) arrived this
   way as a plain member of litellm's ``proxy`` extra.

2. **Install-vs-lock drift.** ``scripts/install-server.sh`` installs with pip,
   not ``uv sync``, and used to ``pip install`` packages that appear nowhere in
   ``pyproject.toml``. The dependency set on a production Pi was therefore not
   the set ``uv.lock`` describes, so an upstream relicence could reach users
   without tripping the lockfile. Any licence scan has to be pointed at what the
   installer actually installs, and the installer must not install anything the
   lockfile has never seen.

Offline by default for the graph checks; ``--licences`` adds the PyPI lookup.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# The extras a real server install asks for (scripts/install-server.sh).
SERVER_EXTRAS = ("proxy",)

# Licence expressions/classifiers that must never reach the shipped venv.
# ``LicenseRef-`` is SPDX's escape hatch for a licence with no SPDX id, which in
# practice means a bespoke proprietary grant we cannot pass on.
BLOCKING_PATTERNS = (
    "licenseref-",
    "proprietary",
    "agpl",
    "sspl",
    "business source",
    "busl",
    "commons clause",
)

# First-party packages: same licensor as taOS, so the terms that would block a
# third-party dependency are ours to grant. taosmd ships MIT + Commons Clause
# and is published by jaylfc, the same licensor as taOS itself.
FIRST_PARTY = {"taosmd", "tinyagentos"}

# pip flags and non-package arguments to skip when reading the installer.
_PIP_FLAG = re.compile(r"^-")
# Everything after one of these on the line belongs to the shell, not to pip.
_SHELL_OPERATORS = {"||", "&&", ";", "|", ">", ">>", "2>", "2>&1", "&"}


def load_lock(lock_path: Path | None = None) -> dict:
    lock_path = lock_path or REPO_ROOT / "uv.lock"
    with open(lock_path, "rb") as fh:
        return tomllib.load(fh)


def resolve_install_set(
    extras: tuple[str, ...] = SERVER_EXTRAS,
    lock: dict | None = None,
    root: str = "tinyagentos",
) -> dict[str, str]:
    """Walk uv.lock from ``root`` and return ``{package_name: version}``.

    Follows plain dependencies plus, for each edge that names extras, that
    dependency's own optional-dependency lists — which is exactly how
    ``litellm[proxy]`` drags ``litellm-enterprise`` in.
    """
    lock = lock or load_lock()
    packages = {p["name"]: p for p in lock.get("package", [])}

    resolved: dict[str, str] = {}
    # Queue entries are (package name, extras requested of that package).
    queue: list[tuple[str, tuple[str, ...]]] = [(root, extras)]
    seen: set[tuple[str, tuple[str, ...]]] = set()

    while queue:
        name, wanted = queue.pop()
        key = (name, tuple(sorted(wanted)))
        if key in seen:
            continue
        seen.add(key)
        pkg = packages.get(name)
        if pkg is None:
            continue
        if name != root:
            resolved[name] = pkg.get("version", "?")

        edges = list(pkg.get("dependencies", []))
        optional = pkg.get("optional-dependencies", {}) or {}
        for extra in wanted:
            edges.extend(optional.get(extra, []))
        for edge in edges:
            queue.append((edge["name"], tuple(edge.get("extra", []))))

    return resolved


def _pyproject_declared(pyproject_path: Path | None = None) -> set[str]:
    """Every distribution name declared anywhere in pyproject.toml."""
    pyproject_path = pyproject_path or REPO_ROOT / "pyproject.toml"
    with open(pyproject_path, "rb") as fh:
        doc = tomllib.load(fh)
    project = doc.get("project", {})
    reqs: list[str] = list(project.get("dependencies", []))
    for entries in (project.get("optional-dependencies", {}) or {}).values():
        reqs.extend(entries)
    for entries in (doc.get("dependency-groups", {}) or {}).values():
        reqs.extend(e for e in entries if isinstance(e, str))
    names = set()
    for req in reqs:
        name = re.split(r"[<>=!~\[;\s]", req.strip(), maxsplit=1)[0]
        if name:
            names.add(canonical(name))
    return names


def undeclared_pip_installs(installer_path: Path | None = None) -> list[str]:
    """Packages the installer pip-installs that pyproject.toml never declares.

    Returns canonical names. A non-empty result means the venv on a production
    box holds something the lockfile has never resolved or audited.
    """
    installer_path = installer_path or REPO_ROOT / "scripts" / "install-server.sh"
    declared = _pyproject_declared()
    findings: list[str] = []
    for line in installer_path.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        match = re.search(r"pip(?:3)?\s+install\s+(.*)$", stripped)
        if not match:
            continue
        for token in match.group(1).split():
            # The rest of the shell line (`|| log "..."`, `&& …`, `# …`) is not
            # an argument to pip — stop reading at the first operator.
            if token in _SHELL_OPERATORS or token.startswith("#"):
                break
            token = token.strip("\"'")
            if not token or _PIP_FLAG.match(token):
                continue
            # Editable/local installs and requirement files are the lockfile
            # path, not an ad-hoc addition.
            if token.startswith((".", "/", "$")) or token.endswith((".txt", ".whl")):
                continue
            name = canonical(re.split(r"[<>=!~\[;]", token, maxsplit=1)[0])
            # `pip install --upgrade pip` bootstraps the installer itself.
            if not name or name == "pip":
                continue
            if name not in declared and name not in findings:
                findings.append(name)
    return findings


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def pypi_licence(name: str, version: str) -> str:
    """Best-effort licence string for a release: expression, then classifiers."""
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            info = json.load(resp).get("info", {})
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        # Never narrate a degraded run as a clean one.
        raise SystemExit(f"ERROR  could not read licence for {name} {version}: {exc}")
    expression = info.get("license_expression") or ""
    if expression:
        return expression
    licence = (info.get("license") or "").strip()
    classifiers = [c for c in info.get("classifiers", []) if c.startswith("License ::")]
    return licence or "; ".join(classifiers) or "UNKNOWN"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--install-set", action="store_true",
        help="print the resolved install set and run the offline gates",
    )
    parser.add_argument(
        "--licences", action="store_true",
        help="also query PyPI for each package's licence (needs network)",
    )
    parser.add_argument(
        "--extras", default=",".join(SERVER_EXTRAS),
        help="comma-separated extras the install asks for (default: proxy)",
    )
    args = parser.parse_args(argv)

    extras = tuple(e for e in args.extras.split(",") if e)
    install_set = resolve_install_set(extras)
    failures = 0

    if args.install_set:
        for name in sorted(install_set):
            print(f"  {name} {install_set[name]}")
        print(f"  ({len(install_set)} packages from extras {list(extras)})")

    if args.licences:
        for name in sorted(install_set):
            if canonical(name) in FIRST_PARTY:
                continue
            licence = pypi_licence(name, install_set[name])
            low = licence.lower()
            if any(pattern in low for pattern in BLOCKING_PATTERNS):
                # Some projects paste the whole licence text into the field;
                # one line is enough to name the offender.
                summary = licence.strip().splitlines()[0][:120]
                print(f"BLOCKER {name} {install_set[name]}  {summary}")
                failures += 1

    for name in undeclared_pip_installs():
        print(f"DRIFT   {name} installed by install-server.sh but absent from pyproject.toml")
        failures += 1

    if failures:
        print(f"exit 1 ({failures} finding(s))")
        return 1
    print("ok: install set carries no blocked licence and no undeclared package")
    return 0


if __name__ == "__main__":
    sys.exit(main())
