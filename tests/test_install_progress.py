import time
from unittest.mock import patch

import pytest

from tinyagentos.install_progress import (
    INSTALL_PROGRESS_TTL_S,
    InstallProgress,
    InstallProgressStore,
    get_global_store,
)


class TestInstallProgress:
    def test_default_state_is_queued(self):
        p = InstallProgress(install_id="abc", app_id="myapp", target_remote=None)
        assert p.state == "queued"

    def test_percent_none_when_bytes_total_zero(self):
        p = InstallProgress(install_id="a", app_id="b", target_remote=None)
        assert p.bytes_total == 0
        assert p.percent is None

    def test_percent_none_when_bytes_total_negative(self):
        p = InstallProgress(install_id="a", app_id="b", target_remote=None, bytes_total=-1)
        assert p.percent is None

    def test_percent_calculation(self):
        p = InstallProgress(
            install_id="a", app_id="b", target_remote=None,
            bytes_downloaded=50, bytes_total=100,
        )
        assert p.percent == 50.0

    def test_percent_capped_at_100(self):
        p = InstallProgress(
            install_id="a", app_id="b", target_remote=None,
            bytes_downloaded=200, bytes_total=100,
        )
        assert p.percent == 100.0

    def test_to_dict_contains_all_fields(self):
        p = InstallProgress(
            install_id="i1", app_id="app1", target_remote="remote1",
            state="downloading", bytes_downloaded=25, bytes_total=100,
            detail="fetching",
        )
        d = p.to_dict()
        assert d["install_id"] == "i1"
        assert d["app_id"] == "app1"
        assert d["target_remote"] == "remote1"
        assert d["state"] == "downloading"
        assert d["bytes_downloaded"] == 25
        assert d["bytes_total"] == 100
        assert d["percent"] == 25.0
        assert d["detail"] == "fetching"
        assert d["error"] is None
        assert d["finished_at"] is None
        assert "started_at" in d
        assert "updated_at" in d

    def test_to_dict_with_finished_entry(self):
        p = InstallProgress(install_id="x", app_id="y", target_remote=None)
        p.finished_at = 1234567890.0
        p.state = "installed"
        d = p.to_dict()
        assert d["finished_at"] == 1234567890.0
        assert d["state"] == "installed"


class TestInstallProgressStore:
    def _store(self):
        return InstallProgressStore()

    def test_start_returns_entry_with_queued_state(self):
        s = self._store()
        entry = s.start("myapp")
        assert entry.state == "queued"
        assert entry.app_id == "myapp"
        assert entry.target_remote is None
        assert len(entry.install_id) > 0

    def test_start_returns_entry_with_defaults(self, monkeypatch):
        fake_id = "abc123"
        import types
        monkeypatch.setattr(
            "tinyagentos.install_progress.uuid",
            types.SimpleNamespace(uuid4=lambda: types.SimpleNamespace(hex=fake_id)),
        )
        store = self._store()
        entry = store.start("myapp", "myremote")
        assert entry.install_id == fake_id
        assert entry.app_id == "myapp"
        assert entry.target_remote == "myremote"
        assert entry.state == "queued"
        assert entry.bytes_downloaded == 0
        assert entry.bytes_total == 0

    def test_start_with_target_remote(self):
        s = self._store()
        entry = s.start("myapp", target_remote="http://example.com")
        assert entry.target_remote == "http://example.com"

    def test_start_prunes_stale_entries(self):
        s = self._store()
        entry = s.start("old")
        s.finish(entry.install_id, success=True)
        # Force finished_at to be older than TTL
        entry.finished_at = time.time() - INSTALL_PROGRESS_TTL_S - 1
        # Starting a new entry should prune the stale one
        s.start("new")
        assert s.get(entry.install_id) is None

    def test_get_returns_entry(self):
        s = self._store()
        entry = s.start("app")
        fetched = s.get(entry.install_id)
        assert fetched is not None
        assert fetched.install_id == entry.install_id

    def test_get_returns_none_for_unknown_id(self):
        s = self._store()
        assert s.get("nonexistent") is None

    def test_get_prunes_stale(self):
        s = self._store()
        entry = s.start("app")
        s.finish(entry.install_id, success=True)
        entry.finished_at = time.time() - INSTALL_PROGRESS_TTL_S - 1
        assert s.get(entry.install_id) is None

    def test_update_state(self):
        s = self._store()
        entry = s.start("app")
        s.update(entry.install_id, state="downloading")
        assert s.get(entry.install_id).state == "downloading"

    def test_update_bytes(self):
        s = self._store()
        entry = s.start("app")
        s.update(entry.install_id, bytes_downloaded=1024, bytes_total=4096)
        fetched = s.get(entry.install_id)
        assert fetched.bytes_downloaded == 1024
        assert fetched.bytes_total == 4096

    def test_update_detail(self):
        s = self._store()
        entry = s.start("app")
        s.update(entry.install_id, detail="downloading model.bin")
        assert s.get(entry.install_id).detail == "downloading model.bin"

    def test_update_error(self):
        s = self._store()
        entry = s.start("app")
        s.update(entry.install_id, error="connection refused")
        assert s.get(entry.install_id).error == "connection refused"

    def test_update_timestamp_changes(self):
        s = self._store()
        entry = s.start("app")
        original_updated = entry.updated_at
        time.sleep(0.01)
        s.update(entry.install_id, state="verifying")
        assert s.get(entry.install_id).updated_at > original_updated

    def test_update_unknown_id_is_noop(self):
        s = self._store()
        s.update("no-such-id", state="downloading")

    def test_update_only_provided_fields(self):
        s = self._store()
        entry = s.start("app")
        s.update(entry.install_id, state="downloading", bytes_downloaded=50)
        fetched = s.get(entry.install_id)
        assert fetched.state == "downloading"
        assert fetched.bytes_downloaded == 50
        assert fetched.bytes_total == 0
        assert fetched.detail == ""

    def test_finish_success(self):
        s = self._store()
        entry = s.start("app")
        s.finish(entry.install_id, success=True)
        fetched = s.get(entry.install_id)
        assert fetched.state == "installed"
        assert fetched.finished_at is not None
        assert fetched.updated_at == fetched.finished_at

    def test_finish_failure(self):
        s = self._store()
        entry = s.start("app")
        s.finish(entry.install_id, success=False, error="disk full")
        fetched = s.get(entry.install_id)
        assert fetched.state == "failed"
        assert fetched.error == "disk full"
        assert fetched.finished_at is not None

    def test_finish_with_detail(self):
        s = self._store()
        entry = s.start("app")
        s.finish(entry.install_id, success=False, detail="cleanup done")
        fetched = s.get(entry.install_id)
        assert fetched.detail == "cleanup done"

    def test_finish_unknown_id_is_noop(self):
        s = self._store()
        s.finish("no-such-id", success=True)

    def test_list_by_app_returns_matching_newest_first(self):
        s = self._store()
        e1 = s.start("app1")
        time.sleep(0.01)
        e2 = s.start("app1")
        time.sleep(0.01)
        s.start("other_app")
        results = s.list_by_app("app1")
        assert len(results) == 2
        assert results[0].install_id == e2.install_id
        assert results[1].install_id == e1.install_id

    def test_list_by_app_excludes_other_apps(self):
        s = self._store()
        s.start("other")
        results = s.list_by_app("myapp")
        assert results == []

    def test_list_by_app_prunes_stale(self):
        s = self._store()
        stale = s.start("myapp")
        s.finish(stale.install_id, success=True)
        stale.finished_at = time.time() - INSTALL_PROGRESS_TTL_S - 1
        results = s.list_by_app("myapp")
        assert results == []

    def test_list_all_returns_all_newest_first(self):
        s = self._store()
        e1 = s.start("a")
        time.sleep(0.01)
        e2 = s.start("b")
        time.sleep(0.01)
        e3 = s.start("c")
        results = s.list_all()
        assert [r.install_id for r in results] == [e3.install_id, e2.install_id, e1.install_id]

    def test_list_all_prunes_stale(self):
        s = self._store()
        stale = s.start("a")
        s.finish(stale.install_id, success=True)
        stale.finished_at = time.time() - INSTALL_PROGRESS_TTL_S - 1
        results = s.list_all()
        assert results == []

    def test_prune_keeps_recent_finished(self):
        s = self._store()
        entry = s.start("app")
        s.finish(entry.install_id, success=True)
        # finished_at is recent, should NOT be pruned
        assert s.get(entry.install_id) is not None

    def test_prune_keeps_unfinished(self):
        s = self._store()
        entry = s.start("app")
        # Not finished, should not be pruned even if very old
        entry.started_at = 0.0
        entry.updated_at = 0.0
        assert s.get(entry.install_id) is not None

    def test_multiple_terminal_states(self):
        s = self._store()
        e1 = s.start("app")
        e2 = s.start("app")
        e3 = s.start("app")
        s.finish(e1.install_id, success=True)
        s.finish(e2.install_id, success=False, error="oops")
        s.update(e3.install_id, state="cancelled")
        assert s.get(e1.install_id).state == "installed"
        assert s.get(e2.install_id).state == "failed"
        assert s.get(e3.install_id).state == "cancelled"

    def test_full_lifecycle(self):
        s = self._store()
        entry = s.start("model-x", target_remote="http://repo.example.com")
        iid = entry.install_id
        assert s.get(iid).state == "queued"

        s.update(iid, state="downloading", bytes_total=1000)
        s.update(iid, state="downloading", bytes_downloaded=500, detail="halfway")
        mid = s.get(iid)
        assert mid.state == "downloading"
        assert mid.percent == 50.0
        assert mid.detail == "halfway"

        s.update(iid, state="verifying")
        s.update(iid, state="unpacking")
        s.update(iid, state="starting")
        s.finish(iid, success=True, detail="ready")
        final = s.get(iid)
        assert final.state == "installed"
        assert final.detail == "ready"
        assert final.finished_at is not None


class TestGetGlobalStore:
    def test_returns_same_instance(self):
        with patch("tinyagentos.install_progress._GLOBAL", None):
            s1 = get_global_store()
            s2 = get_global_store()
            assert s1 is s2

    def test_creates_store_on_first_call(self):
        with patch("tinyagentos.install_progress._GLOBAL", None):
            store = get_global_store()
            assert isinstance(store, InstallProgressStore)
