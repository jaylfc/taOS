import json
from pathlib import Path

import pytest

from tinyagentos.torrent_settings import TorrentSettings, TorrentSettingsStore


# ---------------------------------------------------------------------------
# TorrentSettings dataclass
# ---------------------------------------------------------------------------

class TestTorrentSettings:
    def test_defaults(self):
        s = TorrentSettings()
        assert s.seed_enabled is True
        assert s.upload_rate_limit_kbps == 5000
        assert s.max_active_seeds == 20

    def test_custom_values(self):
        s = TorrentSettings(seed_enabled=False, upload_rate_limit_kbps=1024, max_active_seeds=5)
        assert s.seed_enabled is False
        assert s.upload_rate_limit_kbps == 1024
        assert s.max_active_seeds == 5

    def test_to_dict(self):
        s = TorrentSettings()
        d = s.to_dict()
        assert d == {"seed_enabled": True, "upload_rate_limit_kbps": 5000, "max_active_seeds": 20}

    def test_to_dict_roundtrip(self):
        s = TorrentSettings(seed_enabled=False, upload_rate_limit_kbps=2048, max_active_seeds=10)
        d = s.to_dict()
        s2 = TorrentSettings(**d)
        assert s == s2


# ---------------------------------------------------------------------------
# TorrentSettingsStore.load
# ---------------------------------------------------------------------------

class TestTorrentSettingsStoreLoad:
    def test_missing_file_returns_defaults(self, tmp_path):
        store = TorrentSettingsStore(tmp_path / "nonexistent" / "torrent_settings.json")
        s = store.load()
        assert s == TorrentSettings()

    def test_existing_file_loaded(self, tmp_path):
        path = tmp_path / "torrent_settings.json"
        path.write_text(json.dumps({
            "seed_enabled": False,
            "upload_rate_limit_kbps": 1024,
            "max_active_seeds": 5,
        }))
        store = TorrentSettingsStore(path)
        s = store.load()
        assert s.seed_enabled is False
        assert s.upload_rate_limit_kbps == 1024
        assert s.max_active_seeds == 5

    def test_partial_file_fills_defaults(self, tmp_path):
        path = tmp_path / "torrent_settings.json"
        path.write_text(json.dumps({"seed_enabled": False}))
        store = TorrentSettingsStore(path)
        s = store.load()
        assert s.seed_enabled is False
        assert s.upload_rate_limit_kbps == 5000
        assert s.max_active_seeds == 20

    def test_empty_json_object_returns_defaults(self, tmp_path):
        path = tmp_path / "torrent_settings.json"
        path.write_text("{}")
        store = TorrentSettingsStore(path)
        s = store.load()
        assert s == TorrentSettings()

    def test_corrupt_json_returns_defaults(self, tmp_path):
        path = tmp_path / "torrent_settings.json"
        path.write_text("not{{{json")
        store = TorrentSettingsStore(path)
        s = store.load()
        assert s == TorrentSettings()

    def test_extra_fields_ignored(self, tmp_path):
        path = tmp_path / "torrent_settings.json"
        path.write_text(json.dumps({
            "seed_enabled": True,
            "upload_rate_limit_kbps": 5000,
            "max_active_seeds": 20,
            "extra_field": "ignored",
        }))
        store = TorrentSettingsStore(path)
        s = store.load()
        assert s == TorrentSettings()

    def test_string_numeric_values_coerced(self, tmp_path):
        path = tmp_path / "torrent_settings.json"
        path.write_text(json.dumps({
            "upload_rate_limit_kbps": "2048",
            "max_active_seeds": "10",
        }))
        store = TorrentSettingsStore(path)
        s = store.load()
        assert s.upload_rate_limit_kbps == 2048
        assert s.max_active_seeds == 10

    def test_bool_coercion(self, tmp_path):
        path = tmp_path / "torrent_settings.json"
        path.write_text(json.dumps({"seed_enabled": 0}))
        store = TorrentSettingsStore(path)
        s = store.load()
        assert s.seed_enabled is False

        path.write_text(json.dumps({"seed_enabled": 1}))
        s = store.load()
        assert s.seed_enabled is True


# ---------------------------------------------------------------------------
# TorrentSettingsStore.save
# ---------------------------------------------------------------------------

class TestTorrentSettingsStoreSave:
    def test_save_writes_json(self, tmp_path):
        path = tmp_path / "torrent_settings.json"
        store = TorrentSettingsStore(path)
        s = TorrentSettings(seed_enabled=False, upload_rate_limit_kbps=2048, max_active_seeds=10)
        store.save(s)
        raw = json.loads(path.read_text())
        assert raw == {"seed_enabled": False, "upload_rate_limit_kbps": 2048, "max_active_seeds": 10}

    def test_save_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "deep" / "nested" / "dir" / "torrent_settings.json"
        store = TorrentSettingsStore(path)
        store.save(TorrentSettings())
        assert path.exists()

    def test_save_overwrites_existing(self, tmp_path):
        path = tmp_path / "torrent_settings.json"
        path.write_text(json.dumps({
            "seed_enabled": True,
            "upload_rate_limit_kbps": 5000,
            "max_active_seeds": 20,
        }))
        store = TorrentSettingsStore(path)
        s = TorrentSettings(seed_enabled=False, upload_rate_limit_kbps=100, max_active_seeds=1)
        store.save(s)
        loaded = store.load()
        assert loaded == s

    def test_save_then_load_roundtrip(self, tmp_path):
        path = tmp_path / "torrent_settings.json"
        store = TorrentSettingsStore(path)
        original = TorrentSettings(seed_enabled=False, upload_rate_limit_kbps=9999, max_active_seeds=42)
        store.save(original)
        loaded = store.load()
        assert loaded == original

    def test_save_default_settings(self, tmp_path):
        path = tmp_path / "torrent_settings.json"
        store = TorrentSettingsStore(path)
        store.save(TorrentSettings())
        loaded = store.load()
        assert loaded == TorrentSettings()
