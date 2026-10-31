"""Host-side render step of the screencast pipeline (media-only).

A TV never renders a taOS web page; it only ever receives pixels. This module
turns ONE taOS view into media, reusing the existing render capability rather
than adding a capture stack:

* ``canvas:<project_id>``: the project canvas, rendered by
  ``projects.canvas.render.render_snapshot_png`` (the same Pillow renderer
  behind ``GET /api/projects/{id}/canvas/snapshot.png``). Static content, so
  the shape is a still image; the sender re-pushes it on
  ``STILL_REFRESH_SECONDS``.

Live video (HLS) is NOT built here: nothing in the tree encodes video yet, and
encode cost on the RK3588 (hardware encoder) and the Pi 5 (software only) has
to be measured on that hardware before a live path is promised.

The full-desktop screenshot (``POST /api/desktop/screenshot``) is deliberately
not a cast view: it captures every open window, which breaks the one-view
scope a cast token promises.
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from tinyagentos.projects.canvas.render import render_snapshot_png

# View kinds a cast can be minted for. Anything else is refused at mint time.
CAST_VIEW_KINDS = ("canvas",)

# How stale a still frame can be before the sender re-pushes it. Surfaced to
# the UI in the cast-start response so the viewer knows the cadence.
STILL_REFRESH_SECONDS = 10


def parse_view(view: str) -> tuple[str, str] | None:
    """Split ``"<kind>:<id>"``; None when malformed."""
    kind, sep, view_id = (view or "").partition(":")
    if not sep or not kind or not view_id or "/" in view_id:
        return None
    return kind, view_id


async def view_exists(app, view_kind: str, view_id: str) -> dict | None:
    """Return the backing record for a view, or None if it does not exist."""
    if view_kind == "canvas":
        return await app.state.project_store.get_project(view_id)
    return None


def _render_canvas_png_bytes(elements: list[dict]) -> bytes:
    with tempfile.TemporaryDirectory(prefix="taos-cast-") as tmp:
        out = render_snapshot_png(
            elements=elements, output_path=Path(tmp) / "still.png",
        )
        return out.read_bytes()


async def render_still_png(app, view_kind: str, view_id: str) -> bytes:
    """Render one view as a PNG still. Raises ValueError for an unknown kind."""
    if view_kind == "canvas":
        elements = await app.state.project_canvas_store.list_elements(view_id)
        return await asyncio.to_thread(_render_canvas_png_bytes, elements)
    raise ValueError(f"unsupported cast view kind: {view_kind}")
