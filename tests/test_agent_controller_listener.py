from pathlib import Path

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tinyagentos.deployer import DeployRequest, deploy_agent, _gateway_port_for


@pytest.mark.asyncio
async def test_taos_proxy_taos_connects_to_wrong_port(tmp_path, monkeypatch):
    """RED proof: taos-proxy-taos must NOT connect to the main port.
    
    Before fix: the device connects to req.taos_port (6969), making agent requests
    appear as loopback to the controller, causing auth checks to mistake them for
    console requests.
    
    After fix: the device should connect to a dedicated agent controller listener
    port (6972) which replaces the scope["client"] with a non-loopback address
    (192.0.2.1) before reaching the main app.
    """
    # Setup minimal deploy request
    req = DeployRequest(
        name="test",
        framework="smolagents", 
        model=None,
        data_dir=tmp_path,
        taos_port=6969,  # Main controller port
    )
    
    # Mock proxy devices
    async def mock_exec(name, cmd, **kwargs):
        if "hostname -I" in " ".join(cmd):
            return (0, "10.0.0.5")
        return (0, "ok")
    
    # Track what ports the taos-proxy-taos device connects to
    captured_connect_ports = []
    
    def capture_add_proxy_device(*args, **kwargs):
        if "taos-proxy-taos" in args or (len(args) > 1 and args[1] == "taos-proxy-taos"):
            # Extract the connect port from kwargs
            connect = kwargs.get("connect", "")
            if "connect=" in connect:
                port = connect.split("connect=")[-1].split(":")[-1]
                captured_connect_ports.append(int(port))
        return {"success": True, "output": ""}
    
    with patch("tinyagentos.deployer.create_container", new_callable=AsyncMock) as mock_create, \
         patch("tinyagentos.deployer.exec_in_container", side_effect=mock_exec), \
         patch("tinyagentos.deployer.push_file", new_callable=AsyncMock, return_value=(0, "")), \
         patch("tinyagentos.deployer.add_proxy_device", side_effect=capture_add_proxy_device):
        
        mock_create.return_value = {"success": True, "name": "taos-agent-test"}
        
        # This should fail or show the wrong port in RED proof
        result = await deploy_agent(req)
        
        # The bug: captured_connect_ports should NOT contain 6969 (main port)
        # It should contain 6972 (agent controller port) after fix
        print(f"Captured connect ports: {captured_connect_ports}")
        
        # RED proof assertion (should FAIL before fix)
        assert 6969 not in captured_connect_ports, \
            f"BUG: taos-proxy-taos incorrectly connects to main port 6969. " \
            "Agents should use a dedicated agent controller listener port (e.g., 6972)."