import io
from pathlib import Path

import pytest
from PIL import Image

from tinyagentos.projects.canvas.render import (
    render_snapshot_png,
    _label_for,
    _KIND_FILL,
    _KIND_OUTLINE,
)


def test_render_empty_canvas_returns_blank_png(tmp_path):
    out = tmp_path / "snap.png"
    elements: list[dict] = []
    render_snapshot_png(elements=elements, output_path=out)
    assert out.exists()
    img = Image.open(out)
    assert img.size[0] > 0 and img.size[1] > 0


def test_render_with_note(tmp_path):
    out = tmp_path / "snap.png"
    elements = [{
        "id": "cve-1", "kind": "note",
        "x": 50, "y": 50, "w": 200, "h": 100, "rotation": 0, "z_index": 0,
        "author_id": "u", "author_kind": "user",
        "payload": {"text": "hello world", "color": "yellow"},
    }]
    render_snapshot_png(elements=elements, output_path=out)
    assert out.exists()
    img = Image.open(out)
    assert img.size[0] >= 600
    assert img.size[1] >= 400


# ---------------------------------------------------------------------------
# Ideas-board kinds (#68): distinct colours + meaningful labels in the
# vision-agent snapshot.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["text", "mermaid", "flowchart", "mindmap_edge"])
def test_ideas_board_kinds_have_distinct_colours(kind):
    # Each new kind must have its own colour, not fall back to user_shape's.
    assert kind in _KIND_FILL
    assert kind in _KIND_OUTLINE
    assert _KIND_FILL[kind] != _KIND_FILL["user_shape"]


def test_label_for_text_uses_payload_text():
    el = {"kind": "text", "payload": {"text": "an idea"}}
    assert _label_for(el) == "an idea"


def test_label_for_mermaid_uses_first_source_line():
    el = {"kind": "mermaid", "payload": {"source": "graph TD;\nA-->B"}}
    assert _label_for(el) == "graph TD;"


def test_label_for_mindmap_edge_shows_endpoints():
    el = {"kind": "mindmap_edge", "payload": {"from": "a", "to": "b"}}
    assert _label_for(el) == "a -> b"


def test_label_for_mindmap_edge_without_endpoints_falls_back():
    el = {"kind": "mindmap_edge", "payload": {}}
    assert _label_for(el) == "mindmap_edge"


def test_render_ideas_board_kinds_smoke(tmp_path):
    out = tmp_path / "snap.png"
    elements = [
        {"id": "t", "kind": "text", "x": 0, "y": 0, "w": 120, "h": 60,
         "rotation": 0, "z_index": 0, "author_id": "u", "author_kind": "user",
         "payload": {"text": "idea"}},
        {"id": "m", "kind": "mermaid", "x": 130, "y": 0, "w": 200, "h": 120,
         "rotation": 0, "z_index": 0, "author_id": "u", "author_kind": "user",
         "payload": {"source": "graph TD; X-->Y"}},
        {"id": "e", "kind": "mindmap_edge", "x": 0, "y": 130, "w": 150, "h": 20,
         "rotation": 0, "z_index": 0, "author_id": "u", "author_kind": "user",
         "payload": {"from": "t", "to": "m"}},
    ]
    render_snapshot_png(elements=elements, output_path=out)
    assert out.exists()
    assert Image.open(out).size[0] > 0
