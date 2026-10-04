### Fixed
- Agent registry now refreshes stale container IPs from Incus at controller startup and on connect failure, so agents that moved bridges or projects keep working.
- Host refresh now persists the rewritten IP to config.yaml so the log line fires once instead of repeating on every boot.
- `_refresh_agent_host_from_incus` is now total: any exception during lookup returns None instead of aborting the boot resume pass.
- Connect-failure retry in `_prepare_agent` now only triggers on `httpx.ConnectError`, not on read timeouts after the agent already accepted the request.
- Resume retry loops now break instead of re-POSTing to the same dead host when the Incus lookup yields no new IP.
- `refresh_all_agent_hosts` skips remote agents at boot, avoiding unnecessary Incus calls for agents that can never resolve via container runtime.
