import asyncio
import pytest
from tinyagentos.auto_update import (
    is_valid_branch_name,
    resolve_tracked_branch,
    PREF_NAMESPACE,
)


class _FakeSettings:
    def __init__(self, prefs):
        self._prefs = prefs
    async def get_preference(self, user_id, namespace):
        assert namespace == PREF_NAMESPACE
        return dict(self._prefs)


@pytest.fixture
def repo(tmp_path):
    import subprocess
    r = tmp_path / "repo"; r.mkdir()
    def g(*a): subprocess.run(["git", *a], cwd=r, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    g("init", "-q"); g("config", "user.email", "t@t"); g("config", "user.name", "t")
    g("checkout", "-q", "-b", "master"); (r / "f").write_text("1"); g("add", "."); g("commit", "-qm", "c1")
    return r


def test_returns_pref_branch_when_set(repo):
    s = _FakeSettings({"tracked_branch": "dev"})
    assert asyncio.run(resolve_tracked_branch(s, repo)) == "dev"


def test_falls_back_to_checked_out_branch_when_pref_absent(repo):
    s = _FakeSettings({})
    assert asyncio.run(resolve_tracked_branch(s, repo)) == "master"


def test_ignores_blank_pref(repo):
    s = _FakeSettings({"tracked_branch": ""})
    assert asyncio.run(resolve_tracked_branch(s, repo)) == "master"


def test_tolerates_settings_failure(repo):
    class Boom:
        async def get_preference(self, *a): raise RuntimeError("db down")
    assert asyncio.run(resolve_tracked_branch(Boom(), repo)) == "master"


@pytest.mark.parametrize("name", [
    "master", "dev", "feature/foo", "release-1.2", "user/fix_bug-3",
])
def test_is_valid_branch_name_accepts_plain_refs(name):
    assert is_valid_branch_name(name) is True


@pytest.mark.parametrize("name", [
    "--upload-pack=touch /tmp/pwned",  # flag injection
    "-x", "", "  ", "a b", "a..b", "a~b", "a^b", "a:b", "a?b", "a*b",
    "a[b", "a\\b", "a//b", "/lead", "trail/", "ref.lock", "x@{0}",
    "a\tb", "a\nb", "x.", "a" * 256,
])
def test_is_valid_branch_name_rejects_unsafe(name):
    assert is_valid_branch_name(name) is False


def test_rejects_malformed_stored_pref_and_falls_back(repo):
    # A flag-like value must never reach git argv — fall back to checked-out.
    s = _FakeSettings({"tracked_branch": "--upload-pack=evil"})
    assert asyncio.run(resolve_tracked_branch(s, repo)) == "master"
