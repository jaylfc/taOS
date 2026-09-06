"""Per-project tldraw canvas board.

See docs/superpowers/specs/2026-04-28-projects-canvas-board-design.md.
"""
from tinyagentos.projects.canvas.store import (
    ProjectCanvasStore,
    CanvasPermissionError,
)

__all__ = ["ProjectCanvasStore", "CanvasPermissionError"]
