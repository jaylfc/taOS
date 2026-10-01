### Fixed

- `_convert_to_lxc` now loads `config.yaml` and builds `LLMProxy` before draining or deleting any flat-mode agents. A bad `config.yaml` (e.g. invalid YAML) now prints a clear error and exits non-zero without touching agents.
- `_get_verified_gateway_port` now distinguishes auth failures from reachability failures. A 401/403 from the local controller prints a token-rejected message (`set TAOS_TOKEN or run taosctl login`) instead of the generic `cannot reach local controller` message; transport errors still show the reachability hint.
