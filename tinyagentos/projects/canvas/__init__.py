"""Per-project Excalidraw canvas board.

See docs/superpowers/specs/2026-04-28-projects-canvas-board-design.md.
"""
from tinyagentos.projects.canvas.store import (
    CanvasPermissionError,
    ProjectCanvasStore,
)

__all__ = ["ProjectCanvasStore", "CanvasPermissionError"]
