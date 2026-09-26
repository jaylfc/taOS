"""Tests for the guard-discrimination gate (scripts/check_non_discriminating.py).

The integration tests build a tiny synthetic project in a temp dir: a product
module with three guarded functions, and guard tests for each in a weak form
(cannot fail on the defect it names) and a strong form (fails on it). The weak
forms reproduce the three shapes from the card: the unit under test is
monkeypatched, the assertion cannot see the defect, and the fixture's values
never reach the guarded branch. The gate must flag every weak form (exit 1)
and pass every strong form (exit 0).
"""
from __future__ import annotations

import asyncio
import functools
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import check_non_discriminating as cnd  # noqa: E402

SCRIPT = Path(__file__).parent.parent / "scripts" / "check_non_discriminating.py"

PRODUCT = '''
def can_read(user, owner):
    """Only the owner may read."""
    if user == owner:
        return True
    return False


def authorize(sender, token_owner):
    """The sender must be the identity the token was minted for."""
    return sender == token_owner


def resolve_identity(holder, is_admin):
    """An admin acts as a FIXED principal; holder is display text only."""
    if is_admin:
        return "@operator"
    return holder


def may_release(lease_owner, holder, is_admin):
    return resolve_identity(holder, is_admin) == lease_owner


async def check_scope(scopes, needed):
    await asyncio.sleep(0)
    if needed not in scopes:
        raise PermissionError(needed)
    return True
'''

TESTS = '''
import asyncio

import pytest

import nd_product
from nd_product import can_read, authorize, may_release, check_scope

# Shape 1: the unit under test is monkeypatched away.
@pytest.mark.guards("nd_product:can_read")
def test_weak_cannot_read_others_doc(monkeypatch):
    monkeypatch.setattr(nd_product, "can_read", lambda user, owner: user == owner)
    assert nd_product.can_read("mallory", "alice") is False

@pytest.mark.guards("nd_product:can_read")
def test_strong_cannot_read_others_doc():
    assert can_read("mallory", "alice") is False

# Shape 2: the assertion cannot see the defect (only the agreeing case).
@pytest.mark.guards("nd_product:authorize",
                    replace=[("sender == token_owner", "token_owner == token_owner")])
def test_weak_rejects_sender_not_bound_to_token():
    assert authorize("alice", "alice") is True

@pytest.mark.guards("nd_product:authorize",
                    replace=[("sender == token_owner", "token_owner == token_owner")])
def test_strong_rejects_sender_not_bound_to_token():
    assert authorize("alice", "alice") is True
    assert authorize("mallory", "alice") is False

# Shape 3: the fixture's values never reach the guarded branch.
@pytest.mark.guards("nd_product:resolve_identity",
                    replace=[('return "@operator"', 'return holder or "@operator"')])
def test_weak_admin_does_not_free_another_holders_lease():
    assert may_release("a2a:victim", None, is_admin=True) is False

@pytest.mark.guards("nd_product:resolve_identity",
                    replace=[('return "@operator"', 'return holder or "@operator"')])
def test_strong_admin_does_not_free_another_holders_lease():
    assert may_release("a2a:victim", None, is_admin=True) is False
    assert may_release("a2a:victim", "a2a:victim", is_admin=True) is False

# Async guard, generated mutant named explicitly.
@pytest.mark.asyncio
@pytest.mark.guards("nd_product:check_scope", must_kill=["drop-raise@29"])
async def test_strong_check_scope_rejects_missing_scope():
    with pytest.raises(PermissionError):
        await check_scope({"read"}, "write")
'''


def _project(tmp_path: Path, tests: str = TESTS, product: str = PRODUCT,
             ini: str = "") -> Path:
    (tmp_path / "pytest.ini").write_text("[pytest]\n" + ini)
    (tmp_path / "nd_product.py").write_text("import asyncio\n" + product)
    tdir = tmp_path / "tests"
    tdir.mkdir()
    (tdir / "test_guards.py").write_text(tests)
    return tmp_path


def _gate(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), "--json", *args],
        capture_output=True, text=True, timeout=300, check=False,
    )


def _verdicts(proc: subprocess.CompletedProcess) -> dict[str, dict]:
    data = json.loads(proc.stdout)
    return {r["nodeid"].split("::")[-1]: r for r in data["results"]}


# ── the gate end to end (RED and GREEN) ──────────────────────────────────────

@pytest.fixture(scope="module")
def synthetic_run(tmp_path_factory):
    root = _project(tmp_path_factory.mktemp("nd"))
    return _gate(root)


def test_gate_exits_1_when_any_guard_cannot_discriminate(synthetic_run):
    assert synthetic_run.returncode == 1, synthetic_run.stdout + synthetic_run.stderr


def test_every_weak_guard_is_flagged(synthetic_run):
    v = _verdicts(synthetic_run)
    for name in (
        "test_weak_cannot_read_others_doc",
        "test_weak_rejects_sender_not_bound_to_token",
        "test_weak_admin_does_not_free_another_holders_lease",
    ):
        assert v[name]["verdict"] == "NON-DISCRIMINATING", v[name]


def test_every_strong_guard_passes(synthetic_run):
    v = _verdicts(synthetic_run)
    for name in (
        "test_strong_cannot_read_others_doc",
        "test_strong_rejects_sender_not_bound_to_token",
        "test_strong_admin_does_not_free_another_holders_lease",
        "test_strong_check_scope_rejects_missing_scope",
    ):
        assert v[name]["verdict"] == "OK", v[name]


def test_monkeypatched_guard_is_reported_as_never_executing_the_target(synthetic_run):
    r = _verdicts(synthetic_run)["test_weak_cannot_read_others_doc"]
    assert r["calls"] == 0
    assert set(r["mutants"].values()) == {"survived"}
    assert "never executed" in r["reason"]


def test_named_defect_is_reported_by_its_source_edit(synthetic_run):
    r = _verdicts(synthetic_run)["test_weak_admin_does_not_free_another_holders_lease"]
    assert r["mutants"] == {"replace#1": "survived"}
    assert "return holder or" in r["reason"]
    assert r["calls"] == 1  # it DID run the function: the branch is what it missed


def test_gate_exits_0_when_only_discriminating_guards_remain(tmp_path):
    strong_only = "\n\n".join(
        block for block in TESTS.split("\n\n")
        if "test_weak_" not in block
    )
    proc = _gate(_project(tmp_path, tests=strong_only))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert {r["verdict"] for r in _verdicts(proc).values()} == {"OK"}


def test_adhoc_mode_checks_an_undecorated_test(tmp_path):
    tests = textwrap.dedent('''
        from nd_product import authorize
        def test_rejects_forged_sender():
            assert authorize("alice", "alice")
    ''')
    root = _project(tmp_path, tests=tests)
    node = "tests/test_guards.py::test_rejects_forged_sender"
    weak = _gate(root, "--guard", node, "--target", "nd_product:authorize",
                 "--replace", "sender == token_owner", "True")
    assert weak.returncode == 1, weak.stdout + weak.stderr
    # The same test DOES die on a flipped comparison: the gate reports per variant.
    ok = _gate(root, "--guard", node, "--target", "nd_product:authorize",
               "--must-kill", "flip-cmp@12")
    assert ok.returncode == 0, ok.stdout + ok.stderr


# ── an unchecked guard is an error, never a pass ─────────────────────────────

@pytest.mark.parametrize("decorator, why", [
    ('@pytest.mark.guards("nd_product:can_read", replace=[("no such text", "x")])',
     "found 0 times"),
    ('@pytest.mark.guards("nd_product:can_read", must_kill=["negate-if@999"])',
     "unknown mutant"),
    ('@pytest.mark.guards("nd_product:nope")', "no attribute"),
    ('@pytest.mark.guards("no_such_module:f")', "cannot import"),
])
def test_bad_declaration_is_an_error(tmp_path, decorator, why):
    tests = f"import pytest\nfrom nd_product import can_read\n\n{decorator}\n" \
            "def test_cannot_read():\n    assert can_read('a', 'b') is False\n"
    proc = _gate(_project(tmp_path, tests=tests))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    (r,) = _verdicts(proc).values()
    assert r["verdict"] == "ERROR" and why in r["reason"], r


UNRELATED = PRODUCT + '''

def unrelated(x):
    if x > 0:
        return "pos"
    return "neg"
'''


def test_kill_by_a_test_that_fails_any_rerun_is_an_error_not_ok(tmp_path):
    """Leaked state fails every rerun: the 'kill' says nothing about the mutant.

    The test leaks a module-level counter, so its second in-process run fails
    whatever code is swapped in. Without an unmutated control run after the
    kill, the gate read that as return-true=killed and said OK.
    """
    tests = textwrap.dedent('''
        import pytest
        from nd_product import unrelated

        _runs = {"n": 0}

        @pytest.mark.guards("nd_product:unrelated")
        def test_second_run_fails():
            _runs["n"] += 1
            assert _runs["n"] == 1
            assert unrelated(1) == "pos"
    ''')
    proc = _gate(_project(tmp_path, tests=tests, product=UNRELATED))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    (r,) = _verdicts(proc).values()
    assert r["verdict"] == "ERROR", r
    assert "control run" in r["reason"], r


def test_stable_test_still_passes_its_control_run(tmp_path):
    tests = textwrap.dedent('''
        import pytest
        from nd_product import unrelated

        @pytest.mark.guards("nd_product:unrelated")
        def test_negative_is_neg():
            assert unrelated(-1) == "neg"
    ''')
    proc = _gate(_project(tmp_path, tests=tests, product=UNRELATED))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    (r,) = _verdicts(proc).values()
    assert r["verdict"] == "OK" and r["control"] == "passed", r


def test_repo_per_test_timeout_does_not_kill_the_checker(tmp_path):
    """pytest-timeout's thread method os._exit()s the child mid-guard.

    The profiled baseline is slower than a plain run, so a repo-wide
    per-test timeout could kill the child before it wrote any result.
    The driver bounds the child itself; the per-test timeout must be off.
    """
    tests = textwrap.dedent('''
        import time
        import pytest
        from nd_product import can_read

        @pytest.mark.guards("nd_product:can_read")
        def test_slow_cannot_read():
            time.sleep(1.5)
            assert can_read("mallory", "alice") is False
    ''')
    root = _project(tmp_path, tests=tests, ini="timeout = 1\ntimeout_method = thread\n")
    proc = _gate(root)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    (r,) = _verdicts(proc).values()
    assert r["verdict"] == "OK", r


def test_child_death_mid_guard_names_the_guard(tmp_path):
    tests = textwrap.dedent('''
        import os
        import pytest
        from nd_product import can_read

        @pytest.mark.guards("nd_product:can_read")
        def test_dies_cannot_read():
            os._exit(3)
    ''')
    proc = _gate(_project(tmp_path, tests=tests))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    (r,) = _verdicts(proc).values()
    assert r["nodeid"].endswith("::test_dies_cannot_read"), r
    assert r["verdict"] == "ERROR" and "died while checking this guard" in r["reason"], r


def test_failing_baseline_is_an_error(tmp_path):
    tests = "import pytest\nfrom nd_product import can_read\n\n" \
            '@pytest.mark.guards("nd_product:can_read")\n' \
            "def test_cannot_read():\n    assert can_read('a', 'a') is False\n"
    proc = _gate(_project(tmp_path, tests=tests))
    assert proc.returncode == 2
    (r,) = _verdicts(proc).values()
    assert "baseline run failed" in r["reason"]


def test_undeclared_refusal_named_tests_are_counted_not_checked(tmp_path):
    tests = "def test_x_cannot_y():\n    pass\n\ndef test_plain():\n    pass\n"
    proc = _gate(_project(tmp_path, tests=tests))
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert data["results"] == []
    assert [u.split("::")[-1] for u in data["undeclared"]] == ["test_x_cannot_y"]


# ── mutant compilation (in process) ──────────────────────────────────────────

def _closure_factory():
    secret = "s3"

    def check(token):
        if token == secret:
            return True
        return False

    return check


class _Base:
    def allowed(self, who):
        return who in ("root", "guest")


class _Child(_Base):
    def allowed(self, who):
        if who == "guest":
            return False
        return super().allowed(who)


def _wrapping(fn):
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        return fn(*a, **kw)
    return wrapper


@_wrapping
def _decorated(x):
    return x > 1


def _swap(func, mutant):
    original = func.__code__
    func.__code__ = cnd.compile_mutant(func, mutant)
    return original


def test_mutant_of_a_closure_keeps_its_free_variables():
    check = _closure_factory()
    ids = cnd.list_mutants(check)
    flip = next(m for m in ids if m.startswith("flip-cmp@"))
    original = _swap(check, flip)
    try:
        assert check("s3") is False and check("x") is True
    finally:
        check.__code__ = original
    assert check("s3") is True


def test_mutant_of_a_method_using_super():
    target = _Child.allowed
    original = _swap(target, "negate-if@" + str(target.__code__.co_firstlineno + 1))
    try:
        assert _Child().allowed("guest") is True   # super() still resolves
    finally:
        target.__code__ = original
    assert _Child().allowed("guest") is False


def test_decorated_target_resolves_to_the_wrapped_function():
    func = cnd.resolve_target(f"{__name__}:_decorated")
    assert func is _decorated.__wrapped__
    original = _swap(func, "return-true")
    try:
        assert _decorated(0) is True  # the wrapper now runs the mutant
    finally:
        func.__code__ = original


def test_async_mutant_stays_a_coroutine():
    async def gate(x):
        if x:
            raise PermissionError
        return "ok"

    original = _swap(gate, "drop-raise@" + str(gate.__code__.co_firstlineno + 2))
    try:
        assert asyncio.run(gate(True)) == "ok"
    finally:
        gate.__code__ = original


def test_generator_gets_no_constant_return_mutants():
    def gen(xs):
        for x in xs:
            if x:
                yield x

    ids = cnd.list_mutants(gen)
    assert not any(i.startswith("return-") for i in ids)
    assert any(i.startswith("negate-if@") for i in ids)
