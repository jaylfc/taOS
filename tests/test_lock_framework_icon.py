"""Framework badge URLs are versioned by the icon file's mtime.

A replaced icon used to keep its URL, and the kiosk's disk cache (which
outlives a browser restart) went on showing the old one: the Nous wordmark
after the Hermes mascot had shipped. The URL now carries ``?v=<mtime>``.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

import tinyagentos.routes.auth as auth

ROOT = Path(auth.__file__).resolve().parent.parent.parent


@pytest.mark.parametrize("fw, ext", [
    ("hermes", "webp"), ("grok", "svg"), ("openai", "svg"), ("deepseek", "svg"),
])
def test_a_shipped_badge_url_carries_the_files_mtime(fw, ext):
    url = auth._framework_icon(fw)
    m = re.fullmatch(r"/static/store-icons(?:/brands)?/%s\.%s\?v=(\d+)" % (fw, ext), url)
    assert m, url
    path = ROOT / url[1:].split("?", 1)[0]
    assert int(m.group(1)) == int(path.stat().st_mtime), url


def test_replacing_the_icon_changes_the_url():
    """The point of the query: a new file is a new URL, so no cache can serve
    the old picture under it."""
    url = auth._framework_icon("hermes")
    path = ROOT / url[1:].split("?", 1)[0]
    st = path.stat()
    try:
        os.utime(path, (st.st_atime, st.st_mtime + 1000))
        assert auth._framework_icon("hermes") != url
    finally:
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert auth._framework_icon("hermes") == url


def test_no_icon_means_no_url():
    assert auth._framework_icon("no-such-framework") == ""
    assert auth._framework_icon("") == ""
