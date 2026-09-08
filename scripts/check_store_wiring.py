#!/usr/bin/env python3
"""BaseStore wiring guard.

Detects PRs that add a new BaseStore subclass but never wire it into
tinyagentos/app.py. Routes reach stores ONLY via request.app.state, so a
store that is never assigned to app.state is unreachable.

Algorithm:
  1. Scan the PR diff for Python files under tinyagentos/.
  2. For each newly added file, find classes that subclass BaseStore
     (directly or transitively).
  3. For each modified file, find classes whose ``class Foo(BaseStore)``
     definition line appears in the added diff lines.
  4. For each newly-added store class, check that its class name appears
     somewhere in tinyagentos/app.py (name-level check). Checked BEFORE the
     exemption below, so a base that IS wired is logged as wired.
  5. Skip classes that some other class under tinyagentos/ subclasses AND
     that declare no SCHEMA of their own: such a base exists to be inherited
     from, never to be assigned to app.state, and its concrete subclasses are
     checked in their own right. A base that declares SCHEMA owns tables, so
     it is a store and stays policed. "Subclassed" is decided on the
     (module, class name) identity, so a base in one module cannot exempt an
     unrelated class of the same name in another.
  6. A "Store-Unwired-Intentionally: <ClassName>, <why>" trailer in the PR
     body waives a named class and logs it.

Usage:
    python scripts/check_store_wiring.py
    python scripts/check_store_wiring.py --base origin/dev
    python scripts/check_store_wiring.py --base origin/dev --pr-body "..."
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TRAILER = "Store-Unwired-Intentionally:"


@dataclass
class Violation:
    class_name: str
    file_path: str
    reason: str = ""


def _run_git(args: list[str], repo_root: Path) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo_root, capture_output=True, text=True, check=True,
    )
    return result.stdout


def _parse_name_status(output: str) -> list[tuple[str, str]]:
    changed: list[tuple[str, str]] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0]
        path = parts[-1]
        changed.append((status[0], path))
    return changed


def _git_changed(base_ref: str, repo_root: Path) -> list[tuple[str, str]]:
    out = _run_git(["diff", "--name-status", f"{base_ref}...HEAD"], repo_root)
    return _parse_name_status(out)


def _get_file_at_ref(file_path: str, ref: str, repo_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "show", f"{ref}:{file_path}"],
            cwd=repo_root, capture_output=True, text=True, check=True,
        )
        return result.stdout
    except subprocess.CalledProcessError:
        return None


def _class_def_in_added_lines(
    file_path: str, class_name: str, base_ref: str, repo_root: Path,
) -> bool:
    """Return True if the class definition is newly added in the PR."""
    base_content = _get_file_at_ref(file_path, base_ref, repo_root)
    if base_content is not None:
        try:
            base_tree = ast.parse(base_content)
            for node in ast.walk(base_tree):
                if isinstance(node, ast.ClassDef) and node.name == class_name:
                    return False
        except SyntaxError:
            pass

    diff = _run_git(["diff", f"{base_ref}...HEAD", "--", file_path], repo_root)
    pattern = re.compile(rf"^\+.*class\s+{re.escape(class_name)}\s*\(", re.MULTILINE)
    return bool(pattern.search(diff))


def _is_wired_ast(app_py_content: str, class_name: str) -> bool:
    """Return True if class_name is instantiated and assigned to app.state.

    Handles:
      app.state.X = ClassName(...)
      x = ClassName(...); app.state.X = x
      x = ClassName(...); y = x; app.state.Y = y
    """
    try:
        tree = ast.parse(app_py_content)
    except SyntaxError:
        return False

    instance_vars: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if isinstance(node.value, ast.Call):
            func = node.value.func
            if isinstance(func, ast.Name) and func.id == class_name:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        instance_vars.add(target.id)

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                if isinstance(node.value, ast.Name) and node.value.id in instance_vars:
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id not in instance_vars:
                            instance_vars.add(target.id)
                            changed = True

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Attribute):
                continue
            if not (isinstance(target.value, ast.Name) and target.value.id == "app" and target.attr == "state"):
                continue
            if isinstance(node.value, ast.Call):
                func = node.value.func
                if isinstance(func, ast.Name) and func.id == class_name:
                    return True
            if isinstance(node.value, ast.Name) and node.value.id in instance_vars:
                return True

    return False


def _is_wired_in_app_py(app_py_content: str, class_name: str) -> tuple[bool, str]:
    ast_ok = _is_wired_ast(app_py_content, class_name)
    if ast_ok:
        return True, "AST"
    code_only = re.sub(r"#.*", "", app_py_content)
    name_ok = bool(re.search(rf"\b{re.escape(class_name)}\b", code_only))
    if name_ok:
        return True, "name-level-fallback"
    return False, "unwired"


def _base_names(node: ast.ClassDef) -> set[str]:
    """The terminal names of a class's direct bases.

    ``class NotesStore(shared_db.SharedDBStore)`` records ``SharedDBStore``, or
    a store declared through a qualified import would look like it inherits
    nothing: unpoliced as a store, and useless as a base.
    """
    return {name for _prefix, name in _base_refs(node)}


def _base_refs(node: ast.ClassDef) -> set[tuple]:
    """The direct bases as ``(module-alias or None, class name)`` pairs.

    ``class Foo(Bar)`` gives ``(None, "Bar")``; ``class Foo(mod.Bar)`` gives
    ``("mod", "Bar")`` -- the alias is what tells the resolver which module the
    class was reached through.
    """
    refs: set[tuple] = set()
    for base in node.bases:
        if isinstance(base, ast.Name):
            refs.add((None, base.id))
        elif isinstance(base, ast.Attribute):
            prefix = base.value.id if isinstance(base.value, ast.Name) else None
            refs.add((prefix, base.attr))
    return refs


def _iter_modules(repo_root: Path):
    """Yield ``(repo-relative path, parsed module)`` for every store module."""
    tinyagentos_dir = repo_root / "tinyagentos"
    if not tinyagentos_dir.is_dir():
        return
    for py_file in sorted(tinyagentos_dir.rglob("*.py")):
        try:
            source = py_file.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue
        yield py_file.relative_to(repo_root).as_posix(), tree


def build_class_hierarchy(repo_root: Path) -> dict[str, set[str]]:
    """Build a map of class_name -> set of direct base class names.

    Bare-name keyed, and the bases of same-named classes in different modules
    are UNIONED rather than overwritten.  This map answers "could a class by
    this name be a store?", where merging errs towards policing more classes.
    The EXEMPTION must not be decided from it -- there merging is the unsafe
    direction, so it uses ``build_qualified_hierarchy`` instead.
    """
    classes: dict[str, set[str]] = {}
    for _module, tree in _iter_modules(repo_root):
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                classes.setdefault(node.name, set()).update(_base_names(node))
    return classes


def _module_import_map(module: str, tree: ast.AST) -> dict[str, list[str]]:
    """Map each name bound by an import to the module path(s) it could name.

    ``from tinyagentos.projects.tx import ProjectsDBStore`` binds the CLASS, so
    the name resolves to ``tinyagentos/projects/tx.py``.
    ``from tinyagentos.projects import canvas`` binds a MODULE, so
    ``canvas.Foo`` resolves to ``tinyagentos/projects/canvas.py`` (or its
    package ``__init__``).  Both readings are recorded; the caller keeps
    whichever actually defines the class it is resolving.
    """
    bound: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if not node.module or node.level:
                continue
            pkg = node.module.replace(".", "/")
            for alias in node.names:
                local = alias.asname or alias.name
                bound.setdefault(local, []).extend([
                    f"{pkg}.py",
                    f"{pkg}/__init__.py",
                    f"{pkg}/{alias.name}.py",
                    f"{pkg}/{alias.name}/__init__.py",
                ])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                mod = alias.name.replace(".", "/")
                bound.setdefault(local, []).extend([
                    f"{mod}.py", f"{mod}/__init__.py",
                ])
    return bound


def build_qualified_hierarchy(repo_root: Path) -> dict[tuple, set[tuple]]:
    """Map ``(module, class_name)`` -> the ``(module, class_name)`` it subclasses.

    Two modules may define the same class name.  Keyed by bare name, one
    module's subclassed base handed its exemption to an unrelated, unwired
    class of the same name in another module: the gate reported clean for a
    store nothing could reach.  Each class is therefore tracked by the module
    that defines it, and every base reference is resolved to that identity --
    same module first, then the module the name was imported from.

    A base that resolves nowhere is DROPPED rather than guessed.  The only
    thing this map decides is whether a class is exempt from the wiring
    requirement, so an unresolved base has to mean "not exempt" -- policed,
    with the ``Store-Unwired-Intentionally`` trailer as the way out.
    """
    defined: dict[str, set[str]] = {}
    parsed: list[tuple[str, ast.AST]] = []
    for module, tree in _iter_modules(repo_root):
        parsed.append((module, tree))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                defined.setdefault(module, set()).add(node.name)

    hierarchy: dict[tuple, set[tuple]] = {}
    for module, tree in parsed:
        imports = _module_import_map(module, tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            resolved: set[tuple] = set()
            for prefix, base_name in _base_refs(node):
                # `class Foo(mod.Bar)` is reached through the alias `mod`;
                # `class Foo(Bar)` through the class name itself, defined here
                # or imported.
                if prefix is None and base_name in defined.get(module, set()):
                    resolved.add((module, base_name))
                    continue
                for candidate in imports.get(prefix or base_name, []):
                    if base_name in defined.get(candidate, set()):
                        resolved.add((candidate, base_name))
                        break
            hierarchy[(module, node.name)] = resolved
    return hierarchy


def find_intermediate_bases(classes: dict) -> set:
    """The class identities that some other class under ``tinyagentos/`` subclasses.

    Works on either hierarchy: bare names in, bare names out;
    ``(module, class_name)`` in, ``(module, class_name)`` out.  Being
    subclassed is only HALF of what exempts a class -- see
    ``class_declares_schema``, which supplies the other half.
    """
    bases: set = set()
    for own_bases in classes.values():
        bases.update(own_bases)
    return bases


def class_declares_schema(source: str, class_name: str) -> bool:
    """Return True if ``class_name``'s body assigns ``SCHEMA``.

    ``SCHEMA`` is how a store declares the tables it owns, so it separates a
    real store from a base class that only carries behaviour.  A class is
    exempt from the wiring requirement only when it is subclassed AND owns no
    tables: ``ProjectsDBStore`` in tinyagentos/projects/tx.py is the case --
    it carries the shared projects.db transaction helper, has no tables of its
    own and is never instantiated, so there is nothing to assign to
    ``app.state``.  Requiring both halves keeps a real store policed even once
    somebody subclasses it: without the SCHEMA half, giving an unwired store a
    subclass would launder the parent past the gate.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue
        for stmt in node.body:
            targets: list[ast.expr] = []
            if isinstance(stmt, ast.Assign):
                targets = list(stmt.targets)
            elif isinstance(stmt, ast.AnnAssign):
                targets = [stmt.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id == "SCHEMA":
                    return True
    return False


def _inherits_base_store(
    class_name: str,
    classes: dict[str, set[str]],
    visited: set[str] | None = None,
) -> bool:
    if visited is None:
        visited = set()
    if class_name == "BaseStore":
        return True
    if class_name in visited:
        return False
    visited.add(class_name)
    for base in classes.get(class_name, set()):
        if _inherits_base_store(base, classes, visited):
            return True
    return False


def find_base_store_subclasses_in_file(
    source: str, all_classes: dict[str, set[str]],
) -> set[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()

    classes_in_file: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            classes_in_file.add(node.name)

    return {
        name for name in classes_in_file
        if _inherits_base_store(name, all_classes)
    }


def parse_waived_classes(pr_body: str | None) -> set[str]:
    """Parse Store-Unwired-Intentionally trailer from PR body text.

    Expected format: ``Store-Unwired-Intentionally: <ClassName>, <why>``
    Only the first comma-delimited token is treated as the class name; the
    remainder is the human-readable reason.
    """
    waived: set[str] = set()
    if not pr_body:
        return waived
    for line in pr_body.splitlines():
        line = line.strip()
        if line.startswith(TRAILER):
            classes_str = line[len(TRAILER):].strip()
            cls = classes_str.split(",", 1)[0].strip()
            if cls:
                waived.add(cls)
    return waived


def check_store_wiring(
    base_ref: str,
    repo_root: Path = REPO_ROOT,
    pr_body: str | None = None,
) -> tuple[list[Violation], set[str]]:
    changed = _git_changed(base_ref, repo_root)

    app_py_path = repo_root / "tinyagentos" / "app.py"
    app_py_content = ""
    if app_py_path.exists():
        app_py_content = app_py_path.read_text(encoding="utf-8", errors="ignore")

    all_classes = build_class_hierarchy(repo_root)
    # Keyed by (module, class_name): a subclassed base in one module must not
    # exempt an unrelated class of the same name in another.
    intermediate_bases = find_intermediate_bases(
        build_qualified_hierarchy(repo_root)
    )

    violations: list[Violation] = []
    waived: set[str] = set()
    waived.update(parse_waived_classes(pr_body))

    for status, file_path in changed:
        if not file_path.startswith("tinyagentos/") or not file_path.endswith(".py"):
            continue
        if status.startswith("D"):
            continue
        if not (status.startswith("A") or status.startswith("M") or status.startswith("R") or status.startswith("C")):
            continue

        abs_path = repo_root / file_path
        if not abs_path.exists():
            continue

        source = abs_path.read_text(encoding="utf-8", errors="ignore")
        store_classes = find_base_store_subclasses_in_file(source, all_classes)
        if not store_classes:
            continue

        for class_name in sorted(store_classes):
            is_new = False
            if status.startswith("A") or status.startswith("R") or status.startswith("C"):
                is_new = True
            elif status.startswith("M"):
                is_new = _class_def_in_added_lines(
                    file_path, class_name, base_ref, repo_root,
                )

            if not is_new:
                continue

            if class_name in waived:
                continue

            # Wiring is checked BEFORE the exemption, so the log says what is
            # actually true of the class: a base that IS assigned to app.state
            # is reported as wired rather than as an exempt base nobody can
            # reach.  Both paths pass, so this only changes the audit trail.
            wired, how = _is_wired_in_app_py(app_py_content, class_name)
            if wired:
                continue

            if (file_path, class_name) in intermediate_bases and not class_declares_schema(
                source, class_name,
            ):
                # Subclassed AND owns no tables: a base that carries behaviour,
                # with nothing to assign to app.state, whose subclasses are
                # checked in their own right.  A base that declares SCHEMA is a
                # store and stays policed, so subclassing an unwired store
                # cannot launder it past the gate.  Matched on (module, class):
                # another module's class of the same name is a different class
                # and gets no exemption from this one.  Printed, not silent, so
                # an exemption stays visible in the gate's log.
                print(
                    f"store-wiring-guard: {class_name} in {file_path} declares no "
                    f"tables and is a base class for other stores; its subclasses "
                    f"are checked instead"
                )
                continue

            violations.append(Violation(
                class_name=class_name,
                file_path=file_path,
                reason=how,
            ))

    return violations, waived


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=None, help="Target branch ref (e.g. origin/dev)")
    parser.add_argument("--pr-body", default=None, help="PR body text (for Store-Unwired-Intentionally trailer)")
    args = parser.parse_args(argv)

    base_ref = args.base
    if base_ref is None:
        base_ref = os.environ.get("BASE_REF", "origin/dev")

    pr_body = args.pr_body
    if pr_body is None:
        pr_body = os.environ.get("PR_BODY")

    violations, waived_classes = check_store_wiring(base_ref, REPO_ROOT, pr_body)

    if waived_classes:
        for cls in sorted(waived_classes):
            print(f"store-wiring-guard: waived via Store-Unwired-Intentionally: {cls}")

    if violations:
        print(
            f"STORE-WIRING FAIL: {len(violations)} new BaseStore subclass(es) are not wired "
            f"into tinyagentos/app.py. Routes reach stores ONLY via request.app.state, "
            f"so an unwired store is unreachable:"
        )
        for v in violations:
            print(f"  - {v.class_name} in {v.file_path} ({v.reason})")
        return 1

    print("store-wiring-guard: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
