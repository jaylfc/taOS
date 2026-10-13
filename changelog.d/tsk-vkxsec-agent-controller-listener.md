### Fixed

- Agent containers now reach the controller through a dedicated agent controller listener instead of the main controller port. This prevents agent requests from being mistaken for local console requests by auth checks.

- The agent controller listener uses RFC 5737 TEST-NET-1 address (192.0.2.1) for client requests, ensuring they are never parsed as loopback.

- The `taos-proxy-taos` incus proxy device now connects to the agent controller listener port (6972) instead of the main controller port (6969), and the startup reconcile moves existing agents from the main port to the agent controller port.

- Added new agent controller listener module (`tinyagentos/agent_controller_listener.py`) that wraps the main app and replaces client addresses before forwarding requests.

- Updated the controller startup to include the agent controller listener as a loopback-only sidecar.

- Added agent controller cutover logic to move existing agents from the main port to the agent controller port during startup.

This security fix ensures that agent requests cannot be intercepted or mistaken for console requests, addressing the vulnerability where agents were treated as the local console by authentication and middleware checks.