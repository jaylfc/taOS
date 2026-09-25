#!/usr/bin/env python3
"""Guard-discrimination gate: a guard test must FAIL against the defect it names.

The defect this catches: a test written to guard a security property that is
structurally incapable of failing on that property. It monkeypatches the unit
under test, asserts a tautology, or uses a fixture whose values never reach
the guarded comparison. CI stays green whether the product is right or wrong.

The check is mechanical, not a reading of test names or bodies. A guard test
DECLARES the product function it guards:

    @pytest.mark.guards(
        "tinyagentos.routes.a2a_gpu_lease:_resolve_actor",
        replace=[('identity="@operator"', "identity=holder")],
    )
    async def test_release_does_not_free_another_holders_lease(...): ...

The gate then runs that test, in one pytest process:

1. against the real code (baseline). It must pass, and the gate records
   whether the guarded function was executed at all.
2. against deliberately broken variants of the guarded function. Each variant
   is compiled from the function's own source and swapped in through
   ``func.__code__``, so every reference to the function (routes registered by
   a decorator, ``from x import f`` aliases) runs the broken code. A test that
   replaced the function itself (monkeypatch) keeps running its stand-in, and
   the broken variant cannot reach its assertions, which is the point.

Which variants must be caught:

* ``replace=[(old, new), ...]``: the author spells the defect as a source
  edit inside the guarded function (``old`` must occur exactly once). Every
  such variant must make the test fail.
* ``must_kill=["negate-if@123", ...]``: named generated mutants (list them
  with ``--list-mutants``) that must make the test fail.
* neither: the test must fail against at least ONE generated mutant
  (constant returns, negated conditions, flipped comparisons, swapped and/or,
  dropped raises). A test no mutant can fail is not looking at the function.

A test that survives a required variant is reported NON-DISCRIMINATING and
the gate exits 1. A guard the gate could not check (baseline fails, target
not importable, unknown mutant id, unsupported function) is an ERROR and the
gate exits 2: an unchecked guard is not a passing guard.

Ad-hoc mode checks a test without editing it (for another repo, or a branch
state that predates the marker)::

    check_non_discriminating.py --root ../other --guard 'tests/t.py::test_x' \\
        --target pkg.mod:func --replace 'a == b' 'True'

Runtime: one pytest process per test FILE holding declared guards; each guard
costs one baseline run plus one run per required variant (the default mode
stops at the first variant that kills). Stated per run in the summary line.

Scope: only tests that declare ``guards`` are executed. Refusal-named tests
(``_cannot_``, ``_does_not_``, ...) without a declaration are counted, and
listed with ``--undeclared``, but not checked, since the gate will not guess
which function a test guards. A carded route sweep where a route has no test
at all is a coverage question this gate does not answer.
"""
from __future__ import annotations

import argparse
import ast
import importlib
import inspect
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import time
import types
import warnings
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
PLUGIN_NAME = Path(__file__).stem
SPEC_ENV = "NONDISCRIM_SPEC"
MARKER = "guards"

REFUSAL_NAME = re.compile(
    r"_(cannot|does_not|must_not|rejects|denies|returns_40[13])(_|$)"
)

_FLIP_CMP = {
    ast.Eq: ast.NotEq, ast.NotEq: ast.Eq,
    ast.In: ast.NotIn, ast.NotIn: ast.In,
    ast.Is: ast.IsNot, ast.IsNot: ast.Is,
    ast.Lt: ast.GtE, ast.GtE: ast.Lt,
    ast.Gt: ast.LtE, ast.LtE: ast.Gt,
}

# Body-level mutants first: they are the ones most likely to kill, and the
# default (no named variant) mode stops at the first kill.
_BODY_MUTANTS = {"return-true": True, "return-false": False, "return-none": None}


class GuardError(Exception):
    """The guard could not be checked. Reported as ERROR, never as a pass."""


# ── resolving and mutating the guarded function ──────────────────────────────

def resolve_target(target: str) -> types.FunctionType:
    """``pkg.mod:Qual.name`` -> the innermost plain function object."""
    mod_name, sep, qual = target.partition(":")
    if not sep or not mod_name or not qual:
        raise GuardError(f"target {target!r} must be 'module:qualname'")
    try:
        obj = importlib.import_module(mod_name)
    except Exception as exc:  # reported as ERROR, not swallowed
        raise GuardError(f"cannot import {mod_name!r}: {exc!r}") from exc
    for part in qual.split("."):
        holder = obj
        try:
            obj = inspect.getattr_static(holder, part)
        except AttributeError as exc:
            raise GuardError(f"{target!r}: no attribute {part!r}") from exc
        if isinstance(obj, (staticmethod, classmethod)):
            obj = obj.__func__
    obj = inspect.unwrap(obj)
    if not isinstance(obj, types.FunctionType):
        raise GuardError(f"{target!r} is not a Python function ({type(obj).__name__})")
    return obj


def _function_source(func: types.FunctionType) -> tuple[str, int, str]:
    """(dedented source, first line number, filename) of *func*."""
    try:
        lines, start = inspect.getsourcelines(func)
    except (OSError, TypeError) as exc:
        raise GuardError(f"no source for {func.__qualname__}: {exc!r}") from exc
    return textwrap.dedent("".join(lines)), start, inspect.getsourcefile(func) or "<unknown>"


def _parse_function(src: str, start: int, name: str) -> ast.AST:
    tree = ast.parse(src)
    ast.increment_lineno(tree, start - 1)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise GuardError(f"could not find def {name} in its own source")


def _own_scope(node: ast.AST):
    """Pre-order walk of *node*'s own scope (not nested defs, lambdas, classes)."""
    for child in ast.iter_child_nodes(node):
        yield child
        if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            yield from _own_scope(child)


def _is_generator(fn: ast.AST) -> bool:
    return any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in _own_scope(fn))


def _site_kind(node: ast.AST) -> str | None:
    if isinstance(node, (ast.If, ast.IfExp)):
        return "negate-if"
    if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in _FLIP_CMP:
        return "flip-cmp"
    if isinstance(node, ast.BoolOp):
        return "swap-boolop"
    if isinstance(node, ast.Raise):
        return "drop-raise"
    return None


def _site_ids(fn: ast.AST) -> list[tuple[str, ast.AST]]:
    """Stable ids for every mutable site in *fn*, in source order."""
    sites: list[tuple[str, ast.AST]] = []
    seen: dict[str, int] = {}
    for node in _own_scope(fn):
        kind = _site_kind(node)
        if kind is None:
            continue
        base = f"{kind}@{node.lineno}"
        seen[base] = seen.get(base, 0) + 1
        sites.append((base if seen[base] == 1 else f"{base}.{seen[base]}", node))
    return sites


def list_mutants(func: types.FunctionType) -> list[str]:
    src, start, _ = _function_source(func)
    fn = _parse_function(src, start, func.__name__)
    ids = [] if _is_generator(fn) else list(_BODY_MUTANTS)
    return ids + [sid for sid, _ in _site_ids(fn)]


def _mutate_site(node: ast.AST) -> ast.AST:
    """Return the replacement for *node* (mutating it in place where possible)."""
    if isinstance(node, (ast.If, ast.IfExp)):
        node.test = ast.UnaryOp(op=ast.Not(), operand=node.test)
        return node
    if isinstance(node, ast.Compare):
        node.ops = [_FLIP_CMP[type(node.ops[0])]()]
        return node
    if isinstance(node, ast.BoolOp):
        node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
        return node
    if isinstance(node, ast.Raise):
        return ast.Pass()
    raise GuardError(f"cannot mutate {type(node).__name__}")


class _Replace(ast.NodeTransformer):
    def __init__(self, target: ast.AST):
        self.target = target

    def generic_visit(self, node):
        if node is self.target:
            return _mutate_site(node)
        return super().generic_visit(node)


def _mutant_ast(func: types.FunctionType, mutant: str | tuple[str, str]) -> ast.AST:
    src, start, _ = _function_source(func)
    if isinstance(mutant, tuple):
        old, new = mutant
        count = src.count(old)
        if count != 1:
            raise GuardError(
                f"replace {old!r}: found {count} times in {func.__qualname__}, need exactly 1"
            )
        src = src.replace(old, new)
        try:
            return _parse_function(src, start, func.__name__)
        except SyntaxError as exc:
            raise GuardError(f"replace {old!r} -> {new!r} is not valid Python: {exc}") from exc
    fn = _parse_function(src, start, func.__name__)
    if mutant in _BODY_MUTANTS:
        if _is_generator(fn):
            raise GuardError(f"{mutant} does not apply to a generator")
        fn.body = [ast.Return(value=ast.Constant(value=_BODY_MUTANTS[mutant]))]
        return fn
    for sid, node in _site_ids(fn):
        if sid == mutant:
            _Replace(node).visit(fn)
            return fn
    raise GuardError(
        f"unknown mutant {mutant!r} for {func.__qualname__}; "
        f"available: {', '.join(list_mutants(func))}"
    )


_FUTURE_FLAGS = 0
for _feat in ("annotations", "division", "generator_stop"):
    import __future__ as _fut
    _FUTURE_FLAGS |= getattr(getattr(_fut, _feat, None), "compiler_flag", 0)


def compile_mutant(func: types.FunctionType, mutant: str | tuple[str, str]) -> types.CodeType:
    """Code object for *func* with *mutant* applied, closure-compatible."""
    fn = _mutant_ast(func, mutant)
    fn.decorator_list = []
    ast.fix_missing_locations(fn)
    freevars = func.__code__.co_freevars
    filename = func.__code__.co_filename
    if freevars:
        # Nest the def in an outer function that binds the same names, so the
        # compiled code closes over them exactly as the original does.
        outer = ast.parse(
            "def __nondiscrim_outer__():\n"
            + "".join(f"    {v} = None\n" for v in freevars)
            + "    pass\n"
        ).body[0]
        outer.body[-1] = fn
        module = ast.Module(body=[outer], type_ignores=[])
    else:
        module = ast.Module(body=[fn], type_ignores=[])
    ast.fix_missing_locations(module)
    flags = func.__code__.co_flags & _FUTURE_FLAGS
    code = compile(module, filename, "exec", flags=flags, dont_inherit=True)

    def _find(co: types.CodeType, name: str) -> types.CodeType | None:
        for const in co.co_consts:
            if isinstance(const, types.CodeType) and const.co_name == name:
                return const
        return None

    if freevars:
        code = _find(code, "__nondiscrim_outer__")
    new = _find(code, func.__name__) if code is not None else None
    if new is None:
        raise GuardError(f"compiled mutant of {func.__qualname__} has no code object")
    if new.co_freevars != freevars:
        raise GuardError(
            f"{func.__qualname__}: closure {freevars} cannot be reproduced "
            f"(mutant has {new.co_freevars})"
        )
    return new


# ── pytest plugin half (runs inside the child pytest process) ────────────────

def _load_spec() -> dict | None:
    raw = os.environ.get(SPEC_ENV)
    return json.loads(raw) if raw else None


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        f"{MARKER}(target, replace=None, must_kill=None): the product function this "
        "guard test protects; scripts/check_non_discriminating.py fails the test "
        "suite when a broken variant of it does not make this test fail",
    )


def _guard_spec(item, spec: dict) -> dict | None:
    if spec.get("override"):
        return spec["override"]
    marker = item.get_closest_marker(MARKER)
    if marker is None:
        return None
    target = marker.args[0] if marker.args else marker.kwargs.get("target")
    return {
        "target": target,
        "replace": [list(pair) for pair in (marker.kwargs.get("replace") or [])],
        "must_kill": list(marker.kwargs.get("must_kill") or []),
    }


def _run_once(item) -> str:
    """Run *item* once from a clean fixture state: passed / failed / skipped."""
    from _pytest.runner import runtestprotocol

    item._initrequest()
    reports = runtestprotocol(item, nextitem=None, log=False)
    if any(r.failed for r in reports):
        return "failed"
    if any(r.when == "call" and r.passed for r in reports):
        return "passed"
    return "skipped"


def _count_calls(item, code: types.CodeType) -> tuple[str, int]:
    """Baseline run that also counts calls into *code* (all threads)."""
    import threading

    hits = [0]

    def prof(frame, event, arg):
        if event == "call" and frame.f_code is code:
            hits[0] += 1

    old = sys.getprofile()
    sys.setprofile(prof)
    threading.setprofile(prof)
    try:
        outcome = _run_once(item)
    finally:
        sys.setprofile(old)
        threading.setprofile(None)
    return outcome, hits[0]


def _check_item(item, guard: dict) -> dict:
    result = {
        "nodeid": item.nodeid, "target": guard.get("target"), "verdict": "ERROR",
        "reason": "", "baseline": None, "calls": None, "mutants": {},
    }
    try:
        func = resolve_target(guard.get("target") or "")
        original = func.__code__
        required: list[tuple[str, str | tuple[str, str]]] = []
        for i, pair in enumerate(guard.get("replace") or [], 1):
            if len(pair) != 2:
                raise GuardError(f"replace entry {pair!r} must be (old, new)")
            required.append((f"replace#{i}", (pair[0], pair[1])))
        for mid in guard.get("must_kill") or []:
            required.append((mid, mid))
        # Compile every variant BEFORE running anything: a bad spec is an
        # ERROR up front, not a half-checked guard.
        if required:
            variants = [(mid, compile_mutant(func, m)) for mid, m in required]
        else:
            variants = []
            for mid in list_mutants(func):
                try:
                    variants.append((mid, compile_mutant(func, mid)))
                except GuardError:
                    continue
            if not variants:
                raise GuardError(f"{func.__qualname__} has no applicable mutants")
    except GuardError as exc:
        result["reason"] = str(exc)
        return result

    outcome, calls = _count_calls(item, original)
    result["baseline"], result["calls"] = outcome, calls
    if outcome != "passed":
        result["reason"] = f"baseline run {outcome}; a guard that does not pass cannot be checked"
        return result

    for mid, code in variants:
        func.__code__ = code
        try:
            outcome = _run_once(item)
        finally:
            func.__code__ = original
        result["mutants"][mid] = "killed" if outcome == "failed" else "survived"
        if not required and outcome == "failed":
            break  # default mode: one kill proves the test looks at the function

    survived = [m for m, v in result["mutants"].items() if v == "survived"]
    if required:
        bad = survived
        why = "survived the named defect"
    else:
        bad = survived if len(survived) == len(result["mutants"]) else []
        why = "no mutant of the guarded function makes it fail"
    if bad:
        result["verdict"] = "NON-DISCRIMINATING"
        detail = []
        for mid in bad:
            label = mid
            for rid, m in required:
                if rid == mid and isinstance(m, tuple):
                    label = f"{mid} ({m[0]!r} -> {m[1]!r})"
            detail.append(label)
        result["reason"] = f"{why}: {', '.join(detail)}"
        if calls == 0:
            result["reason"] += (
                "; the guarded function was never executed by this test "
                "(replaced by a stub, or not reached)"
            )
    else:
        result["verdict"] = "OK"
    return result


def pytest_runtest_protocol(item, nextitem):
    spec = _load_spec()
    if spec is None:
        return None
    guard = _guard_spec(item, spec)
    if guard is None:
        return None
    result = _check_item(item, guard)
    with open(spec["out"], "a", encoding="utf-8") as fh:
        fh.write(json.dumps(result) + "\n")
    return True


# ── driver half (the CLI) ────────────────────────────────────────────────────

def _is_guards_decorator(dec: ast.AST) -> bool:
    node = dec.func if isinstance(dec, ast.Call) else dec
    return (
        isinstance(node, ast.Attribute) and node.attr == MARKER
        and isinstance(node.value, ast.Attribute) and node.value.attr == "mark"
    )


def scan_tests(paths: list[Path]) -> tuple[dict[Path, set[str]], list[str]]:
    """({file: declared guard test names}, [undeclared refusal-named tests])."""
    declared: dict[Path, set[str]] = {}
    undeclared: list[str] = []
    files: list[Path] = []
    for p in paths:
        if p.is_file():
            files.append(p)
        elif p.is_dir():
            files.extend(sorted(p.rglob("test_*.py")))
    for f in files:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                tree = ast.parse(f.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_"):
                continue
            if any(_is_guards_decorator(d) for d in node.decorator_list):
                declared.setdefault(f, set()).add(node.name)
            elif REFUSAL_NAME.search(node.name):
                undeclared.append(f"{f}::{node.name}")
    return declared, undeclared


def _run_child(root: Path, args: list[str], override: dict | None, python: str,
               timeout: float) -> tuple[list[dict], str]:
    with tempfile.NamedTemporaryFile("r", suffix=".jsonl", delete=False) as out:
        out_path = out.name
    env = dict(os.environ)
    env[SPEC_ENV] = json.dumps({"override": override, "out": out_path})
    env["PYTHONPATH"] = os.pathsep.join(
        [str(SCRIPTS_DIR), str(root)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
    )
    cmd = [python, "-m", "pytest", "-p", PLUGIN_NAME, "-p", "no:cacheprovider",
           "-q", "--no-header", *args]
    try:
        proc = subprocess.run(cmd, cwd=root, env=env, capture_output=True, text=True, check=False,
                              timeout=timeout)
        log = proc.stdout + proc.stderr
    except subprocess.TimeoutExpired:
        log = f"child pytest exceeded {timeout:.0f}s"
    try:
        with open(out_path, encoding="utf-8") as fh:
            results = [json.loads(line) for line in fh if line.strip()]
    finally:
        os.unlink(out_path)
    return results, log


def _tail(log: str, n: int = 15) -> str:
    return "\n".join("    " + line for line in log.strip().splitlines()[-n:])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="*", help="test files/dirs to scan (default: tests/)")
    ap.add_argument("--root", type=Path, default=REPO_ROOT, help="repo root to run pytest in")
    ap.add_argument("--guard", help="ad-hoc: pytest node id of one test to check")
    ap.add_argument("--target", help="ad-hoc: module:qualname the --guard test protects")
    ap.add_argument("--replace", nargs=2, action="append", metavar=("OLD", "NEW"),
                    default=[], help="ad-hoc: named defect as a source edit (repeatable)")
    ap.add_argument("--must-kill", action="append", default=[], metavar="MUTANT_ID",
                    help="ad-hoc: generated mutant id that must make the test fail")
    ap.add_argument("--list-mutants", metavar="TARGET",
                    help="print the generated mutant ids for module:qualname and exit")
    ap.add_argument("--undeclared", action="store_true",
                    help="list refusal-named tests that declare no guards target")
    ap.add_argument("--python", default=sys.executable, help="interpreter for child pytest")
    ap.add_argument("--timeout", type=float, default=900, help="seconds per child pytest")
    ap.add_argument("--json", action="store_true", help="print results as JSON")
    ns = ap.parse_args(argv)
    root = ns.root.resolve()

    if ns.list_mutants:
        sys.path[:0] = [str(root)]
        try:
            func = resolve_target(ns.list_mutants)
            for mid in list_mutants(func):
                print(mid)
        except GuardError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        return 0

    started = time.monotonic()
    runs: list[tuple[list[str], dict | None, set[str]]] = []
    undeclared: list[str] = []
    if ns.guard:
        if not ns.target:
            ap.error("--guard needs --target")
        override = {"target": ns.target, "replace": ns.replace, "must_kill": ns.must_kill}
        runs.append(([ns.guard], override, {ns.guard.split("::")[-1].split("[")[0]}))
    else:
        if ns.replace or ns.must_kill or ns.target:
            ap.error("--target/--replace/--must-kill need --guard")
        paths = [root / p for p in ns.paths] or [root / "tests"]
        declared, undeclared = scan_tests(paths)
        for f, names in sorted(declared.items()):
            runs.append(([str(f.relative_to(root)), "-m", MARKER], None, names))

    results: list[dict] = []
    for args, override, expected in runs:
        got, log = _run_child(root, args, override, ns.python, ns.timeout)
        results.extend(got)
        ran = {r["nodeid"].split("::")[-1].split("[")[0] for r in got}
        for name in sorted(expected - ran):
            results.append({
                "nodeid": args[0] if override else f"{args[0]}::{name}", "target": (override or {}).get("target"),
                "verdict": "ERROR",
                "reason": "declared guard never reached the checker (collection error?)\n"
                          + _tail(log),
            })
    elapsed = time.monotonic() - started

    bad = [r for r in results if r["verdict"] == "NON-DISCRIMINATING"]
    errors = [r for r in results if r["verdict"] == "ERROR"]
    if ns.json:
        print(json.dumps({"results": results, "seconds": round(elapsed, 1),
                          "undeclared": undeclared}, indent=2))
    else:
        for r in results:
            print(f"{r['verdict']}: {r['nodeid']}")
            print(f"    target: {r['target']}")
            if r.get("mutants"):
                print("    mutants: " + ", ".join(f"{k}={v}" for k, v in r["mutants"].items()))
            if r.get("calls") is not None:
                print(f"    guarded function calls in baseline run: {r['calls']}")
            if r["reason"]:
                print(f"    {r['reason']}")
        if ns.undeclared:
            for name in undeclared:
                print(f"UNDECLARED: {name}")
        print(
            f"guard-discrimination: {len(results)} guard(s) checked, "
            f"{len(bad)} non-discriminating, {len(errors)} error(s), "
            f"{len(undeclared)} refusal-named test(s) undeclared, {elapsed:.1f}s"
        )
    if bad:
        return 1
    return 2 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
