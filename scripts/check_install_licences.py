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

# The extras a real server install asks for (scripts/install-server.sh). None
# since the LiteLLM ``proxy`` extra was removed (a handset adds ``ble``).
SERVER_EXTRAS: tuple[str, ...] = ()

# Matches "Commons Clause" and "Commons-Clause" (SPDX-adjacent text spells it
# both ways, e.g. "MIT-0 WITH Commons-Clause"), case-insensitively.
COMMONS_CLAUSE_RE = re.compile(r"\bcommons[\s-]?clause\b", re.IGNORECASE)

# First-party packages: same licensor as taOS, so the terms that would block a
# third-party dependency are ours to grant. taosmd ships MIT + Commons Clause
# and is published by jaylfc, the same licensor as taOS itself.
FIRST_PARTY = {"taosmd", "tinyagentos"}

# pip flags and non-package arguments to skip when reading the installer.
_PIP_FLAG = re.compile(r"^-")
# Everything after one of these on the line belongs to the shell, not to pip.
_SHELL_OPERATORS = {"||", "&&", ";", "|", ">", ">>", "2>", "2>&1", "&"}

# pip install options that take a separate value after them (skip the option and its value)
_PIP_VALUE_OPTIONS = frozenset([
    "-c", "--constraint", "-r", "--requirement", "-e", "--editable",
    "-i", "--index-url", "--extra-index-url", "-f", "--find-links",
    "--trusted-host", "--retries", "--timeout", "-t", "--target",
    "--prefix", "--root", "--src", "--cache-dir", "--progress-bar",
    "--python-version", "--platform", "--implementation", "--abi",
    "--no-binary", "--only-binary", "--log", "--upgrade-strategy",
])


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
        tokens = match.group(1).split()
        i = 0
        while i < len(tokens):
            token = tokens[i]
            # The rest of the shell line (`|| log "..."`, `&& …`, `# …`) is not
            # an argument to pip — stop reading at the first operator.
            if token in _SHELL_OPERATORS or token.startswith("#"):
                break
            token = token.strip("\"'")
            if not token:
                i += 1
                continue
            # Skip flags first, including value options
            if _PIP_FLAG.match(token):
                if token in _PIP_VALUE_OPTIONS:
                    nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
                    if nxt and not nxt.startswith("-"):
                        i += 2
                    else:
                        i += 1
                    continue
                i += 1
                continue
            # Editable/local installs and requirement files are the lockfile
            # path, not an ad-hoc addition.
            if token.startswith((".", "/", "$")) or token.endswith((".txt", ".whl")):
                i += 1
                continue
            name = canonical(re.split(r"[<>=!~\[;]", token, maxsplit=1)[0])
            # `pip install --upgrade pip` bootstraps the installer itself.
            if not name or name == "pip":
                i += 1
                continue
            if name not in declared and name not in findings:
                findings.append(name)
            i += 1
    return findings


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def licence_from_info(info: dict) -> str:
    """Best-effort licence string from a PyPI release ``info`` dict.

    Prefers the SPDX ``license_expression``, then the free-text ``license``
    field, then any ``License ::`` classifier. Returns ``"UNKNOWN"`` when none
    of those carry anything readable — an unreadable licence is not evidence
    the package is safe to redistribute, so callers must treat it as a finding
    of its own rather than as a silent pass.
    """
    expression = info.get("license_expression") or ""
    if expression:
        return expression
    licence = (info.get("license") or "").strip()
    classifiers = [c for c in info.get("classifiers", []) if c.startswith("License ::")]
    return licence or "; ".join(classifiers) or "UNKNOWN"


def pypi_licence(name: str, version: str) -> str:
    """Best-effort licence string for a release: expression, then classifiers."""
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            info = json.load(resp).get("info", {})
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        # Never narrate a degraded run as a clean one.
        raise SystemExit(f"ERROR  could not read licence for {name} {version}: {exc}")
    return licence_from_info(info)


# ---------------------------------------------------------------------------
# Licence classification
#
# taOS ships under AGPL-3.0-or-later only (no commercial licence), so AGPL and
# GPL-3.0 dependencies are fine. What cannot be shipped: GPL-2.0-only,
# proprietary/LicenseRef, non-commercial or no-derivatives terms, SSPL, BSL,
# Elastic, Commons Clause. A string we cannot recognise is a finding of its
# own ("unknown-licence"); it never passes silently.
# ---------------------------------------------------------------------------

OK, BLOCKED, UNKNOWN = "ok", "blocked", "unknown-licence"

# Checked first, on the normalised text. Deliberately substring-style: a false
# positive costs a minute of triage, a false negative ships a blocker.
_BLOCK_RES = [re.compile(p) for p in (
    r"licenseref", r"proprietary", r"non ?commercial", r"\bnc\b", r"\bnd\b",
    r"no ?derivs?", r"\bsspl", r"server side public", r"business source",
    r"\bbusl\b", r"\bbsl 1\.1", r"elastic", r"\belv2\b", r"commons ?clause",
    r"all rights reserved", r"\bcommercial\b", r"\brestricted\b",
    r"\bunlicensed\b", r"not (?:free|open)",
)]

_ALLOWED = frozenset({
    "mit", "mit 0", "mit no attribution", "expat", "x11",
    "apache", "apache 2.0", "apache 2", "apache2", "asl 2.0",
    "bsd", "new bsd", "simplified bsd", "revised bsd", "bsd 2 clause",
    "bsd 3 clause", "bsd 2", "bsd 3", "0bsd", "isc", "iscl",
    "psf", "psf 2.0", "python", "python 2.0", "python foundation",
    "mpl 2.0", "mozilla public 2.0", "mozilla public 2.0 mpl 2.0",
    "unlicense", "cc0 1.0", "cc0 1.0 universal public domain dedication",
    "cc0 1.0 universal", "public domain", "zlib", "zlib libpng", "libpng",
    "bsl 1.0", "boost 1.0", "boost", "artistic 1.0", "artistic 2.0",
    "artistic", "hpnd", "wtfpl", "postgresql", "zpl 2.1", "unicode 3.0",
    "cc by 4.0", "cc by 3.0", "ofl 1.1",
})

_GNU = [
    (re.compile(r"gnu affero general public"), "agpl"),
    (re.compile(r"gnu (?:library or )?lesser general public"), "lgpl"),
    (re.compile(r"gnu library general public"), "lgpl"),
    (re.compile(r"gnu general public"), "gpl"),
]
_COPYLEFT_RE = re.compile(r"^(agpl|lgpl|gpl) ?(\d(?:\.\d)?)?( only| or later)?$")

# SPDX exceptions that only add permissions.
_ALLOWED_EXCEPTIONS = frozenset({
    "classpath exception 2.0", "gcc exception 3.1", "gcc exception 2.0",
    "llvm exception", "font exception 2.0", "autoconf exception 2.0",
    "autoconf exception 3.0", "bison exception 2.2", "linux syscall note",
    "lgpl 3.0 linking exception", "openssl exception", "universal foss exception 1.0",
})


def _norm(text: str) -> str:
    t = text.strip().lower().replace("licence", "license")
    if "::" in t:
        t = t.split("::")[-1]
    outside = re.sub(r"\([^)]*\)", " ", t)
    if outside.strip():
        t = outside
    for pat, repl in _GNU:
        t = pat.sub(repl, t)
    t = re.sub(r"(agpl|lgpl|gpl)v(\d)", r"\1 \2", t)
    t = re.sub(r"\bv(?=\d)", "", t)
    t = re.sub(r"(\d)\+", r"\1 or later", t)
    t = re.sub(r"(agpl|lgpl|gpl)\+", r"\1 or later", t)
    t = re.sub(r"[^a-z0-9.]+", " ", t)
    t = re.sub(r"\b(license|the|software|version|public domain dedication)\b", " ", t) \
        if t.strip() not in {"public domain"} else t
    return " ".join(t.split())


def _classify_atom(text: str) -> str:
    raw = text.strip()
    if not raw or raw.upper() == "UNKNOWN":
        return UNKNOWN
    t = _norm(raw)
    if not t:
        return UNKNOWN
    if any(r.search(t) for r in _BLOCK_RES) or COMMONS_CLAUSE_RE.search(raw):
        return BLOCKED
    if t in _ALLOWED:
        return OK
    m = _COPYLEFT_RE.match(t)
    if m:
        family, ver, suffix = m.group(1), m.group(2), (m.group(3) or "").strip()
        if family == "agpl" and ver in ("3", "3.0"):
            return OK
        if family == "gpl" and ver in ("3", "3.0"):
            return OK
        if family == "gpl" and ver in ("2", "2.0"):
            return OK if suffix == "or later" else BLOCKED
        if family == "lgpl" and ver in (None, "2", "2.0", "2.1", "3", "3.0"):
            return OK
        return UNKNOWN
    # Free text such as "MIT or Apache License 2.0": every piece must be known.
    parts = re.split(r"\s+(or|and)\s+", raw, flags=re.IGNORECASE)
    if len(parts) >= 3:
        ops = {p.lower() for p in parts[1::2]}
        results = [_classify_atom(p) for p in parts[0::2]]
        if "or" in ops and "and" not in ops:
            return _any_of(results)
        if "and" in ops and "or" not in ops:
            return _all_of(results)
    return UNKNOWN


def _all_of(results: list[str]) -> str:
    if BLOCKED in results:
        return BLOCKED
    return UNKNOWN if UNKNOWN in results else OK


def _any_of(results: list[str]) -> str:
    if OK in results:
        return OK
    return BLOCKED if all(r == BLOCKED for r in results) else UNKNOWN


_SPDX_TOKEN = re.compile(r"\(|\)|[A-Za-z0-9.+:-]+")


def _spdx_tokens(text: str) -> list[str] | None:
    """Tokens when ``text`` is strictly SPDX-shaped, else None (free text)."""
    tokens = _SPDX_TOKEN.findall(text)
    if not tokens or "".join(tokens) != re.sub(r"\s+", "", text):
        return None
    return tokens


def _eval_spdx(tokens: list[str]) -> str | None:
    """Evaluate an SPDX expression; None when it is not a well-formed one."""
    pos = 0

    def peek():
        return tokens[pos] if pos < len(tokens) else None

    def expr():
        nonlocal pos
        res = [term()]
        while peek() == "OR":
            pos += 1
            res.append(term())
        return _any_of(res) if len(res) > 1 else res[0]

    def term():
        nonlocal pos
        res = [factor()]
        while peek() == "AND":
            pos += 1
            res.append(factor())
        return _all_of(res) if len(res) > 1 else res[0]

    def factor():
        nonlocal pos
        tok = peek()
        if tok is None:
            raise ValueError("unexpected end")
        if tok == "(":
            pos += 1
            res = expr()
            if peek() != ")":
                raise ValueError("unbalanced")
            pos += 1
            return res
        if tok in (")", "AND", "OR", "WITH"):
            raise ValueError("unexpected operator")
        pos += 1
        res = _classify_atom(tok)
        if peek() == "WITH":
            pos += 1
            exc = peek()
            if exc is None or exc in ("(", ")", "AND", "OR", "WITH"):
                raise ValueError("bad exception")
            pos += 1
            if COMMONS_CLAUSE_RE.search(exc):
                exc_res = BLOCKED
            elif _norm(exc) in _ALLOWED_EXCEPTIONS:
                exc_res = OK
            else:
                exc_res = UNKNOWN
            res = _all_of([res, exc_res])
        return res

    try:
        result = expr()
    except ValueError:
        return None
    return result if pos == len(tokens) else None


def classify_licence(licence: str) -> str | None:
    """Return ``None`` (ok), ``"blocked"`` or ``"unknown-licence"``.

    Understands real PyPI ``License ::`` classifier strings (several joined by
    ``"; "`` all have to be acceptable), SPDX ids, and SPDX expressions with
    AND / OR / WITH and parentheses: any non-allowed operand of an AND blocks,
    an OR passes when one side is allowed. Anything unrecognised is
    ``"unknown-licence"``: the gate fails on it, it is never skipped.
    """
    text = (licence or "").strip()
    if not text or text.upper() == "UNKNOWN":
        return UNKNOWN
    if "::" in text:
        results = [_classify_atom(p) for p in text.split(";") if p.strip()]
    else:
        tokens = _spdx_tokens(text)
        result = _eval_spdx(tokens) if tokens else None
        if result is None:
            # Not an SPDX expression: a free-text name or pasted licence text.
            result = _classify_atom(text.splitlines()[0] if len(text) < 200 else text)
        results = [result]
    verdict = _all_of(results)
    return None if verdict == OK else verdict


def main(argv: list[str] | None = None, lookup=None) -> int:
    lookup = lookup or pypi_licence
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
        "--lock", type=Path, default=None,
        help="uv.lock to read (default: the repo's uv.lock)",
    )
    parser.add_argument(
        "--extras", default=",".join(SERVER_EXTRAS),
        help="comma-separated extras the install asks for (default: none)",
    )
    args = parser.parse_args(argv)

    extras = tuple(e for e in args.extras.split(",") if e)
    install_set = resolve_install_set(extras, lock=load_lock(args.lock))
    failures = 0

    if args.install_set:
        for name in sorted(install_set):
            print(f"  {name} {install_set[name]}")
        print(f"  ({len(install_set)} packages from extras {list(extras)})")

    if args.licences:
        for name in sorted(install_set):
            if canonical(name) in FIRST_PARTY:
                continue
            licence = lookup(name, install_set[name])
            finding = classify_licence(licence)
            if finding == "unknown-licence":
                print(
                    f"unknown-licence {name} {install_set[name]}  "
                    "PyPI gave no license_expression, license, or License classifier "
                    "-- verify by hand before shipping"
                )
                failures += 1
            elif finding == "blocked":
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
