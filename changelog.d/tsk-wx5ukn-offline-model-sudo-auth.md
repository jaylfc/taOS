### Fixed

- Offline model: `PUT /api/system/offline-model` now requires a signed-in admin session (the host local token and agent tokens are refused), runs start/stop through passwordless `sudo -n` while status stays sudo-free, maps start exit 1 to 502 with the helper's message and logs other non-zero starts, surfaces stop errors, and persists the enable preference only on success. The LLM gateway routing table regression (`routing_table` awaited at every caller, including `llm_gateway/resolve.py`) is fixed so model resolution works again.
