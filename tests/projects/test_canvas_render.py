import io
from pathlib import Path

import pytest
from PIL import Image

from tinyagentos.projects.canvas.render import render_snapshot_png


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
