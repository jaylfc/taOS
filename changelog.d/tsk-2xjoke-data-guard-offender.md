### Fixed

- Fixed agents deployment endpoint test-data pollution: agent registration no longer mutates the repository's `data/agents.json` under the xdist data-dir mutation guard (CI: shards (3.13, 1) on head 5dde82a79).
