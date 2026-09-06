"""Tests for _CacheAwareStaticFiles — ensures index.html, manifests, and
sw.js never cache, so installed PWAs pick up rebuilds."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tinyagentos.app import _CacheAwareStaticFiles


@pytest.fixture
def static_app(tmp_path):
    (tmp_path / "index.html").write_text("<html></html>")
    (tmp_path / "manifest-desktop.json").write_text("{}")
    (tmp_path / "sw.js").write_text("// stub")
    (tmp_path / "icon-192.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    unrelated = tmp_path / "store-icons"
    unrelated.mkdir()
    (unrelated / "app-with-manifest-in-name.json").write_text("{}")

    app = FastAPI()
    app.mount("/static", _CacheAwareStaticFiles(directory=str(tmp_path)), name="static")
    return TestClient(app)


def test_html_never_cached(static_app):
    r = static_app.get("/static/index.html")
    assert r.status_code == 200
    assert "no-cache" in r.headers["cache-control"]
    assert "no-store" in r.headers["cache-control"]


def test_manifest_never_cached(static_app):
    r = static_app.get("/static/manifest-desktop.json")
    assert r.status_code == 200
    assert "no-cache" in r.headers["cache-control"]


def test_sw_never_cached(static_app):
    r = static_app.get("/static/sw.js")
    assert r.status_code == 200
    assert "no-cache" in r.headers["cache-control"]


def test_icon_cacheable(static_app):
    r = static_app.get("/static/icon-192.png")
    assert r.status_code == 200
    assert "public" in r.headers["cache-control"]
    assert "max-age" in r.headers["cache-control"]


def test_unrelated_json_with_manifest_in_path_is_cacheable(static_app):
    # A JSON file whose filename doesn't start with "manifest" should not
    # be no-cache'd just because "manifest" appears elsewhere.
    r = static_app.get("/static/store-icons/app-with-manifest-in-name.json")
    assert r.status_code == 200
    assert "no-cache" not in r.headers["cache-control"]
