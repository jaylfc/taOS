import asyncio
import subprocess
from pathlib import Path
import pytest
from tinyagentos.update_runner import switch_to_branch


def _g(cwd, *a):
    subprocess.run(["git", *a], cwd=cwd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _sha(cwd, ref="HEAD"):
    return subprocess.run(["git", "rev-parse", ref], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def remote_and_clone(tmp_path):
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"; seed.mkdir()
    _g(seed, "init", "-q"); _g(seed, "config", "user.email", "t@t"); _g(seed, "config", "user.name", "t")
    _g(seed, "checkout", "-q", "-b", "master"); (seed / "f").write_text("m1"); _g(seed, "add", "."); _g(seed, "commit", "-qm", "m1")
    _g(seed, "checkout", "-q", "-b", "dev"); (seed / "d").write_text("d1"); _g(seed, "add", "."); _g(seed, "commit", "-qm", "d1")
    _g(seed, "checkout", "-q", "master")
    subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(origin)], check=True)
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    _g(clone, "config", "user.email", "t@t"); _g(clone, "config", "user.name", "t")
    _g(clone, "checkout", "-q", "master")
    return clone


def test_switches_to_existing_branch(remote_and_clone):
    clone = remote_and_clone
    res = asyncio.run(switch_to_branch("dev", clone))
    cur = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=clone, capture_output=True, text=True).stdout.strip()
    assert cur == "dev"
    assert (clone / "d").exists()
    assert res.new_sha == _sha(clone)
    assert res.ok is True


def test_stashes_dirty_tree_and_tags_recovery(remote_and_clone):
    clone = remote_and_clone
    (clone / "f").write_text("dirty-edit")
    res = asyncio.run(switch_to_branch("dev", clone))
    cur = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=clone, capture_output=True, text=True).stdout.strip()
    assert cur == "dev"
    tags = subprocess.run(["git", "tag"], cwd=clone, capture_output=True, text=True).stdout
    assert "taos-pre-switch-" in tags
    # The whole point of stashing is that the dirty edit SURVIVES the switch.
    # Without asserting the restored content, a broken stash-pop would pass.
    assert res.stash_restored is True
    assert (clone / "f").read_text() == "dirty-edit"


def test_fetch_failure_is_non_destructive(tmp_path):
    r = tmp_path / "r"; r.mkdir()
    _g(r, "init", "-q"); _g(r, "config", "user.email", "t@t"); _g(r, "config", "user.name", "t")
    _g(r, "checkout", "-q", "-b", "master"); (r / "f").write_text("1"); _g(r, "add", "."); _g(r, "commit", "-qm", "c1")
    before = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=r, capture_output=True, text=True).stdout.strip()
    res = asyncio.run(switch_to_branch("dev", r))
    after = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=r, capture_output=True, text=True).stdout.strip()
    assert after == before == "master"
    assert res.ok is False
    assert "Fetch failed" in res.message or res.new_sha == res.previous_sha
