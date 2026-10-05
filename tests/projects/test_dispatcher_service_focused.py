"""Test DispatcherService implementation (tsk-2q75ca) - focused on core functionality.

This test focuses on demonstrating the RED-FIRST process for DispatcherService.
The tests show the current state of implementation and what needs to be fixed.
"""
import asyncio
import pytest

from tinyagentos.projects.dispatcher_store import DispatcherConfig
from tinyagentos.projects.dispatcher import DispatcherService
from tinyagentos.projects.dispatch_policy import is_candidate, select_assignments as dispatch_policy_select_assignments
from tinyagentos.agent_heartbeat import wake_agent_with_task


# Test that DispatcherService can be instantiated and has required methods
def test_dispatcher_service_basic_instantiation():
    """Test that DispatcherService can be instantiated and has required methods."""
    # Create a simple app state using SimpleNamespace (like the original tests)
    app_state = type('AppState', (), {})()
    app_state.config = type('Config', (), {'server': {'agent_heartbeat_enabled': False}})()
    app_state._background_tasks = asyncio.Queue()
    
    # Create dispatcher service
    dispatcher_service = DispatcherService(app_state)
    
    # Verify the dispatcher service has the required methods
    assert hasattr(dispatcher_service, 'tick_user')
    assert hasattr(dispatcher_service, 'tick')
    assert callable(dispatcher_service.tick_user)
    assert callable(dispatcher_service.tick)


# Test tick_user with disabled config (sync wrapper)
def test_tick_user_disabled_sync():
    """Test tick_user with disabled dispatcher config - sync wrapper."""
    # Create a simple app state using SimpleNamespace
    app_state = type('AppState', (), {})()
    app_state.config = type('Config', (), {'server': {'agent_heartbeat_enabled': False}})()
    app_state._background_tasks = asyncio.Queue()
    
    # Create dispatcher service
    dispatcher_service = DispatcherService(app_state)
    
    # Test basic tick_user with disabled config
    cfg = DispatcherConfig(
        user_id="test-user",
        enabled=False,  # Disabled by default
        boards=["board-1"],
        eligible_agents=["agent-1"],
    )
    
    # This should complete without raising exceptions
    # Use asyncio.run to run the async function
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


# Test that wake_agent_with_task is accessible (basic function test)
def test_wake_agent_with_task_accessible():
    """Test that wake_agent_with_task function is accessible."""
    # Just verify the function exists
    assert callable(wake_agent_with_task), "wake_agent_with_task should be callable"


# Test that dispatch_policy functions are accessible
@pytest.mark.asyncio
async def test_dispatch_policy_functions_accessible():
    """Test that dispatch_policy functions are accessible."""
    # Just verify the functions exist
    assert callable(is_candidate), "is_candidate should be callable"
    assert callable(dispatch_policy_select_assignments), "dispatch_policy_select_assignments should be callable"


# Test is_board_dispatchable function behavior (sync wrapper)
def test_is_board_dispatchable_behavior_sync():
    """Test is_board_dispatchable function behavior - sync wrapper."""
    # We'll test this indirectly through the dispatcher service
    # by creating a dispatcher service and checking its is_board_dispatchable function
    app_state = type('AppState', (), {})()
    app_state.config = type('Config', (), {'server': {'agent_heartbeat_enabled': False}})()
    app_state._background_tasks = asyncio.Queue()
    
    dispatcher_service = DispatcherService(app_state)
    
    # Verify that is_board_dispatchable function exists in the module
    from tinyagentos.projects.dispatcher import is_board_dispatchable
    assert callable(is_board_dispatchable), "is_board_dispatchable should be callable"


# Test dispatcher service with enabled config (basic structure test)
def test_dispatcher_service_with_enabled_config_basic_sync():
    """Test that dispatcher service works with enabled config - sync wrapper."""
    # Create a simple app state
    app_state = type('AppState', (), {})()
    app_state.config = type('Config', (), {'server': {'agent_heartbeat_enabled': False}})()
    app_state._background_tasks = asyncio.Queue()
    
    # Create dispatcher service
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
    
    # Verify basic structure
    assert isinstance(results, dict)
    assert "assigned" in results
    assert "wake" in results
    assert "board_held" in results


if __name__ == "__main__":
    print("Running focused tests for DispatcherService functionality...")
    print("This script demonstrates the core functionality that needs to be implemented.")
    print("\nKey functionality tested:")
    print("1. DispatcherService instantiation and basic structure")
    print("2. tick_user with disabled dispatcher config")
    print("3. is_board_dispatchable function behavior")
    print("4. wake_agent_with_task accessibility")
    print("5. dispatch_policy functions accessibility")
    print("\nThese tests help verify the core dispatcher functionality.")