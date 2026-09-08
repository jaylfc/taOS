"""RED test for SpanStoreRegistry eviction - must fail on current code.

This test demonstrates that the SpanStoreRegistry never evicts entries
and grows indefinitely, which violates the bounded LRU requirement.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tinyagentos.otel.span_store import SpanStoreRegistry


@pytest.mark.asyncio
async def test_span_store_registry_evicts_lru(tmp_path: Path) -> None:
    """RED test: registry must evict LRU entries when over max_size.
    
    The current implementation never evicts, so creating more stores
    than max_size will cause unbounded growth. This test will FAIL
    before the fix is applied.
    """
    data_dir = tmp_path
    registry = SpanStoreRegistry(data_dir, max_size=3)
    
    # Create 5 different stores - registry should evict oldest when over max_size
    stores = []
    for i in range(5):
        slug = f"agent-{i}"
        store = await registry.get(slug)
        stores.append(store)
    
    # Check that registry doesn't have more than max_size entries
    # This will FAIL with the current implementation
    active_stores = len(registry._stores)
    assert active_stores <= 3, f"Registry has {active_stores} stores, expected ≤ 3"
    
    # Verify that the first (oldest) entries were evicted
    assert "agent-0" not in registry._stores, "Oldest entry (agent-0) should have been evicted"
    assert "agent-1" not in registry._stores, "Oldest entry (agent-1) should have been evicted"
    # agent-2 should remain since we only evict when exceeding max_size of 3
    # When agent-3 is created, agent-0 is evicted
    # When agent-4 is created, agent-1 is evicted
    # So agent-2, agent-3, and agent-4 should remain
    assert "agent-2" in registry._stores
    assert "agent-3" in registry._stores
    assert "agent-4" in registry._stores
    
    await registry.close_all()