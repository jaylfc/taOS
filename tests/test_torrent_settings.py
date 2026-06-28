import json
from pathlib import Path

import pytest

from tinyagentos.torrent_settings import TorrentSettings, TorrentSettingsStore


class TestTorrentSettingsDataclass:
    def test_defaults(self):
        s = TorrentSettings()
        assert s.seed_enabled is True
        assert s.upload_rate_limit_kbps == 5000
        assert s.max_active_seeds == 20

    def test_custom_values(self):
        s = TorrentSettings(seed_enabled=False, upload_rate_limit_kbps=1000, max_active_seeds=5)
        assert s.seed_enabled is False
        assert s.upload_rate_limit_kbps == 1000
        assert s.max_active_seeds == 5

    def test_to_dict(self):
        s = TorrentSettings(seed_enabled=False, upload_rate_limit_kbps=2048, max_active_seeds=10)
        d = s.to_dict()
        assert d == {
            "seed_enabled": False,
            "upload_rate_limit_kbps": 2048,
            "max_active_seeds": 10,
        }

    def test_to_dict_roundtrip(self):
        original = TorrentSettings(seed_enabled=False, upload_rate_limit_kbps=1234, max_active_seeds=7)
        restored = TorrentSettings(**original.to_dict())
        assert restored == original


class TestTorrentSettingsStoreLoad:
    def test_load_missing_file_returns_defaults(self, tmp_path):
        store = TorrentSettingsStore(tmp_path / "nonexistent" / "settings.json")
        s = store.load()
        assert s == TorrentSettings()

    def test_load_returns_defaults_when_file_empty(self, tmp_path):
        p = tmp_path / "settings.json"
        p.write_text("")
        store = TorrentSettingsStore(p)
        s = store.load()
        assert s == TorrentSettings()

    def test_load_returns_defaults_when_file_invalid_json(self, tmp_path):
        p = tmp_path / "settings.json"
        p.write_text("{not valid json")
        store = TorrentSettingsStore(p)
        s = store.load()
        assert s == TorrentSettings()

    def test_load_discard_unknown_keys(self, tmp_path):
        p = tmp_path / "settings.json"
        p.write_text(json.dumps({"seed_enabled": True, "unknown_field": 42}))
        store = TorrentSettingsStore(p)
        s = store.load()
        assert s.seed_enabled is True
        assert s.upload_rate_limit_kbps == 5000
        assert s.max_active_seeds == 20

    def test_load_partial_values_use_defaults_for_rest(self, tmp_path):
        p = tmp_path / "settings.json"
        p.write_text(json.dumps({"seed_enabled": False}))
        store = TorrentSettingsStore(p)
        s = store.load()
        assert s.seed_enabled is False
        assert s.upload_rate_limit_kbps == 5000
        assert s.max_active_seeds == 20

    def test_load_full_values(self, tmp_path):
        p = tmp_path / "settings.json"
        payload = {"seed_enabled": False, "upload_rate_limit_kbps": 8000, "max_active_seeds": 50}
        p.write_text(json.dumps(payload))
        store = TorrentSettingsStore(p)
        s = store.load()
        assert s.seed_enabled is False
        assert s.upload_rate_limit_kbps == 8000
        assert s.max_active_seeds == 50

    def test_load_coerces_string_to_bool_and_int(self, tmp_path):
        p = tmp_path / "settings.json"
        payload = {"seed_enabled": "yes", "upload_rate_limit_kbps": "3000", "max_active_seeds": "15"}
        p.write_text(json.dumps(payload))
        store = TorrentSettingsStore(p)
        s = store.load()
        assert s.seed_enabled is True
        assert s.upload_rate_limit_kbps == 3000
        assert s.max_active_seeds == 15


class TestTorrentSettingsStoreSave:
    def test_save_creates_parent_directories(self, tmp_path):
        p = tmp_path / "deep" / "nested" / "settings.json"
        store = TorrentSettingsStore(p)
        settings = TorrentSettings(seed_enabled=False)
        store.save(settings)
        assert p.exists()

    def test_save_writes_indented_json(self, tmp_path):
        p = tmp_path / "settings.json"
        store = TorrentSettingsStore(p)
        settings = TorrentSettings(upload_rate_limit_kbps=9999)
        store.save(settings)
        raw = json.loads(p.read_text())
        assert raw["upload_rate_limit_kbps"] == 9999
        assert raw["seed_enabled"] is True
        assert raw["max_active_seeds"] == 20

    def test_save_then_load_roundtrip(self, tmp_path):
        p = tmp_path / "settings.json"
        store = TorrentSettingsStore(p)
        original = TorrentSettings(seed_enabled=False, upload_rate_limit_kbps=3000, max_active_seeds=5)
        store.save(original)
        loaded = store.load()
        assert loaded == original


class TestTorrentSettingsStoreSaveThenReloadReturnTrip:
    def test_separate_instances_share_file(self, tmp_path):
        p = tmp_path / "settings.json"
        store_a = TorrentSettingsStore(p)
        store_b = TorrentSettingsStore(p)
        settings = TorrentSettings(seed_enabled=False, upload_rate_limit_kbps=7777, max_active_seeds=3)
        store_a.save(settings)
        loaded = store_b.load()
        assert loaded == settings

    def test_overwrite_existing(self, tmp_path):
        p = tmp_path / "settings.json"
        store = TorrentSettingsStore(p)
        store.save(TorrentSettings(seed_enabled=True))
        store.save(TorrentSettings(seed_enabled=False, upload_rate_limit_kbps=100))
        loaded = store.load()
        assert loaded.seed_enabled is False
        assert loaded.upload_rate_limit_kbps == 100


class TestTorrentSettingsStoreEdgeCases:
    def test_load_truncated_json_returns_defaults(self, tmp_path):
        p = tmp_path / "settings.json"
        p.write_text('{"seed_enabled": true, "upload_rate_limit_kbps":')
        store = TorrentSettingsStore(p)
        s = store.load()
        assert s == TorrentSettings()

    def test_load_coerces_numeric_strings(self, tmp_path):
        p = tmp_path / "settings.json"
        p.write_text(json.dumps({
            "seed_enabled": 0,
            "upload_rate_limit_kbps": "3000",
            "max_active_seeds": "10",
        }))
        store = TorrentSettingsStore(p)
        s = store.load()
        assert s.seed_enabled is False
        assert s.upload_rate_limit_kbps == 3000
        assert s.max_active_seeds == 10

    def test_init_converts_string_path_to_pathlib(self, tmp_path):
        path_str = str(tmp_path / "settings.json")
        store = TorrentSettingsStore(path_str)
        assert isinstance(store.path, Path)

    def test_init_accepts_pathlib_path(self, tmp_path):
        path = tmp_path / "settings.json"
        store = TorrentSettingsStore(path)
        assert store.path == path

    def test_save_writes_indented_json(self, tmp_path):
        p = tmp_path / "settings.json"
        store = TorrentSettingsStore(p)
        store.save(TorrentSettings(seed_enabled=True, upload_rate_limit_kbps=8000, max_active_seeds=50))
        raw = p.read_text()
        parsed = json.loads(raw)
        assert parsed == {"seed_enabled": True, "upload_rate_limit_kbps": 8000, "max_active_seeds": 50}
        assert "  " in raw  # indent=2 produces indentation
