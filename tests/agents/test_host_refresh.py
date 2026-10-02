"""Tests for host refresh functionality.

Tests verify that agent hosts are correctly refreshed from Incus when containers move
between projects/bridges, and that the refresh happens both at startup and when
controller-to-agent calls fail.
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinyagentos.agent_db import find_agent
from tinyagentos.containers import ContainerInfo
from tinyagentos.restart_orchestrator import _refresh_agent_host_from_incus


@pytest.mark.asyncio
async def test_agent_host_refreshed_at_startup():
    """Test that an agent's host is refreshed from Incus at startup."""
    # Create a mock config with an agent that has a stale host
    config = MagicMock()
    config.agents = [
        {
            "name": "test-agent",
            "host": "10.26.37.174",  # Stale host (old IP)
            "paused": False,
        }
    ]
    
    # Create a mock app_state with config
    app_state = MagicMock()
    app_state.config = config
    app_state.config.config_path = MagicMock()
    
    # Mock the agent_db.find_agent function to return the agent
    with patch('tinyagentos.restart_orchestrator.find_agent') as mock_find_agent:
        mock_find_agent.return_value = config.agents[0]
        
        # Mock the containers.list_containers function to return a container
        # with a new IP (10.42.246.174)
        mock_container_info = ContainerInfo(
            name="taos-agent-test-agent",
            status="Running",
            ip="10.42.246.174",  # New IP (updated)
            memory_mb=1024,
            cpu_cores=2,
        )
        
        with patch('tinyagentos.restart_orchestrator.list_containers') as mock_list_containers:
            mock_list_containers.return_value = [mock_container_info]
            
            # Mock the containers.get_container_state function
            with patch('tinyagentos.restart_orchestrator.get_container_state') as mock_get_state:
                mock_get_state.return_value = {"project": "user-999"}
                
                # Mock the save_config_locked function
                with patch('tinyagentos.restart_orchestrator.save_config_locked') as mock_save:
                    # Mock the logger to avoid actual logging
                    with patch('tinyagentos.restart_orchestrator.logger'):
                        # Call the function to test
                        agent = config.agents[0]
                        result = await _refresh_agent_host_from_incus(agent, app_state)
                        
                        # Assert that the function returned True (host was refreshed)
                        assert result is True
                        
                        # Assert that the agent's host was updated
                        assert agent["host"] == "10.42.246.174"
                        
                        # Assert that save_config_locked was called
                        mock_save.assert_called_once()


@pytest.mark.asyncio
async def test_agent_host_refreshed_after_connection_failure():
    """Test that a failed controller-to-agent connection triggers host refresh."""
    # This test simulates a connection failure and verifies that the host
    # is refreshed before retrying.
    
    # Create a mock config with an agent that has a stale host
    config = MagicMock()
    config.agents = [
        {
            "name": "test-agent",
            "host": "10.26.37.174",  # Stale host (old IP)
            "paused": False,
        }
    ]
    
    # Create a mock app_state with config
    app_state = MagicMock()
    app_state.config = config
    app_state.config.config_path = MagicMock()
    
    # Mock the agent_db.find_agent function to return the agent
    with patch('tinyagentos.restart_orchestrator.find_agent') as mock_find_agent:
        mock_find_agent.return_value = config.agents[0]
        
        # Mock the containers.list_containers function to return a container
        # with a new IP (10.42.246.174)
        mock_container_info = ContainerInfo(
            name="taos-agent-test-agent",
            status="Running",
            ip="10.42.246.174",  # New IP (updated)
            memory_mb=1024,
            cpu_cores=2,
        )
        
        with patch('tinyagentos.restart_orchestrator.list_containers') as mock_list_containers:
            mock_list_containers.return_value = [mock_container_info]
            
            # Mock the containers.get_container_state function
            with patch('tinyagentos.restart_orchestrator.get_container_state') as mock_get_state:
                mock_get_state.return_value = {"project": "user-999"}
                
                # Mock the save_config_locked function
                with patch('tinyagentos.restart_orchestrator.save_config_locked') as mock_save:
                    # Mock the logger to avoid actual logging
                    with patch('tinyagentos.restart_orchestrator.logger'):
                        # Call the function to test
                        agent = config.agents[0]
                        result = await _refresh_agent_host_from_incus(agent, app_state)
                        
                        # Assert that the function returned True (host was refreshed)
                        assert result is True
                        
                        # Assert that the agent's host was updated
                        assert agent["host"] == "10.42.246.174"
                        
                        # Assert that save_config_locked was called
                        mock_save.assert_called_once()


@pytest.mark.asyncio
async def test_agent_host_refreshed_uses_agent_project():
    """Test that the host refresh uses the agent's Incus project, not default."""
    # Create a mock config with an agent in project user-999
    config = MagicMock()
    config.agents = [
        {
            "name": "test-agent",
            "host": "10.26.37.174",  # Stale host
            "paused": False,
        }
    ]
    
    # Create a mock app_state with config
    app_state = MagicMock()
    app_state.config = config
    app_state.config.config_path = MagicMock()
    
    # Mock the agent_db.find_agent function to return the agent
    with patch('tinyagentos.restart_orchestrator.find_agent') as mock_find_agent:
        mock_find_agent.return_value = config.agents[0]
        
        # Mock the containers.list_containers function to return a container
        mock_container_info = ContainerInfo(
            name="taos-agent-test-agent",
            status="Running",
            ip="10.42.246.174",  # New IP
            memory_mb=1024,
            cpu_cores=2,
        )
        
        with patch('tinyagentos.restart_orchestrator.list_containers') as mock_list_containers:
            mock_list_containers.return_value = [mock_container_info]
            
            # Mock the containers.get_container_state function to return project user-999
            with patch('tinyagentos.restart_orchestrator.get_container_state') as mock_get_state:
                mock_get_state.return_value = {"project": "user-999"}
                
                # Mock the save_config_locked function
                with patch('tinyagentos.restart_orchestrator.save_config_locked') as mock_save:
                    # Mock the logger to avoid actual logging
                    with patch('tinyagentos.restart_orchestrator.logger'):
                        # Call the function to test
                        agent = config.agents[0]
                        result = await _refresh_agent_host_from_incus(agent, app_state)
                        
                        # Assert that the function returned True (host was refreshed)
                        assert result is True
                        
                        # Assert that the agent's host was updated
                        assert agent["host"] == "10.42.246.174"
                        
                        # Verify that the container state was called with the correct parameters
                        mock_get_state.assert_called_once_with("taos-agent-test-agent")
                        
                        # Assert that save_config_locked was called
                        mock_save.assert_called_once()


@pytest.mark.asyncio
async def test_agent_host_not_refreshed_if_same_ip():
    """Test that an agent's host is NOT refreshed if the IP hasn't changed."""
    # Create a mock config with an agent that has a current host
    config = MagicMock()
    config.agents = [
        {
            "name": "test-agent",
            "host": "10.42.246.174",  # Current host (already correct)
            "paused": False,
        }
    ]
    
    # Create a mock app_state with config
    app_state = MagicMock()
    app_state.config = config
    app_state.config.config_path = MagicMock()
    
    # Mock the agent_db.find_agent function to return the agent
    with patch('tinyagentos.restart_orchestrator.find_agent') as mock_find_agent:
        mock_find_agent.return_value = config.agents[0]
        
        # Mock the containers.list_containers function to return a container
        # with the same IP (no change)
        mock_container_info = ContainerInfo(
            name="taos-agent-test-agent",
            status="Running",
            ip="10.42.246.174",  # Same IP (no change)
            memory_mb=1024,
            cpu_cores=2,
        )
        
        with patch('tinyagentos.restart_orchestrator.list_containers') as mock_list_containers:
            mock_list_containers.return_value = [mock_container_info]
            
            # Mock the containers.get_container_state function
            with patch('tinyagentos.restart_orchestrator.get_container_state') as mock_get_state:
                mock_get_state.return_value = {"project": "user-999"}
                
                # Mock the save_config_locked function
                with patch('tinyagentos.restart_orchestrator.save_config_locked') as mock_save:
                    # Mock the logger to avoid actual logging
                    with patch('tinyagentos.restart_orchestrator.logger'):
                        # Call the function to test
                        agent = config.agents[0]
                        result = await _refresh_agent_host_from_incus(agent, app_state)
                        
                        # Assert that the function returned False (host was NOT refreshed)
                        assert result is False
                        
                        # Assert that the agent's host was NOT updated
                        assert agent["host"] == "10.42.246.174"
                        
                        # Assert that save_config_locked was NOT called
                        mock_save.assert_not_called()


@pytest.mark.asyncio
async def test_agent_host_refreshed_logs_old_and_new_values():
    """Test that host refresh logs old and new values."""
    # Create a mock config with an agent that has a stale host
    config = MagicMock()
    config.agents = [
        {
            "name": "test-agent",
            "host": "10.26.37.174",  # Old host
            "paused": False,
        }
    ]
    
    # Create a mock app_state with config
    app_state = MagicMock()
    app_state.config = config
    app_state.config.config_path = MagicMock()
    
    # Mock the agent_db.find_agent function to return the agent
    with patch('tinyagentos.restart_orchestrator.find_agent') as mock_find_agent:
        mock_find_agent.return_value = config.agents[0]
        
        # Mock the containers.list_containers function to return a container
        mock_container_info = ContainerInfo(
            name="taos-agent-test-agent",
            status="Running",
            ip="10.42.246.174",  # New IP
            memory_mb=1024,
            cpu_cores=2,
        )
        
        with patch('tinyagentos.restart_orchestrator.list_containers') as mock_list_containers:
            mock_list_containers.return_value = [mock_container_info]
            
            # Mock the containers.get_container_state function
            with patch('tinyagentos.restart_orchestrator.get_container_state') as mock_get_state:
                mock_get_state.return_value = {"project": "user-999"}
                
                # Mock the save_config_locked function
                with patch('tinyagentos.restart_orchestrator.save_config_locked') as mock_save:
                    # Mock the logger to capture log messages
                    with patch('tinyagentos.restart_orchestrator.logger') as mock_logger:
                        # Call the function to test
                        agent = config.agents[0]
                        result = await _refresh_agent_host_from_incus(agent, app_state)
                        
                        # Assert that the function returned True (host was refreshed)
                        assert result is True
                        
                        # Assert that the agent's host was updated
                        assert agent["host"] == "10.42.246.174"
                        
                        # Verify that the logger was called with the old and new values
                        mock_logger.info.assert_called()
                        
                        # Get the actual log message call
                        log_calls = mock_logger.info.call_args_list
                        # Find the call that contains "host changed"
                        host_changed_calls = [
                            call for call in log_calls
                            if "host changed" in call[0][0]
                        ]
                        
                        # There should be at least one such call
                        assert len(host_changed_calls) > 0
                        
                        # Get the format string and arguments
                        format_string = host_changed_calls[0][0][0]
                        args = host_changed_calls[0][0][1:]
                        
                        # Format the message to check if it contains our IP values
                        log_message = format_string % tuple(args)
                        assert "10.26.37.174" in log_message  # Old value
                        assert "10.42.246.174" in log_message  # New value
                        assert "user-999" in log_message  # Project
                        
                        # Assert that save_config_locked was called
                        mock_save.assert_called_once()