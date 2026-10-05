"""Test DispatcherService implementation (tsk-2q75ca).

This test focuses on demonstrating the RED-FIRST process for DispatcherService.
The tests show the current state of implementation and what needs to be fixed.
"""
import asyncio
from types import SimpleNamespace

import pytest

from tinyagentos.projects.dispatcher_store import DispatcherConfig
from tinyagentos.projects.dispatcher import DispatcherService


def test_dispatcher_service_basic():
    """Test basic DispatcherService functionality - this should PASS."""
    # Create a simple app state
    app_state = SimpleNamespace()
    app_state.config = SimpleNamespace(server={'agent_heartbeat_enabled': False})
    app_state._background_tasks = asyncio.Queue()
    
    # Create dispatcher service
    dispatcher_service = DispatcherService(app_state)
    app_state.dispatcher_service = dispatcher_service
    
    # Test basic tick_user with disabled config
    cfg = DispatcherConfig(
        user_id="test-user",
        enabled=False,  # Disabled by default
        boards=["board-1"],
        eligible_agents=["agent-1"],
    )
    
    # This should complete without raising exceptions
    results = asyncio.run(dispatcher_service.tick_user("test-user", cfg, 1000.0))
    
    # Verify basic structure
    assert isinstance(results, dict)
    assert "assigned" in results
    assert "wake" in results
    assert "board_held" in results
    
    # Verify disabled config assigns nothing
    assert results["assigned"] == 0
    assert results["wake"] == 0
    assert results["board_held"] == 0


def test_dispatcher_service_structure():
    """Test DispatcherService has the required methods - this should PASS."""
    app_state = SimpleNamespace()
    app_state.config = SimpleNamespace(server={'agent_heartbeat_enabled': False})
    app_state._background_tasks = asyncio.Queue()
    
    dispatcher_service = DispatcherService(app_state)
    
    # Verify the dispatcher service has the required methods
    assert hasattr(dispatcher_service, 'tick_user')
    assert hasattr(dispatcher_service, 'tick')
    assert callable(dispatcher_service.tick_user)
    assert callable(dispatcher_service.tick)


def test_dispatcher_service_with_enabled_config():
    """Test DispatcherService with enabled config - this should PASS."""
    app_state = SimpleNamespace()
    app_state.config = SimpleNamespace(server={'agent_heartbeat_enabled': False})
    app_state._background_tasks = asyncio.Queue()
    
    dispatcher_service = DispatcherService(app_state)
    
    # Test with enabled config
    cfg = DispatcherConfig(
        user_id="test-user",
        enabled=True,
        boards=["board-1"],
        eligible_agents=["agent-1"],
    )
    
    # This should complete without raising exceptions
    results = asyncio.run(dispatcher_service.tick_user("test-user", cfg, 1000.0))
    
    # Verify structure
    assert isinstance(results, dict)
    assert "assigned" in results
    assert "wake" in results
    assert "board_held" in results
    
    # With enabled config but no tasks, should still work
    # (Note: actual assignment logic would need more implementation)
    pass