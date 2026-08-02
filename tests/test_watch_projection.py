"""Tests for tinyagentos/projects/canvas/watch_projection.py.

Cover: empty list, each kind mapping, non-dict row, non-dict payload, bool z_index,
z ordering, creds-URL domain (red-first vs item 2), file_id inclusion.
"""
from __future__ import annotations

import pytest
from tinyagentos.projects.canvas.watch_projection import build_watch_projection


def test_empty_list():
    assert build_watch_projection([]) == {"version": 1, "elements": []}


def test_text_card():
    el = {"kind": "note", "payload": {"text": "hello"}, "z_index": 10}
    result = build_watch_projection([el])
    assert result["version"] == 1
    assert len(result["elements"]) == 1
    e = result["elements"][0]
    assert e["kind"] == "note"
    assert e["type"] == "text_card"
    assert e["text"] == "hello"
    assert e["z_index"] == 10


def test_text_card_bare_string_payload():
    el = {"kind": "text", "payload": "bare text", "z_index": 5}
    result = build_watch_projection([el])
    assert result["elements"][0]["text"] == "bare text"


def test_text_card_no_text():
    el = {"kind": "note", "payload": {"color": "yellow"}}
    result = build_watch_projection([el])
    assert result["elements"][0]["text"] == ""


def test_link_row():
    el = {
        "kind": "link",
        "payload": {"title": "Example", "url": "https://example.com"},
        "z_index": 3,
    }
    result = build_watch_projection([el])
    e = result["elements"][0]
    assert e["kind"] == "link"
    assert e["type"] == "link_row"
    assert e["title"] == "Example"
    assert e["domain"] == "example.com"
    assert e["z_index"] == 3


def test_link_row_no_url():
    el = {"kind": "link", "payload": {"title": "No URL"}}
    result = build_watch_projection([el])
    assert result["elements"][0]["domain"] == ""


def test_link_row_creds_url():
    # credentials in URL should be stripped (urlparse().hostname, not netloc)
    el = {"kind": "link", "payload": {"url": "https://user:pass@example.com"}}
    result = build_watch_projection([el])
    assert result["elements"][0]["domain"] == "example.com"


def test_image_thumbnail():
    el = {
        "kind": "image",
        "payload": {"alt": "photo", "file_id": "file123"},
        "z_index": 7,
    }
    result = build_watch_projection([el])
    e = result["elements"][0]
    assert e["type"] == "thumbnail"
    assert e["alt"] == "photo"
    assert e["file_id"] == "file123"
    assert e["z_index"] == 7


def test_image_thumbnail_no_file_id():
    el = {"kind": "image", "payload": {"alt": "no file id"}}
    result = build_watch_projection([el])
    assert result["elements"][0]["file_id"] == ""


def test_malformed_row_not_dict():
    result = build_watch_projection(["not a dict"])
    e = result["elements"][0]
    assert e["kind"] == "unknown"
    assert e["type"] == "placeholder"
    assert e["label"] == "malformed element"
    assert e["z_index"] == 0


def test_string_payload_degrades_to_placeholder():
    el = {"kind": "note", "payload": "not a dict"}
    result = build_watch_projection([el])
    # Payload as string is valid - treated as text content
    e = result["elements"][0]
    assert e["type"] == "text_card"
    assert e["text"] == "not a dict"


def test_bool_z_index():
    el = {"kind": "note", "z_index": True}
    result = build_watch_projection([el])
    assert result["elements"][0]["z_index"] == 0


def test_z_ordering():
    el1 = {"kind": "note", "z_index": 20}
    el2 = {"kind": "note", "z_index": 5}
    el3 = {"kind": "note", "z_index": 15}
    result = build_watch_projection([el1, el2, el3])
    assert [e["z_index"] for e in result["elements"]] == [5, 15, 20]


def test_kind_mermaid():
    el = {"kind": "mermaid", "payload": {"source": "graph TD"}}
    result = build_watch_projection([el])
    e = result["elements"][0]
    assert e["kind"] == "mermaid"
    assert e["type"] == "placeholder"
    assert e["label"] == "diagram - open on phone"


def test_kind_user_shape():
    el = {"kind": "user_shape", "payload": {"shape": "circle"}}
    result = build_watch_projection([el])
    e = result["elements"][0]
    assert e["label"] == "user shape"


def test_unknown_kind():
    el = {"kind": "ghost", "payload": {"x": 1}}
    result = build_watch_projection([el])
    e = result["elements"][0]
    assert e["label"] == "ghost"
