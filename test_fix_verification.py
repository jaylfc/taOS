#!/usr/bin/env python3
"""Quick verification script for the fix."""

import sys
import os
from pathlib import Path

# Add the current directory to the path
sys.path.insert(0, str(Path(__file__).parent))

# Mock the external dependencies for testing
import unittest.mock as mock

def test_ensure_agent_llm_key_with_mock_proxy():
    """Test that ensure_agent_llm_key works correctly with a mock proxy."""
    from tinyagentos.agent_keys import ensure_agent_llm_key
    
    # Mock config
    mock_config = mock.MagicMock()
    mock_config.config_path = Path("/tmp/config.yaml")
    
    # Mock agent dict
    agent_dict = {"name": "test-agent", "permitted_models": ["model1"], "model": "model1"}
    
    # Mock proxy that is running
    mock_proxy = mock.MagicMock()
    mock_proxy.is_running.return_value = True
    mock_proxy.create_agent_key = mock.AsyncMock(return_value="sk-minted-key")
    
    # Test 1: Proxy is running and mints a key
    print("Test 1: Proxy running and minting key...")
    async def run_test1():
        result, reason = await ensure_agent_llm_key(
            config=mock_config,
            agent_dict=agent_dict,
            proxy=mock_proxy,
            data_dir=Path("/tmp/data"),
        )
        assert result == "sk-minted-key", f"Expected 'sk-minted-key', got {result}"
        assert reason is None, f"Expected None reason, got {reason}"
        assert agent_dict.get("llm_key") == "sk-minted-key", "Agent dict should have llm_key set"
        print("✓ Test 1 passed: Key minted and persisted")
    
    import asyncio
    asyncio.run(run_test1())
    
    # Test 2: Proxy is running but key mint fails with DB configured
    print("\nTest 2: Proxy running, DB configured, key mint fails...")
    async def run_test2():
        mock_proxy.create_agent_key.reset_mock()
        mock_proxy.create_agent_key.return_value = None
        mock_proxy.database_url = "postgresql://user:pass@localhost:5432/db"
        
        result, reason = await ensure_agent_llm_key(
            config=mock_config,
            agent_dict=agent_dict,
            proxy=mock_proxy,
            data_dir=Path("/tmp/data"),
        )
        assert result is None, f"Expected None result, got {result}"
        assert "DB configured" in reason, f"Expected error about DB, got: {reason}"
        print("✓ Test 2 passed: Refusal with DB error")
    
    asyncio.run(run_test2())
    
    # Test 3: Proxy not running, agent has existing key
    print("\nTest 3: Proxy not running, agent has existing key...")
    async def run_test3():
        mock_proxy.is_running.return_value = False
        existing_key = "sk-existing-key"
        
        result, reason = await ensure_agent_llm_key(
            config=mock_config,
            agent_dict=agent_dict,
            proxy=mock_proxy,
            data_dir=Path("/tmp/data"),
            existing_llm_key=existing_key,
        )
        # Should fall back to master key logic when existing key is present but proxy not running
        assert result is not None, "Expected fallback to master key"
        assert "proxy not running" not in reason.lower() if reason else True, "Should not return proxy-not-running error when existing key present"
        print("✓ Test 3 passed: Fallback to master key when existing key present")
    
    asyncio.run(run_test3())
    
    print("\n✅ All tests passed! The fix appears to be working correctly.")

if __name__ == "__main__":
    test_ensure_agent_llm_key_with_mock_proxy()
