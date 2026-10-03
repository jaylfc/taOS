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
@pytest.mark.guards("nd_product:check_scope", must_kill=["drop-raise#1"])
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
               "--must-kill", "flip-cmp#1")
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
    flip = next(m for m in ids if m.startswith("flip-cmp#"))
    original = _swap(check, flip)
    try:
        assert check("s3") is False and check("x") is True
    finally:
        check.__code__ = original
    assert check("s3") is True


def test_mutant_of_a_method_using_super():
    target = _Child.allowed
    ids = cnd.list_mutants(target)
    negate_if = next(m for m in ids if m.startswith("negate-if#"))
    original = _swap(target, negate_if)
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

    ids = cnd.list_mutants(gate)
    drop_raise = next(m for m in ids if m.startswith("drop-raise#"))
    original = _swap(gate, drop_raise)
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
    assert any(i.startswith("negate-if#") for i in ids)


# ── new uncheckable target shapes (ERROR, never NON-DISCRIMINATING) ────────────

def test_lru_cache_target_is_error(tmp_path):
    """A guard on an lru_cache-wrapped function is uncheckable (ERROR)."""
    product = '''
from functools import lru_cache

@lru_cache(maxsize=None)
def cached_compute(x):
    if x > 0:
        return "positive"
    return "non-positive"
'''
    tests = '''
import pytest
from nd_product import cached_compute

@pytest.mark.guards("nd_product:cached_compute", replace=[('return "positive"', 'return "negative"')])
def test_cached_compute_positive():
    assert cached_compute(1) == "positive"
'''
    proc = _gate(_project(tmp_path, tests=tests, product=product))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    (r,) = _verdicts(proc).values()
    assert r["verdict"] == "ERROR"
    assert "lru_cache" in r["reason"].lower() or "cache" in r["reason"].lower()


def test_default_argument_edit_is_error(tmp_path):
    """A replace mutation on a default argument value is uncheckable (ERROR)."""
    product = '''
def greet(name, greeting="Hello"):
    return f"{greeting}, {name}!"
'''
    tests = '''
import pytest
from nd_product import greet

@pytest.mark.guards("nd_product:greet", replace=[('greeting="Hello"', 'greeting="Hi"')])
def test_greet_default():
    assert greet("Alice") == "Hello, Alice!"
'''
    proc = _gate(_project(tmp_path, tests=tests, product=product))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    (r,) = _verdicts(proc).values()
    assert r["verdict"] == "ERROR"
    assert "default argument" in r["reason"].lower()


def test_default_argument_value_mutation_is_error(tmp_path):
    """A replace mutation on a default argument value (simple form) is uncheckable (ERROR)."""
    product = '''
def compute(x, threshold=10):
    if x > threshold:
        return "high"
    return "low"
'''
    tests = '''
import pytest
from nd_product import compute

@pytest.mark.guards("nd_product:compute", replace=[("threshold=10", "threshold=20")])
def test_compute_threshold():
    assert compute(15) == "high"
'''
    proc = _gate(_project(tmp_path, tests=tests, product=product))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    (r,) = _verdicts(proc).values()
    assert r["verdict"] == "ERROR"
    assert "default argument" in r["reason"].lower()


def test_decorator_mutation_is_error(tmp_path):
    """A replace mutation on a decorator is uncheckable (ERROR)."""
    product = '''
import functools

def my_decorator(fn):
    @functools.wraps(fn)
    def wrapper(x):
        return fn(x) + 1
    return wrapper

@my_decorator
def compute(x):
    if x > 0:
        return 10
    return 20
'''
    tests = '''
import pytest
from nd_product import compute

@pytest.mark.guards("nd_product:compute", replace=[("@my_decorator", "@staticmethod")])
def test_compute_decorated():
    assert compute(1) == 11
'''
    proc = _gate(_project(tmp_path, tests=tests, product=product))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    (r,) = _verdicts(proc).values()
    assert r["verdict"] == "ERROR"
    assert "decorator" in r["reason"].lower()


def test_lru_cache_target_with_must_kill_is_error(tmp_path):
    """A guard on an lru_cache-wrapped function with must_kill is uncheckable (ERROR)."""
    product = '''
from functools import lru_cache

@lru_cache(maxsize=None)
def cached_compute(x):
    if x > 0:
        return "positive"
    return "non-positive"
'''
    tests = '''
import pytest
from nd_product import cached_compute

@pytest.mark.guards("nd_product:cached_compute", must_kill=["negate-if#1"])
def test_cached_compute_positive():
    assert cached_compute(1) == "positive"
'''
    proc = _gate(_project(tmp_path, tests=tests, product=product))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    (r,) = _verdicts(proc).values()
    assert r["verdict"] == "ERROR"
    assert "lru_cache" in r["reason"].lower() or "cache" in r["reason"].lower()


def test_own_scope_excludes_decorator_and_default_mutants(tmp_path):
    """_own_scope does not yield mutation sites in decorators or default args."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    import check_non_discriminating as cnd
    import ast

    product = '''
def simple_decorator(fn):
    return fn

@simple_decorator
def compute(x, threshold=10):  # simple default, no mutable site
    if x > threshold:
        return "high"
    return "low"
'''
    tests = '''
import pytest
from nd_product import compute

@pytest.mark.guards("nd_product:compute", must_kill=["negate-if#1"])
def test_compute():
    assert compute(15) == "high"
    assert compute(5) == "low"
'''
    # The function has:
    # - 1 negate-if in body (x > threshold)
    # - 1 flip-cmp in body (x > threshold)
    # - NO mutants from decorator or default arg (excluded by _own_scope)
    root = _project(tmp_path, tests=tests, product=product)
    
    # First check list_mutants directly
    import sys
    sys.path.insert(0, str(root))
    import nd_product
    ids = cnd.list_mutants(nd_product.compute)
    # Should only have body mutants: return-true, return-false, return-none, negate-if#1, flip-cmp#1
    assert "negate-if#1" in ids
    assert "flip-cmp#1" in ids
    # No other negate-if or flip-cmp from decorator/default
    negate_ifs = [i for i in ids if i.startswith("negate-if#")]
    flip_cmps = [i for i in ids if i.startswith("flip-cmp#")]
    assert len(negate_ifs) == 1, f"Expected 1 negate-if, got {negate_ifs}"
    assert len(flip_cmps) == 1, f"Expected 1 flip-cmp, got {flip_cmps}"
    
    # Now run the gate - should pass because the test kills the only negate-if
    proc = _gate(root)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    (r,) = _verdicts(proc).values()
    assert r["verdict"] == "OK"


def test_default_arg_with_mutable_site_is_error(tmp_path):
    """A default argument containing a mutable site (ternary) makes the target uncheckable."""
    product = '''
def some_condition():
    return True

def compute(x, threshold=10 if some_condition() else 20):
    if x > threshold:
        return "high"
    return "low"
'''
    tests = '''
import pytest
from nd_product import compute

@pytest.mark.guards("nd_product:compute", must_kill=["negate-if#1"])
def test_compute():
    assert compute(15) == "high"
    assert compute(5) == "low"
'''
    proc = _gate(_project(tmp_path, tests=tests, product=product))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    (r,) = _verdicts(proc).values()
    assert r["verdict"] == "ERROR"
    assert "default argument" in r["reason"].lower()


def test_decorator_with_mutable_site_is_error(tmp_path):
    """A decorator expression containing a mutable site (ternary) makes the target uncheckable."""
    product = '''
def deco1(fn): return fn
def deco2(fn): return fn

condition = True

@deco1 if condition else deco2
def compute(x):
    if x > 0:
        return "pos"
    return "neg"
'''
    tests = '''
import pytest
from nd_product import compute

@pytest.mark.guards("nd_product:compute", must_kill=["negate-if#1"])
def test_compute():
    assert compute(1) == "pos"
    assert compute(-1) == "neg"
'''
    proc = _gate(_project(tmp_path, tests=tests, product=product))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    (r,) = _verdicts(proc).values()
    assert r["verdict"] == "ERROR"
    assert "decorator" in r["reason"].lower()


METHOD_DEFAULT_SITE = '''
FLAG = True

class K:
    def compute(self, x=(1 if FLAG else 2)):
        if x > 0:
            return "pos"
        return "neg"
'''


def test_method_default_arg_with_mutable_site_is_error(tmp_path):
    """A method is checked too: its indented source must not skip the shape check."""
    tests = '''
import pytest
from nd_product import K

@pytest.mark.guards("nd_product:K.compute", must_kill=["negate-if#1"])
def test_compute():
    assert K().compute() == "pos"
    assert K().compute(-1) == "neg"
'''
    proc = _gate(_project(tmp_path, tests=tests, product=METHOD_DEFAULT_SITE))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    (r,) = _verdicts(proc).values()
    assert r["verdict"] == "ERROR"
    assert "default argument" in r["reason"].lower()


METHOD_DECORATOR_SITE = '''
def deco1(fn): return fn
def deco2(fn): return fn

condition = True

class K:
    @deco1 if condition else deco2
    def compute(self, x):
        if x > 0:
            return "pos"
        return "neg"
'''


def test_method_decorator_with_mutable_site_is_error(tmp_path):
    """A decorated method is checked too: a ternary in its decorator is uncheckable."""
    tests = '''
import pytest
from nd_product import K

@pytest.mark.guards("nd_product:K.compute", must_kill=["negate-if#1"])
def test_compute():
    assert K().compute(1) == "pos"
    assert K().compute(-1) == "neg"
'''
    proc = _gate(_project(tmp_path, tests=tests, product=METHOD_DECORATOR_SITE))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    (r,) = _verdicts(proc).values()
    assert r["verdict"] == "ERROR"
    assert "decorator" in r["reason"].lower()


def test_method_without_a_definition_time_site_still_checks_out(tmp_path):
    """The method check is not over-broad: a plain method is still checked."""
    product = '''
class K:
    def compute(self, x, threshold=10):
        if x > threshold:
            return "pos"
        return "neg"
'''
    tests = '''
import pytest
from nd_product import K

@pytest.mark.guards("nd_product:K.compute", must_kill=["negate-if#1"])
def test_compute():
    assert K().compute(15) == "pos"
    assert K().compute(5) == "neg"
'''
    proc = _gate(_project(tmp_path, tests=tests, product=product))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    (r,) = _verdicts(proc).values()
    assert r["verdict"] == "OK", r
    assert r["mutants"] == {"negate-if#1": "killed"}, r


def test_lru_cache_under_a_wraps_decorator_is_error(tmp_path):
    """lru_cache anywhere in the decorator stack caches results, so the guard is uncheckable."""
    product = '''
import functools

def deco(fn):
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        return fn(*a, **kw)
    return wrapper

@deco
@functools.lru_cache(maxsize=None)
def cached_compute(x):
    if x > 0:
        return "positive"
    return "non-positive"
'''
    tests = '''
import pytest
from nd_product import cached_compute

@pytest.mark.guards("nd_product:cached_compute", must_kill=["negate-if#1"])
def test_cached_compute_positive():
    assert cached_compute(1) == "positive"
'''
    proc = _gate(_project(tmp_path, tests=tests, product=product))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    (r,) = _verdicts(proc).values()
    assert r["verdict"] == "ERROR"
    assert "lru_cache" in r["reason"].lower() or "cache" in r["reason"].lower()


def test_must_kill_stable_across_line_insertion(tmp_path_factory):
    """Stable mutant IDs (kind#ordinal) survive inserting lines above the function."""
    product_v1 = '''
def compute(x):
    if x > 0:
        return "pos"
    return "neg"
'''
    product_v2 = '''
# Added comment line
def compute(x):
    if x > 0:
        return "pos"
    return "neg"
'''
    tests = '''
import pytest
from nd_product import compute

@pytest.mark.guards("nd_product:compute", must_kill=["negate-if#1"])
def test_compute_positive():
    assert compute(1) == "pos"
    assert compute(-1) == "neg"
'''
    # Version 1
    root1 = _project(tmp_path_factory.mktemp("v1"), tests=tests, product=product_v1)
    proc1 = _gate(root1)
    assert proc1.returncode == 0, proc1.stdout + proc1.stderr
    (r1,) = _verdicts(proc1).values()
    assert r1["verdict"] == "OK"

    # Version 2 (line added above)
    root2 = _project(tmp_path_factory.mktemp("v2"), tests=tests, product=product_v2)
    proc2 = _gate(root2)
    assert proc2.returncode == 0, proc2.stdout + proc2.stderr
    (r2,) = _verdicts(proc2).values()
    assert r2["verdict"] == "OK"
