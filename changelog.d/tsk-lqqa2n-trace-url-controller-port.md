### Fixed
- `TAOS_TRACE_URL` now targets the taOS controller port (resolved via `TAOS_PORT` env var, `config.server['port']`, or default 6969) rather than the LiteLLM proxy port, so trace POSTs from the LiteLLM callback always reach `/api/trace` on the controller regardless of the proxy's bound port.
