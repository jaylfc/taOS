"""Unit tests for tinyagentos/install_progress.py."""

import time
from unittest.mock import patch

import pytest

from tinyagentos.install_progress import (
    INSTALL_PROGRESS_TTL_S,
    InstallProgress,
    InstallProgressStore,
    get_global_store,
)


# ---------------------------------------------------------------------------
# InstallProgress dataclass
# ---------------------------------------------------------------------------


class TestInstallProgress:
    def _make(self, **kw):
        defaults = dict(
            install_id="i1",
            app_id="myapp",
            target_remote="http://r",
        )
        defaults.update(kw)
        return InstallProgress(**defaults)

    def test_defaults(self):
        entry = self._make()
        assert entry.state == "queued"
        assert entry.bytes_downloaded == 0
        assert entry.bytes_total == 0
        assert entry.finished_at is None
        assert entry.error is None
        assert entry.detail == ""

    def test_percent_none_when_total_zero(self):
        entry = self._make(bytes_total=0, bytes_downloaded=5)
        assert entry.percent is None

    def test_percent_none_when_total_negative(self):
        entry = self._make(bytes_total=-1, bytes_downloaded=5)
        assert entry.percent is None

    def test_percent_half(self):
        entry = self._make(bytes_total=200, bytes_downloaded=100)
        assert entry.percent == pytest.approx(50.0)

    def test_percent_caps_at_100(self):
        entry = self._make(bytes_total=100, bytes_downloaded=200)
        assert entry.percent == 100.0

    def test_percent_zero(self):
        entry = self._make(bytes_total=100, bytes_downloaded=0)
        assert entry.percent == 0.0

    def test_to_dict_shape(self):
        entry = self._make(
            state="downloading",
            bytes_total=1000,
            bytes_downloaded=250,
            detail="fetching model",
        )
        d = entry.to_dict()
        assert d["install_id"] == "i1"
        assert d["app_id"] == "myapp"
        assert d["target_remote"] == "http://r"
        assert d["state"] == "downloading"
        assert d["bytes_downloaded"] == 250
        assert d["bytes_total"] == 1000
        assert d["percent"] == pytest.approx(25.0)
        assert d["detail"] == "fetching model"
        assert d["error"] is None
        assert d["finished_at"] is None
        assert "started_at" in d
        assert "updated_at" in d

    def test_to_dict_with_error(self):
        entry = self._make(state="failed", error="boom")
        d = entry.to_dict()
        assert d["error"] == "boom"
        assert d["state"] == "failed"

    def test_to_dict_finished_at_preserved(self):
        ts = time.time()
        entry = self._make(finished_at=ts)
        assert entry.to_dict()["finished_at"] == ts


# ---------------------------------------------------------------------------
# InstallProgressStore
# ---------------------------------------------------------------------------


class TestInstallProgressStore:
    def test_start_returns_entry_with_defaults(self):
        store = InstallProgressStore()
        entry = store.start("app1")
        assert entry.app_id == "app1"
        assert entry.target_remote is None
        assert entry.state == "queued"
        assert len(entry.install_id) == 32  # uuid4 hex

    def test_start_with_remote(self):
        store = InstallProgressStore()
        entry = store.start("app1", target_remote="http://host")
        assert entry.target_remote == "http://host"

    def test_start_generates_unique_ids(self):
        store = InstallProgressStore()
        e1 = store.start("a")
        e2 = store.start("a")
        assert e1.install_id != e2.install_id

    def test_get_returns_entry(self):
        store = InstallProgressStore()
        entry = store.start("app1")
        got = store.get(entry.install_id)
        assert got is entry

    def test_get_missing_returns_none(self):
        store = InstallProgressStore()
        assert store.get("nope") is None

    def test_update_state(self):
        store = InstallProgressStore()
        entry = store.start("app1")
        store.update(entry.install_id, state="downloading")
        assert entry.state == "downloading"

    def test_update_bytes(self):
        store = InstallProgressStore()
        entry = store.start("app1")
        store.update(entry.install_id, bytes_downloaded=50, bytes_total=200)
        assert entry.bytes_downloaded == 50
        assert entry.bytes_total == 200

    def test_update_detail(self):
        store = InstallProgressStore()
        entry = store.start("app1")
        store.update(entry.install_id, detail="working")
        assert entry.detail == "working"

    def test_update_error(self):
        store = InstallProgressStore()
        entry = store.start("app1")
        store.update(entry.install_id, error="oops")
        assert entry.error == "oops"

    def test_update_missing_id_noop(self):
        store = InstallProgressStore()
        # must not raise
        store.update("ghost", state="downloading")

    def test_update_touches_updated_at(self):
        store = InstallProgressStore()
        entry = store.start("app1")
        original = entry.updated_at
        time.sleep(0.01)
        store.update(entry.install_id, detail="x")
        assert entry.updated_at > original

    def test_finish_success(self):
        store = InstallProgressStore()
        entry = store.start("app1")
        store.finish(entry.install_id, success=True)
        assert entry.state == "installed"
        assert entry.finished_at is not None
        assert entry.updated_at == entry.finished_at

    def test_finish_failure(self):
        store = InstallProgressStore()
        entry = store.start("app1")
        store.finish(entry.install_id, success=False, error="boom")
        assert entry.state == "failed"
        assert entry.error == "boom"
        assert entry.finished_at is not None

    def test_finish_with_detail(self):
        store = InstallProgressStore()
        entry = store.start("app1")
        store.finish(entry.install_id, success=True, detail="all done")
        assert entry.detail == "all done"

    def test_finish_missing_id_noop(self):
        store = InstallProgressStore()
        store.finish("ghost", success=True)  # must not raise

    def test_list_by_app(self):
        store = InstallProgressStore()
        e1 = store.start("app1")
        e2 = store.start("app2")
        e3 = store.start("app1")
        results = store.list_by_app("app1")
        ids = [e.install_id for e in results]
        assert e1.install_id in ids
        assert e3.install_id in ids
        assert e2.install_id not in ids

    def test_list_by_app_newest_first(self):
        store = InstallProgressStore()
        e1 = store.start("app1")
        time.sleep(0.01)
        e2 = store.start("app1")
        results = store.list_by_app("app1")
        assert results[0] is e2
        assert results[1] is e1

    def test_list_by_app_empty(self):
        store = InstallProgressStore()
        assert store.list_by_app("nope") == []

    def test_list_all(self):
        store = InstallProgressStore()
        e1 = store.start("a")
        e2 = store.start("b")
        all_entries = store.list_all()
        ids = {e.install_id for e in all_entries}
        assert e1.install_id in ids
        assert e2.install_id in ids

    def test_list_all_newest_first(self):
        store = InstallProgressStore()
        e1 = store.start("a")
        time.sleep(0.01)
        e2 = store.start("b")
        results = store.list_all()
        assert results[0] is e2
        assert results[1] is e1

    # --- pruning ---

    def test_prune_removes_stale_finished_entries(self):
        store = InstallProgressStore()
        entry = store.start("app1")
        store.finish(entry.install_id, success=True)
        # Force finished_at well into the past
        entry.finished_at = time.time() - INSTALL_PROGRESS_TTL_S - 10
        assert store.get(entry.install_id) is None

    def test_prune_keeps_recent_finished_entries(self):
        store = InstallProgressStore()
        entry = store.start("app1")
        store.finish(entry.install_id, success=True)
        # finished_at is "now", well within TTL
        assert store.get(entry.install_id) is not None

    def test_prune_keeps_unfinished_entries(self):
        store = InstallProgressStore()
        entry = store.start("app1")
        # No finished_at, even if started long ago
        entry.started_at = 0
        entry.updated_at = 0
        assert store.get(entry.install_id) is not None

    def test_prune_runs_on_start(self):
        store = InstallProgressStore()
        stale = store.start("old")
        store.finish(stale.install_id, success=True)
        stale.finished_at = time.time() - INSTALL_PROGRESS_TTL_S - 10
        # Next start triggers prune
        store.start("new")
        assert store.get(stale.install_id) is None

    def test_prune_runs_on_list_all(self):
        store = InstallProgressStore()
        stale = store.start("old")
        store.finish(stale.install_id, success=True)
        stale.finished_at = time.time() - INSTALL_PROGRESS_TTL_S - 10
        results = store.list_all()
        ids = [e.install_id for e in results]
        assert stale.install_id not in ids


# ---------------------------------------------------------------------------
# get_global_store (module-level singleton)
# ---------------------------------------------------------------------------


class TestGetGlobalStore:
    def test_returns_same_instance(self):
        from tinyagentos import install_progress as mod
        mod._GLOBAL = None
        s1 = get_global_store()
        s2 = get_global_store()
        assert s1 is s2

    def test_returns_install_progress_store(self):
        from tinyagentos import install_progress as mod
        mod._GLOBAL = None
        s = get_global_store()
        assert isinstance(s, InstallProgressStore)

    def test_uses_existing_global(self):
        from tinyagentos import install_progress as mod
        mod._GLOBAL = None
        s1 = get_global_store()
        s1.start("x")
        s2 = get_global_store()
        assert s2 is s1
        assert len(s2.list_all()) == 1
        # cleanup
        mod._GLOBAL = None
