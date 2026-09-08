"""RED test for undeploy_agent trace directory removal.

This test demonstrates that undeploy_agent's docstring says it removes
the agent's trace directory when delete_state=True, but the code never did.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tinyagentos.deployer import undeploy_agent


@pytest.mark.asyncio
async def test_undeploy_agent_removes_trace_dir(tmp_path: Path) -> None:
    """RED test: undeploy_agent must remove trace directory when delete_state=True.
    
    The current implementation's docstring claims trace directory is removed,
    but the code only removes workspaces and memory, not the trace directory.
    This test will FAIL on the current implementation.
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    
    agent_name = "test-agent"
    trace_dir = data_dir / "trace" / agent_name
    workspaces_dir = data_dir / "agent-workspaces" / agent_name
    memory_dir = data_dir / "agent-memory" / agent_name
    
    # Create the directories that simulate post-deploy state
    trace_dir.mkdir(parents=True)
    (trace_dir / "otel-spans.db").write_text("test data")
    workspaces_dir.mkdir(parents=True)
    (workspaces_dir / "workspace").write_text("test")
    memory_dir.mkdir(parents=True)
    (memory_dir / "memory").write_text("test")
    
    # Verify directories exist before undeploy
    assert trace_dir.exists()
    assert workspaces_dir.exists()
    assert memory_dir.exists()
    
    # Mock destroy_container to simulate successful undeploy
    import asyncio
    from unittest.mock import AsyncMock, patch
    
    with patch("tinyagentos.deployer.destroy_container") as mock_destroy:
        mock_destroy.return_value = {"success": True}
        
        # Call undeploy with delete_state=True
        result = await undeploy_agent(
            agent_name,
            data_dir=data_dir,
            delete_state=True
        )
        
        # Verify undeploy succeeded
        assert result["success"] is True
        assert result["name"] == agent_name
        
        # RED TEST EXPECTATION: Trace directory should be removed
        # But current implementation will FAIL because it doesn't remove trace_dir
        assert not trace_dir.exists(), f"Trace directory {trace_dir} should have been removed"
        assert not workspaces_dir.exists(), "Workspaces directory should have been removed"
        assert not memory_dir.exists(), "Memory directory should have been removed"
        
        # Verify destroy_container was called
        mock_destroy.assert_called_once_with(f"taos-agent-{agent_name}")