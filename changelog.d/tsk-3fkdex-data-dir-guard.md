### Fixed

- Fixed data/ guard: `tinyagentos/cli/worker.py:162` now passes the resolved `data_dir` parameter to `_load_agents_json()` instead of using the default hardcoded path `data/agents.json`. This prevents tests from writing to the repository's data directory.