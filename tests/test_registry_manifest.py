# tests/test_registry_manifest.py
"""Schema validation at the catalog boundary.

App manifests come from an externally-authored git repo (see
tinyagentos/catalog_sync.py) and were previously parsed with ``data.get(...)
`` defaults and no type checks, so a single malformed entry broke the whole
store listing at install time. These tests pin the boundary contract: wrong
types are rejected with a named ``ValidationError`` *at load time*, and one
bad manifest does not abort the rest of the catalog.
"""
from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

from tinyagentos.registry import AppManifest, AppRegistry


# -- helpers ----------------------------------------------------------------

def _write_manifest(app_dir, manifest: dict) -> None:
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "manifest.yaml").write_text(yaml.dump(manifest))


def _good_manifest(mid: str = "good-app") -> dict:
    return {
        "id": mid,
        "name": "Good App",
        "type": "agent-framework",
        "version": "1.0.0",
    }


# -- RED tests --------------------------------------------------------------

class TestBoundaryRejectsWrongTypes:
    def test_string_requires_is_rejected(self, tmp_path, caplog):
        """A string where ``requires`` should be a mapping would otherwise
        travel into the install path and raise AttributeError deep in
        install code, with no indication which manifest was malformed."""
        import logging

        bad = _good_manifest("bad-requires")
        bad["requires"] = "ollama"  # YAML string, not a mapping
        d = tmp_path / "agents" / "bad-requires"
        _write_manifest(d, bad)
        with caplog.at_level(logging.WARNING):
            with pytest.raises(ValidationError) as exc:
                AppManifest.from_file(d / "manifest.yaml")
        # ValidationError names the offending field...
        assert "requires" in str(exc.value)
        # ...and the load-time log names the offending manifest by id and path.
        assert any("bad-requires" in r.getMessage() for r in caplog.records)

    def test_string_context_window_is_coerced_or_rejected(self, tmp_path):
        """A string-valued ``context_window`` flowed into model-selection
        arithmetic. Either coerce to int or reject; a silent string is
        not acceptable."""
        bad = _good_manifest("bad-window")
        bad["type"] = "model"
        bad["context_window"] = "8192"  # YAML string
        d = tmp_path / "models" / "bad-window"
        _write_manifest(d, bad)
        try:
            m = AppManifest.from_file(d / "manifest.yaml")
        except ValidationError:
            return  # rejected is fine
        # If coerced, it must be the int 8192, not the string.
        assert m.context_window == 8192
        assert isinstance(m.context_window, int)


class TestCatalogResilience:
    def test_one_bad_manifest_does_not_abort_catalog(self, tmp_path):
        """A manifest missing the required ``id`` field used to raise bare
        ``KeyError`` from from_dict, which the loop swallowed -- but every
        subsequent manifest in the same type_dir was loaded into the same
        loop, and a later exception propagated. Per the audit, one bad
        manifest must NOT take down the store listing: the rest load."""
        catalog = tmp_path
        agents = catalog / "agents"
        # Good manifest before the bad one.
        _write_manifest(agents / "alpha", _good_manifest("alpha"))
        # Bad manifest: missing required id field.
        bad = agents / "broken"
        bad.mkdir(parents=True, exist_ok=True)
        (bad / "manifest.yaml").write_text(
            yaml.dump({"name": "Broken", "type": "agent-framework", "version": "1.0.0"})
        )
        # Good manifest after the bad one.
        _write_manifest(agents / "zeta", _good_manifest("zeta"))

        reg = AppRegistry(catalog_dir=catalog, installed_path=tmp_path / "installed.json")
        apps = reg.list_available()
        ids = {a.id for a in apps}
        # Both good manifests are listed, the bad one is skipped.
        assert "alpha" in ids
        assert "zeta" in ids
        assert "broken" not in ids


class TestWellFormedManifestStillLoads:
    def test_well_formed_manifest_loads(self, tmp_path):
        """Sanity: a well-formed manifest still loads cleanly through the
        new pydantic model."""
        d = tmp_path / "agents" / "happy"
        _write_manifest(d, _good_manifest("happy"))
        m = AppManifest.from_file(d / "manifest.yaml")
        assert m.id == "happy"
        assert m.type == "agent-framework"