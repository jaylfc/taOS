### Fixed

- Fixed the agents deploy endpoint test-data pollution: `tinyagentos/routes/agents.py` now passes the resolved `data_dir` to the taosmd agent registry instead of relying on its process-global default registry, so agent registration writes land in the app's data directory (tmp_path in tests) and no longer mutate the repository's `data/agents.json` under the xdist data-dir mutation guard.
