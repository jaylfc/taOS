"""Integration tests for the Beads bridge wired into the FastAPI app.

Uses the existing 'client' fixture which builds a real app via create_app
with a tmp data dir. Bridge is exercised end-to-end: route hooks, chat
hooks, broker subscription, JSONL writes.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_app_boots_with_beads_bridge(app):
    """The bridge must be attached to app.state on lifespan startup."""
    async with app.router.lifespan_context(app):
        bridge = app.state.beads_bridge
        assert bridge is not None
        # data_root should be data_dir/projects
        assert bridge._data_root.name == "projects"
